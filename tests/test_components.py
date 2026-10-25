from __future__ import annotations

import hashlib
import http.server
import json
import tempfile
import threading
import time
import unittest
import zipfile
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import components, config
from app.engines import rvc


def make_rvc_tree(root: Path) -> Path:
    app_root = root / "RVC-fixture"
    (app_root / "configs").mkdir(parents=True)
    (app_root / "assets").mkdir()
    (app_root / "webui.py").write_text("# stub", encoding="utf-8")
    (app_root / "configs" / "config.py").write_text("# stub", encoding="utf-8")
    return app_root


def make_archive(source: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in source.rglob("*"):
            bundle.write(path, source.parent.name + "/" + str(path.relative_to(source.parent)))


class RvcDetectTest(unittest.TestCase):
    def test_tolerant_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "RVC20240604Nvidia"
            (root / "configs").mkdir(parents=True)
            (root / "configs" / "config.py").write_text("# stub", encoding="utf-8")
            (root / "go-webui.bat").write_text("@echo off", encoding="utf-8")
            self.assertTrue(rvc.is_rvc_dir(root))
            self.assertEqual(rvc.locate_rvc(root), root)

    def test_rejects_gpt_sovits_like_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "GPT-SoVITS-v4"
            (root / "runtime").mkdir(parents=True)
            (root / "webui.py").write_text("# stub", encoding="utf-8")
            (root / "go-webui.bat").write_text("@echo off", encoding="utf-8")
            self.assertFalse(rvc.is_rvc_dir(root))

    def test_locate_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "AI"
            make_rvc_tree(parent / "nested")
            located = rvc.locate_rvc(parent)
            self.assertIsNotNone(located)
            self.assertEqual(located.name, "RVC-fixture")

    def test_diagnose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            missing = rvc.diagnose(empty)
            self.assertTrue(any("webui" in item for item in missing))
            self.assertTrue(any("configs" in item for item in missing))

    def test_locate_rejects_unrelated_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "something"
            folder.mkdir()
            (folder / "notes.txt").write_text("x", encoding="utf-8")
            self.assertIsNone(rvc.locate_rvc(folder))

    def test_candidates_include_app_components_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            fixture = make_rvc_tree(data / "components")
            cfg = SimpleNamespace(data_dir=str(data), components_dir="")
            with mock.patch.object(config, "load_config", return_value=cfg):
                candidates = rvc._candidates(None)
        self.assertIn(data / "components", candidates)
        self.assertIn(fixture.parent, candidates)


class ComponentInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_config = config.CONFIG_PATH
        config.CONFIG_PATH = self.root / "config.json"

        self.source = self.root / "source"
        fixture = make_rvc_tree(self.source / "content")
        self.archive = self.root / "downloads" / "RVC-fixture.zip"
        self.archive.parent.mkdir(parents=True)
        make_archive(fixture, self.archive)
        self.payload = self.archive.read_bytes()
        self.sha256 = hashlib.sha256(self.payload).hexdigest()

        self.serve_dir = self.root / "serve"
        self.serve_dir.mkdir()
        (self.serve_dir / "RVC-fixture.zip").write_bytes(self.payload)
        class QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args, **kwargs):
                pass

        handler = partial(QuietHandler, directory=str(self.serve_dir))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.port}/RVC-fixture.zip"

        self.item = {
            "id": "fixture-rvc",
            "name": "RVC fixture",
            "version": "test",
            "filename": "RVC-fixture.zip",
            "size": len(self.payload),
            "sha256": self.sha256,
            "backend": "cuda",
            "backend_label": "CUDA",
            "recommend": "test",
            "note": "test",
            "homepage": "",
        }
        components._BY_ID[self.item["id"]] = self.item
        self.manager = components.Manager()

    def tearDown(self) -> None:
        components._BY_ID.pop("fixture-rvc", None)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        config.CONFIG_PATH = self.old_config
        self.tmp.cleanup()

    def _start(self, keep_archive: bool = False) -> dict:
        with mock.patch.object(components, "download_urls", return_value=[self.url]):
            self.manager.start(
                self.item["id"],
                target=str(self.root / "components"),
                source="official",
                keep_archive=keep_archive,
            )
            deadline = time.time() + 30
            while time.time() < deadline:
                snapshot = self.manager.snapshot()
                if snapshot and not snapshot["active"]:
                    return snapshot
                time.sleep(0.1)
        self.fail("组件任务超时")

    def test_download_verify_extract_activate(self) -> None:
        result = self._start()
        self.assertEqual(result["state"], "done", result)
        rvc_path = Path(result["rvc_path"])
        self.assertTrue((rvc_path / "webui.py").is_file())
        self.assertEqual(config.load_config().rvc_path, str(rvc_path))
        self.assertFalse(
            (self.root / "components" / self.item["id"] / self.item["filename"]).exists()
        )
        self.assertTrue(rvc.detect_rvc(rvc_path).available)

    def test_keep_archive(self) -> None:
        result = self._start(keep_archive=True)
        self.assertEqual(result["state"], "done", result)
        archive = self.root / "components" / self.item["id"] / self.item["filename"]
        self.assertTrue(archive.is_file())
        self.assertEqual(archive.stat().st_size, len(self.payload))

    def test_hash_mismatch_reports_error(self) -> None:
        self.item["sha256"] = "0" * 64
        result = self._start()
        self.assertEqual(result["state"], "error")
        self.assertIn("校验", result["error"])
        part = self.root / "components" / self.item["id"] / (self.item["filename"] + ".part")
        self.assertFalse(part.exists())

    def test_reuses_verified_archive(self) -> None:
        target = self.root / "components" / self.item["id"]
        target.mkdir(parents=True)
        (target / self.item["filename"]).write_bytes(self.payload)
        with mock.patch.object(components, "download_urls", return_value=[]):
            self.manager.start(
                self.item["id"],
                target=str(self.root / "components"),
                source="official",
            )
            deadline = time.time() + 30
            while time.time() < deadline:
                snapshot = self.manager.snapshot()
                if snapshot and not snapshot["active"]:
                    break
                time.sleep(0.1)
        self.assertEqual(snapshot["state"], "done", snapshot)
        self.assertTrue((Path(snapshot["rvc_path"]) / "webui.py").is_file())

    def test_public_catalog_shape(self) -> None:
        catalog = components.public_catalog()
        ids = {item["id"] for item in catalog}
        self.assertIn("rvc-nvidia", ids)
        self.assertIn("rvc-amd", ids)
        for item in catalog:
            self.assertGreater(item["size"], 0)
            self.assertEqual(len(item["sha256"]), 64)
            self.assertTrue(item["homepage"].startswith("https://"))

    def test_required_bytes(self) -> None:
        item = components.component("rvc-nvidia")
        self.assertGreater(components.required_bytes(item), item["size"] * 2)

    def test_uninstall_removes_managed_runtime(self) -> None:
        result = self._start()
        self.assertEqual(result["state"], "done", result)
        project = self.root / "voice_projects" / "p1.wav"
        project.parent.mkdir(parents=True, exist_ok=True)
        project.write_bytes(b"project")
        folder = self.root / "components" / self.item["id"]
        self.assertTrue(folder.is_dir())

        removed = components.uninstall(self.item["id"])

        self.assertEqual(removed["kind"], "rvc")
        self.assertFalse(folder.exists())
        self.assertEqual(config.load_config().rvc_path, "")
        self.assertTrue(project.is_file())

    def test_uninstall_refuses_manual_runtime(self) -> None:
        external = make_rvc_tree(self.root / "external")
        config.update_config(
            rvc_path=str(external),
            data_dir=str(self.root / "data"),
            components_dir="",
        )
        with self.assertRaises(components.ComponentError):
            components.uninstall("rvc")
        self.assertTrue((external / "webui.py").is_file())

    def test_preflight_reports_space_and_slim(self) -> None:
        item = components.component("fixture-rvc")
        report = components.preflight(
            self.item["id"], target=str(self.root / "components"), slim=True
        )
        self.assertEqual(report["download_bytes"], item["size"])
        self.assertGreater(report["required_bytes"], item["size"])
        self.assertGreater(report["free_bytes"], 0)
        self.assertFalse(report["slim"])

        gpt = {
            "id": "fixture-gpt",
            "name": "GPT fixture",
            "version": "test",
            "filename": "fixture.7z",
            "size": 1024,
            "sha256": "0" * 64,
            "backend": "cuda",
            "backend_label": "CUDA",
            "recommend": "test",
            "note": "test",
            "homepage": "",
            "kind": "gpt_sovits",
        }
        components._BY_ID[gpt["id"]] = gpt
        try:
            slim = components.preflight(
                gpt["id"], target=str(self.root / "components"), slim=True
            )
            self.assertTrue(slim["slim"])
            self.assertGreater(slim["slim_estimate_bytes"], 0)
        finally:
            components._BY_ID.pop(gpt["id"], None)

    def test_prune_slim_removes_only_asr_and_uvr5(self) -> None:
        root = self.root / "gpt-runtime"
        for relative, size in (
            ("tools/asr/model.bin", 100),
            ("tools/uvr5/weights.bin", 50),
            ("tools/other.bin", 25),
            ("GPT_SoVITS/model.bin", 10),
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)

        saved = components._prune_slim(root)

        self.assertEqual(saved, 150)
        self.assertFalse((root / "tools/asr").exists())
        self.assertFalse((root / "tools/uvr5").exists())
        self.assertTrue((root / "tools/other.bin").is_file())
        self.assertTrue((root / "GPT_SoVITS/model.bin").is_file())


if __name__ == "__main__":
    unittest.main()
