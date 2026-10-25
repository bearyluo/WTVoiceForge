from __future__ import annotations

import io
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from app import catalog, config, packs, server, voice
from app.engines import gpt_sovits
from tests.test_voice import make_tone, make_wav, wav_peak
from app.server import create_server

BANK_GUID = "d5bab5d0-c653-4e3b-a5f0-e5711c067d7d"
EVENT_GUID = "368f54fe-9eb0-45f4-a5dc-7b9faa5dd50b"
AUDIO_GUID = "97dcca65-10a2-4077-a36e-a7ac25cf8cc8"


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls._old_config_path = config.CONFIG_PATH
        config.CONFIG_PATH = cls.root / "config.json"
        config.update_config(data_dir=str(cls.root / "data"))
        catalog.store.clear()

        cls.game = cls.root / "War Thunder"
        (cls.game / "win64").mkdir(parents=True)
        (cls.game / "win64" / "aces.exe").write_bytes(b"stub")
        (cls.game / "sound").mkdir()
        (cls.game / "sound" / "aircraft_gui.assets.bank").write_bytes(b"bank")
        (cls.game / "sound" / "aircraft_gui.bank").write_bytes(b"meta")
        (cls.game / "config.blk").write_text(
            "sound{\n  fmod_sound_enable:b=yes\n  enable_mod:b=no\n}\n", encoding="utf-8"
        )

        cls.kit = cls.root / "kit"
        (cls.kit / "Assets" / "gui").mkdir(parents=True)
        (cls.kit / "Assets" / "gui" / "enemy_destroyed.wav").write_bytes(b"OggS" + b"0" * 256)
        (cls.kit / "sounds.fspro").write_text("{}", encoding="utf-8")
        bank_dir = cls.kit / "Metadata" / "Bank"
        event_dir = cls.kit / "Metadata" / "Event"
        audio_dir = cls.kit / "Metadata" / "AudioFile"
        bank_dir.mkdir(parents=True)
        event_dir.mkdir(parents=True)
        audio_dir.mkdir(parents=True)
        (bank_dir / f"{{{BANK_GUID}}}.xml").write_text(
            f'<objects><object class="Bank" id="{{{BANK_GUID}}}">'
            f'<property name="name"><value>aircraft_gui</value></property></object></objects>',
            encoding="utf-8",
        )
        (event_dir / f"{{{EVENT_GUID}}}.xml").write_text(
            f'<objects><object class="Event" id="{{{EVENT_GUID}}}">'
            f'<property name="name"><value>enemy_destroyed</value></property>'
            f'<relationship name="banks"><destination>{{{BANK_GUID}}}</destination></relationship>'
            f'<object class="SingleSound"><relationship name="audioFile">'
            f'<destination>{{{AUDIO_GUID}}}</destination></relationship></object>'
            f"</object></objects>",
            encoding="utf-8",
        )
        (audio_dir / f"{{{AUDIO_GUID}}}.xml").write_text(
            f'<objects><object class="AudioFile" id="{{{AUDIO_GUID}}}">'
            f'<property name="assetPath"><value>gui/enemy_destroyed.wav</value></property>'
            f'<property name="length"><value>3.185</value></property>'
            f'<property name="channelCount"><value>2</value></property>'
            f'<property name="frequencyInKHz"><value>44.1</value></property>'
            f"</object></objects>",
            encoding="utf-8",
        )

        cls.rvc = cls.root / "RVC"
        (cls.rvc / "configs").mkdir(parents=True)
        (cls.rvc / "assets").mkdir()
        (cls.rvc / "webui.py").write_text("# stub", encoding="utf-8")
        (cls.rvc / "configs" / "config.py").write_text("# stub", encoding="utf-8")

        cls.rvc_parent = cls.root / "RVC-parent"
        nested = cls.rvc_parent / "nested" / "RVC20240604Nvidia"
        (nested / "configs").mkdir(parents=True)
        (nested / "configs" / "config.py").write_text("# stub", encoding="utf-8")
        (nested / "assets").mkdir()
        (nested / "go-webui.bat").write_text("@echo off", encoding="utf-8")

        cls.httpd = create_server("127.0.0.1", 0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        config.CONFIG_PATH = cls._old_config_path
        catalog.store.clear()
        cls.tmp.cleanup()

    def raw_request(
        self,
        path: str,
        method: str = "GET",
        headers: dict | None = None,
    ) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(
            self.base + path,
            method=method,
            headers=headers or {},
        )
        try:
            with self.opener.open(req, timeout=15) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def request(self, path: str, method: str = "GET", payload=None) -> tuple[int, dict]:
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = {"raw": raw}
                return resp.status, body
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = {"raw": raw}
            return exc.code, body

    def test_health(self) -> None:
        status, body = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["app"], "WT Voice Forge")
        self.assertEqual(body["version"], "0.9.20")
        self.assertEqual(body["milestone"], "M7.20")

    def test_components_preflight_route(self) -> None:
        status, body = self.request(
            "/api/components/preflight",
            "POST",
            {
                "id": "rvc-nvidia",
                "target": str(self.root / "components"),
                "slim": False,
            },
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"] or body["warnings"])
        self.assertGreater(body["download_bytes"], 0)
        self.assertGreater(body["required_bytes"], body["download_bytes"])
        self.assertEqual(body["component"], "rvc-nvidia")

    def test_storage_clean_route_only_logs(self) -> None:
        log = self.root / "data" / "logs" / "api-test.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_bytes(b"log" * 10)
        status, body = self.request(
            "/api/storage/clean", "POST", {"categories": ["logs"]}
        )
        self.assertEqual(status, 200, body)
        self.assertGreaterEqual(body["freed_bytes"], 30)
        self.assertFalse(log.exists())
        self.assertIn("storage", body)

    def test_components_uninstall_rejects_unknown(self) -> None:
        status, body = self.request(
            "/api/components/uninstall", "POST", {"id": "not-a-component"}
        )
        self.assertEqual(status, 400)
        self.assertIn("卸载", body["error"])

    def test_quick_options(self) -> None:
        status, body = self.request("/api/quick/options")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["nations"]), 10)
        self.assertEqual(
            [item["id"] for item in body["scopes"]],
            ["common", "ground", "naval", "cockpit"],
        )
        self.assertEqual(
            [item["id"] for item in body["line_scopes"]],
            ["common", "air", "other", "ground", "naval", "cockpit"],
        )
        self.assertIn("cockpit", body)
        self.assertEqual(body["cockpit"]["bank"], "aircraft_gui")
        self.assertEqual([item["id"] for item in body["modes"]], ["zh", "original"])
        self.assertIn("catalog", body)
        self.assertTrue(any(item["id"] == "de" for item in body["nations"]))

    def test_quick_prepare_requires_source(self) -> None:
        status, body = self.request(
            "/api/quick/prepare",
            "POST",
            {"nations": ["de"], "scopes": ["ground"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("音色项目", body["error"])

    def test_convert_rejects_regular_project(self) -> None:
        status, body = self.request(
            "/api/voice/projects", "POST", {"name": "普通合成项目"}
        )
        self.assertEqual(status, 200)
        status, body = self.request(
            "/api/voice/convert",
            "POST",
            {"project": body["project"]["id"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("原语言", body["error"])

    def test_state(self) -> None:
        status, body = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("app", body)
        self.assertIn("gpu", body)
        self.assertIn("banks", body)
        self.assertIn("recommended_backend", body["gpu"])
        self.assertIn("fmod_studio", body)
        self.assertIn("vgmstream", body)
        self.assertIn("available", body["vgmstream"])

    def test_game_detect(self) -> None:
        status, body = self.request("/api/game/detect")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsInstance(body["installs"], list)

    def test_game_scan_valid(self) -> None:
        status, body = self.request("/api/game/scan?path=" + urllib.parse.quote(str(self.game)))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["game"]["bank_count"], 1)
        self.assertEqual(body["banks"]["total"], 1)
        self.assertEqual(body["banks"]["key_banks"][0]["name"], "aircraft_gui")
        self.assertTrue(body["banks"]["key_banks"][0]["present"])

    def test_game_scan_invalid(self) -> None:
        status, body = self.request("/api/game/scan?path=" + urllib.parse.quote(str(self.root / "nope")))
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_kit_inspect(self) -> None:
        status, body = self.request("/api/kit/inspect?path=" + urllib.parse.quote(str(self.kit)))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["kit"]["valid"])
        self.assertEqual(body["kit"]["bank_xml_count"], 1)
        self.assertEqual(body["kit"]["event_xml_count"], 1)

    def test_catalog(self) -> None:
        status, body = self.request(
            "/api/catalog?limit=5&path=" + urllib.parse.quote(str(self.kit))
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        catalog = body["catalog"]
        self.assertEqual(catalog["event_count"], 1)
        self.assertEqual(catalog["returned_events"], 1)
        self.assertEqual(catalog["events"][0]["event"], "enemy_destroyed")
        self.assertEqual(catalog["events"][0]["bank"], "aircraft_gui")

    def test_catalog_v2_build_query_and_audio(self) -> None:
        status, body = self.request(
            "/api/kit/select", method="POST", payload={"path": str(self.kit)}
        )
        self.assertEqual(status, 200)
        status, body = self.request(
            "/api/config",
            method="POST",
            payload={"data_dir": str(self.root / "data"), "game_path": ""},
        )
        self.assertEqual(status, 200)

        status, body = self.request("/api/catalog/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["available"])

        status, body = self.request(
            "/api/catalog/build", method="POST", payload={"force": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNotNone(body["job"])
        deadline = time.time() + 15
        job = body["job"]
        while time.time() < deadline:
            status, job_body = self.request("/api/jobs/current")
            self.assertEqual(status, 200)
            job = job_body["job"]
            if job and not job["active"]:
                break
            time.sleep(0.05)
        self.assertIsNotNone(job)
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(job["kind"], "catalog")

        status, body = self.request("/api/catalog/status")
        self.assertTrue(body["available"])
        self.assertFalse(body["stale"])
        self.assertEqual(body["stats"]["assigned_events"], 1)

        status, body = self.request("/api/catalog/query?q=enemy&page_size=5")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        result = body["catalog"]
        self.assertEqual(result["page"]["total"], 1)
        event = result["events"][0]
        self.assertEqual(event["event"], "enemy_destroyed")
        self.assertEqual(event["duration_sec"], 3.185)
        self.assertEqual(event["assets"][0]["guid"], AUDIO_GUID)

        status, body = self.request("/api/catalog/query?has_assets=assets")
        self.assertEqual(status, 200)
        self.assertEqual(body["catalog"]["page"]["total"], 1)

        status, headers, content = self.raw_request(
            f"/api/audio/catalog?guid={AUDIO_GUID}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "audio/ogg")
        self.assertTrue(content.startswith(b"OggS"))

        status, headers, content = self.raw_request(
            f"/api/audio/catalog?guid={AUDIO_GUID}",
            headers={"Range": "bytes=0-3"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(content, b"OggS")
        self.assertEqual(headers.get("Content-Range"), "bytes 0-3/260")

        status, headers, content = self.raw_request(
            f"/api/audio/catalog?guid={AUDIO_GUID}",
            method="HEAD",
        )
        self.assertEqual(status, 200)
        self.assertEqual(content, b"")
        self.assertEqual(headers.get("Content-Length"), "260")

        status, headers, content = self.raw_request(
            f"/api/audio/catalog?guid={AUDIO_GUID}",
            headers={"Range": "bytes=9999-"},
        )
        self.assertEqual(status, 416)
        self.assertEqual(headers.get("Content-Range"), "bytes */260")

        status, body = self.request("/api/audio/catalog?guid=not-there")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])

        status, body = self.request("/api/unpack")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("vgmstream", body)
        self.assertIn("banks", body)

        status, body = self.request("/api/unpack/manifest?bank=aircraft_gui")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])

        status, body = self.request(
            "/api/unpack/start", method="POST", payload={"bank": "aircraft_gui"}
        )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_kit_select_and_state(self) -> None:
        status, body = self.request(
            "/api/kit/select", method="POST", payload={"path": str(self.kit)}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["config"]["kit_path"], str(self.kit))

        status, body = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNotNone(body["kit"])
        self.assertTrue(body["kit"]["valid"])

    def test_rvc_detect(self) -> None:
        status, body = self.request("/api/rvc/detect?path=" + urllib.parse.quote(str(self.rvc)))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["rvc"]["available"])

    def test_rvc_select_nested_parent(self) -> None:
        status, body = self.request(
            "/api/rvc/select", method="POST", payload={"path": str(self.rvc_parent)}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("RVC20240604Nvidia", body["config"]["rvc_path"])

    def test_components_info(self) -> None:
        status, body = self.request("/api/components")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        ids = {item["id"] for item in body["catalog"]}
        self.assertIn("rvc-nvidia", ids)
        self.assertIn("rvc-amd", ids)
        self.assertIn(body["rvc_component"], ids)
        self.assertIn("default_dir", body)
        self.assertIn("extractor", body)

    def test_components_job(self) -> None:
        status, body = self.request("/api/components/job")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("job", body)

    def test_components_download_invalid(self) -> None:
        status, body = self.request(
            "/api/components/download", method="POST", payload={"id": "not-a-component"}
        )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_config_save_and_state(self) -> None:
        status, body = self.request(
            "/api/game/select", method="POST", payload={"path": str(self.game)}
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["config"]["game_path"], str(self.game))

        status, body = self.request("/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(body["config"]["game_path"], str(self.game))

        status, body = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertIsNotNone(body["selected_game"])
        self.assertEqual(body["selected_game"]["bank_count"], 1)

    def test_voice_project_and_lines(self) -> None:
        status, body = self.request("/api/voice/projects", "POST", {"name": "测试音色"})
        self.assertEqual(status, 200)
        project_id = body["project"]["id"]

        status, body = self.request(
            "/api/voice/lines",
            "POST",
            {"project": project_id, "text": "准备起飞", "event": "takeoff"},
        )
        self.assertEqual(status, 200)
        line_id = body["line"]["id"]

        status, body = self.request(f"/api/voice/project?project={project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["project"]["lines"]), 1)

        status, body = self.request(
            "/api/voice/line/update",
            "POST",
            {"project": project_id, "line": line_id, "text": "准备降落"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["line"]["text"], "准备降落")

        status, body = self.request(
            f"/api/voice/audio?project={project_id}&line={line_id}"
        )
        self.assertEqual(status, 404)

        status, _headers, raw = self.raw_request(f"/api/voice/export?project={project_id}")
        self.assertEqual(status, 200)
        self.assertIn("event_id", raw.decode("utf-8"))

    def test_voice_take_audio_and_select(self) -> None:
        status, body = self.request("/api/voice/projects", "POST", {"name": "多版本测试"})
        self.assertEqual(status, 200)
        project_id = body["project"]["id"]
        status, body = self.request(
            "/api/voice/lines",
            "POST",
            {"project": project_id, "text": "多版本台词"},
        )
        self.assertEqual(status, 200)
        line_id = body["line"]["id"]
        data_dir = Path(config.load_config().data_dir)
        folder = voice.takes_dir(data_dir, project_id, line_id)
        first = "t_111111111111"
        second = "t_222222222222"
        make_wav(folder / f"{first}.wav", 0.3)
        make_wav(folder / f"{second}.wav", 0.5)
        voice.save_takes(
            data_dir,
            project_id,
            line_id,
            [
                {"id": first, "duration_sec": 0.3},
                {"id": second, "duration_sec": 0.5},
            ],
            selected_id=first,
        )

        status, headers, raw = self.raw_request(
            f"/api/voice/audio?project={project_id}&line={line_id}&take={second}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(raw.startswith(b"RIFF"))

        status, body = self.request(
            "/api/voice/take/select",
            "POST",
            {"project": project_id, "line": line_id, "take": second},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["line"]["selected_take"], second)
        self.assertAlmostEqual(body["line"]["duration_sec"], 0.5, places=3)

        status, _headers, raw = self.raw_request(
            f"/api/voice/audio?project={project_id}&line={line_id}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(raw.startswith(b"RIFF"))

        status, body = self.request(
            f"/api/voice/audio?project={project_id}&line={line_id}&take=t_333333333333"
        )
        self.assertEqual(status, 400)
        self.assertIn("版本", body["error"])

    def test_voice_line_tuning_route(self) -> None:
        status, body = self.request("/api/voice/projects", "POST", {"name": "语气音量"})
        project_id = body["project"]["id"]
        refs = self.root / "tuning_refs"
        make_wav(refs / "calm.wav")
        make_wav(refs / "angry.wav")
        status, body = self.request(
            "/api/voice/reference",
            "POST",
            {
                "project": project_id,
                "path": str(refs),
                "prompt_text": "参考文字",
                "prompt_lang": "zh",
            },
        )
        self.assertEqual(status, 200, body)
        files = body["project"]["reference"]["files"]
        status, body = self.request(
            "/api/voice/lines", "POST", {"project": project_id, "text": "调整我"}
        )
        line_id = body["line"]["id"]
        data_dir = Path(config.load_config().data_dir)
        take_id = "t_abcdef012348"
        take = voice.take_audio_path(data_dir, project_id, line_id, take_id)
        make_tone(take, peak=10000)
        voice.save_takes(
            data_dir,
            project_id,
            line_id,
            [{"id": take_id, "duration_sec": 0.2}],
        )

        status, body = self.request(
            "/api/voice/line/tuning",
            "POST",
            {"project": project_id, "line": line_id, "gain_db": 3},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["updated"], 1)
        main = voice.line_audio_path(data_dir, project_id, line_id)
        self.assertAlmostEqual(wav_peak(main), 14125, delta=60)

        status, body = self.request(
            "/api/voice/line/tuning",
            "POST",
            {"project": project_id, "line": line_id, "gain_db": -6},
        )
        self.assertEqual(status, 200, body)
        self.assertAlmostEqual(wav_peak(main), 5011, delta=40)

        status, body = self.request(
            "/api/voice/line/tuning",
            "POST",
            {
                "project": project_id,
                "line": line_id,
                "reference": files[1],
                "speed": 1.1,
            },
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["needs_synthesis"], 1)
        self.assertEqual(body["project"]["lines"][0]["tuning"]["reference"], files[1])
        self.assertFalse(main.exists())

        status, body = self.request(
            "/api/voice/line/tuning",
            "POST",
            {"project": project_id, "line": line_id, "reference": "missing.wav"},
        )
        self.assertEqual(status, 400)
        status, body = self.request(
            "/api/voice/line/tuning",
            "POST",
            {"project": project_id, "line": line_id, "speed": "fast"},
        )
        self.assertEqual(status, 400)
        status, body = self.request(
            "/api/voice/line/tuning",
            "POST",
            {"project": project_id, "lines": [line_id, "l_ffffffffffff"], "gain_db": 0},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["updated"], 1)

    def test_voice_synthesize_uses_per_line_tuning(self) -> None:
        from app import jobs

        status, body = self.request("/api/voice/projects", "POST", {"name": "逐句语气合成"})
        project_id = body["project"]["id"]
        refs = self.root / "synth_refs"
        make_wav(refs / "a.wav")
        make_wav(refs / "b.wav")
        voice.import_reference(
            Path(config.load_config().data_dir),
            project_id,
            refs,
            prompt_text="参考文字",
            prompt_lang="zh",
        )
        data_dir = Path(config.load_config().data_dir)
        project = voice.load_project(data_dir, project_id)
        files = project["reference"]["files"]
        voice.set_reference_texts(
            data_dir,
            project_id,
            {files[0]: "平静参考", files[1]: "急促参考"},
        )
        first = voice.add_line(data_dir, project_id, "第一句")["line"]
        second = voice.add_line(data_dir, project_id, "第二句")["line"]
        voice.set_line_tuning(
            data_dir, project_id, [first["id"]], reference=files[0], speed=0.9
        )
        voice.set_line_tuning(
            data_dir, project_id, [second["id"]], reference=files[1], speed=1.2
        )

        calls: list[dict] = []

        def fake_synthesize(**kwargs):
            calls.append(kwargs)
            make_wav(Path(kwargs["out_path"]), 0.2)
            return Path(kwargs["out_path"])

        deadline = time.time() + 5
        while jobs.manager.busy() and time.time() < deadline:
            time.sleep(0.1)
        with mock.patch.object(gpt_sovits, "find_install", return_value=self.kit), \
                mock.patch.object(gpt_sovits, "start_server", return_value={}), \
                mock.patch.object(gpt_sovits, "activate", return_value=None), \
                mock.patch.object(gpt_sovits, "synthesize", side_effect=fake_synthesize):
            status, body = self.request(
                "/api/voice/synthesize",
                "POST",
                {
                    "project": project_id,
                    "lines": [],
                    "force": False,
                    "versions": 1,
                    "text_lang": "zh",
                    "dedupe": True,
                },
            )
            self.assertEqual(status, 200, body)
            job_id = body["job"]["id"]
            final = None
            deadline = time.time() + 10
            while time.time() < deadline:
                status, poll = self.request("/api/jobs/current")
                job = poll.get("job") or {}
                if job.get("id") == job_id and not job.get("active"):
                    final = job
                    break
                time.sleep(0.1)
        self.assertIsNotNone(final)
        self.assertEqual(final["state"], "done", final.get("error"))
        self.assertEqual(len(calls), 2)
        by_text = {call["text"]: call for call in calls}
        self.assertEqual(by_text["第一句"]["speed_factor"], 0.9)
        self.assertEqual(by_text["第二句"]["speed_factor"], 1.2)
        self.assertEqual(Path(by_text["第一句"]["ref_audio_path"]).name, files[0])
        self.assertEqual(Path(by_text["第二句"]["ref_audio_path"]).name, files[1])
        self.assertEqual(by_text["第一句"]["prompt_text"], "平静参考")
        self.assertEqual(by_text["第二句"]["prompt_text"], "急促参考")
        saved = voice.load_project(data_dir, project_id)
        self.assertTrue(all(line.get("audio") for line in saved["lines"]))

    def test_voice_reference_texts_and_train_guard(self) -> None:
        status, body = self.request("/api/voice/projects", "POST", {"name": "文字测试"})
        project_id = body["project"]["id"]
        reference = self.root / "ref_text.wav"
        reference.write_bytes(b"RIFF" + b"0" * 64)
        status, body = self.request(
            "/api/voice/reference",
            "POST",
            {"project": project_id, "path": str(reference)},
        )
        self.assertEqual(status, 200)
        name = body["project"]["reference"]["files"][0]
        status, body = self.request(
            "/api/voice/reference-texts",
            "POST",
            {"project": project_id, "texts": {name: "这段话"}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["project"]["reference"]["texts"][name], "这段话")

        with mock.patch.object(gpt_sovits, "find_install", return_value=self.root):
            status, body = self.request("/api/voice/train", "POST", {"project": project_id})
        self.assertEqual(status, 400)
        self.assertIn("至少需要", body["error"])

    def test_voice_model_toggle_requires_trained_weights(self) -> None:
        status, body = self.request("/api/voice/projects", "POST", {"name": "模型切换"})
        project_id = body["project"]["id"]
        status, body = self.request(
            "/api/voice/model",
            "POST",
            {"project": project_id, "mode": "fine_tuned"},
        )
        self.assertEqual(status, 400)
        self.assertIn("还没有训练", body["error"])
        status, body = self.request(
            "/api/voice/model",
            "POST",
            {"project": project_id, "mode": "zero_shot"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["project"]["model"]["mode"], "zero_shot")

    def test_voice_imports_external_gpt_and_rvc_models(self) -> None:
        status, body = self.request("/api/voice/projects", "POST", {"name": "导入模型"})
        self.assertEqual(status, 200)
        project_id = body["project"]["id"]
        weights = self.root / "external_weights"
        weights.mkdir()
        sovits = weights / "role_e8.pth"
        gpt = weights / "role-e12.ckpt"
        sovits.write_bytes(b"sovits")
        gpt.write_bytes(b"gpt")
        status, body = self.request(
            "/api/voice/model/import",
            "POST",
            {"project": project_id, "sovits": str(sovits), "gpt": str(gpt)},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["project"]["model"]["source"], "external")
        self.assertTrue(Path(body["project"]["model"]["sovits_path"]).is_file())
        self.assertTrue(Path(body["project"]["model"]["gpt_path"]).is_file())

        status, body = self.request(
            "/api/voice/model/import",
            "POST",
            {"project": project_id, "sovits": str(sovits)},
        )
        self.assertEqual(status, 400)
        self.assertIn("GPT", body["error"])

        rvc_model = weights / "role_rvc.pth"
        rvc_index = weights / "added.index"
        rvc_model.write_bytes(b"rvc")
        rvc_index.write_bytes(b"index")
        status, body = self.request(
            "/api/voice/rvc/model/import",
            "POST",
            {
                "project": project_id,
                "model": str(rvc_model),
                "index": str(rvc_index),
                "reset_audio": False,
            },
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["project"]["rvc"]["source"], "external")
        self.assertTrue(Path(body["project"]["rvc"]["model"]).is_file())
        self.assertTrue(Path(body["project"]["rvc"]["index"]).is_file())

    def test_voice_transcribe_updates_prompt(self) -> None:
        status, body = self.request("/api/voice/projects", "POST", {"name": "识别测试"})
        self.assertEqual(status, 200)
        project_id = body["project"]["id"]
        reference = self.root / "reference.wav"
        reference.write_bytes(b"RIFF" + b"0" * 64)
        status, body = self.request(
            "/api/voice/reference",
            "POST",
            {"project": project_id, "path": str(reference), "prompt_lang": "zh"},
        )
        self.assertEqual(status, 200)
        with mock.patch.object(gpt_sovits, "transcribe", return_value="识别出来的文字") as patched:
            status, body = self.request(
                "/api/voice/transcribe",
                "POST",
                {"project": project_id, "file": body["prompt_file"], "lang": "zh"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "识别出来的文字")
        self.assertEqual(body["project"]["reference"]["prompt_text"], "识别出来的文字")
        patched.assert_called_once()

    def test_voice_build_guard(self) -> None:
        from app import exporter, voice
        from tests.test_voice import make_tone, make_wav, wav_peak

        status, body = self.request("/api/voice/projects", "POST", {"name": "构建测试"})
        project_id = body["project"]["id"]
        status, body = self.request(
            "/api/voice/build", "POST", {"project": project_id}
        )
        self.assertEqual(status, 400)
        self.assertIn("合成", body["error"])

        added = voice.add_line(
            self.root / "data",
            project_id,
            "击毁目标",
            source={
                "event_id": EVENT_GUID,
                "event": "enemy_destroyed",
                "bank": "aircraft_gui",
                "assets": [{"guid": AUDIO_GUID, "path": "gui/enemy_destroyed.wav"}],
            },
        )
        line_id = added["line"]["id"]
        make_wav(voice.line_audio_path(self.root / "data", project_id, line_id), 1.0)
        voice.mark_line(
            self.root / "data",
            project_id,
            line_id,
            status="ready",
            audio=f"audio/{line_id}.wav",
        )

        status, body = self.request(f"/api/voice/project?project={project_id}")
        self.assertEqual(status, 200)
        self.assertEqual(body["build"]["banks"], [{"name": "aircraft_gui", "lines": 1}])
        self.assertEqual(body["build"]["ready_lines"], 1)

        with mock.patch.object(exporter, "_cli", return_value=None):
            status, body = self.request(
                "/api/voice/build", "POST", {"project": project_id}
            )
        self.assertEqual(status, 400)
        self.assertIn("FMOD", body["error"])

    def test_voice_package_download(self) -> None:
        status, body = self.request("/api/voice/projects", "POST", {"name": "下载测试"})
        project_id = body["project"]["id"]
        exports = self.root / "data" / "voice_projects" / project_id / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        (exports / "pack_20260101_000000.wtvoice").write_bytes(b"PK\x05\x06" + b"0" * 18)

        status, body = self.request(
            f"/api/voice/package?project={project_id}&name=../escape.wtvoice"
        )
        self.assertEqual(status, 400)
        status, body = self.request(
            f"/api/voice/package?project={project_id}&name=missing.wtvoice"
        )
        self.assertEqual(status, 404)

        status, headers, content = self.raw_request(
            f"/api/voice/package?project={project_id}&name=pack_20260101_000000.wtvoice"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/zip")
        self.assertTrue(content.startswith(b"PK"))

        status, headers, zip_content = self.raw_request(
            f"/api/voice/package?project={project_id}"
            f"&name=pack_20260101_000000.wtvoice&as=zip"
        )
        self.assertEqual(status, 200)
        self.assertIn("pack_20260101_000000.zip", headers.get("Content-Disposition", ""))
        self.assertEqual(zip_content, content)

    def test_voice_package_export_and_one_click_install(self) -> None:
        status, _ = self.request("/api/game/select", "POST", {"path": str(self.game)})
        self.assertEqual(status, 200)
        status, body = self.request("/api/voice/projects", "POST", {"name": "一键安装"})
        project_id = body["project"]["id"]
        exports = self.root / "data" / "voice_projects" / project_id / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        name = "一键安装_20260101_000000.wtvoice"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema": 1,
                        "format": "wtvoice",
                        "name": "一键安装",
                        "banks": ["quick_api.bank"],
                        "lines": 1,
                    },
                    ensure_ascii=False,
                ),
            )
            archive.writestr("sound/mod/quick_api.bank", b"QUICK-API-BANK")
        package = buffer.getvalue()
        (exports / name).write_bytes(package)

        status, body = self.request(
            "/api/voice/package/export",
            "POST",
            {"project": project_id, "name": name},
        )
        self.assertEqual(status, 200)
        exported = Path(body["path"])
        self.assertEqual(exported.parent, self.root / "data" / "语音包导出")
        self.assertEqual(exported.read_bytes(), package)

        status, body = self.request(
            "/api/voice/package/export",
            "POST",
            {"project": project_id, "name": "../escape.wtvoice"},
        )
        self.assertEqual(status, 400)

        target = self.game / "sound" / "mod" / "quick_api.bank"
        token = ""
        try:
            with mock.patch.object(packs, "game_running", return_value=False):
                status, body = self.request(
                    "/api/voice/package/install",
                    "POST",
                    {"project": project_id, "name": name},
                )
                self.assertEqual(status, 200, body)
                token = body["pack"]["token"]
                pack_id = body["pack"]["id"]
                self.assertEqual(target.read_bytes(), b"QUICK-API-BANK")
                config_text = (self.game / "config.blk").read_text(encoding="utf-8")
                self.assertIn("enable_mod:b=yes", config_text)
                self.assertIn("fmod_sound_enable:b=yes", config_text)

                status, body = self.request(
                    "/api/voice/package/install",
                    "POST",
                    {"project": project_id, "name": name},
                )
                self.assertEqual(status, 200, body)
                self.assertEqual(body["pack"]["token"], token)
                matching = [
                    entry
                    for entry in body["installed"]["packs"]
                    if entry["token"] == token
                ]
                self.assertEqual(len(matching), 1)
                pack_id = body["pack"]["id"]

                status, body = self.request(
                    "/api/packs/uninstall", "POST", {"pack_id": pack_id}
                )
                self.assertEqual(status, 200, body)
                self.assertFalse(target.exists())
        finally:
            with mock.patch.object(packs, "game_running", return_value=False):
                self.request("/api/packs/enable-mod", "POST", {"enabled": False})
            if token:
                with mock.patch.object(packs, "game_running", return_value=False):
                    self.request("/api/packs/discard", "POST", {"token": token})
            exported.unlink(missing_ok=True)
            try:
                (self.root / "data" / "语音包导出").rmdir()
            except OSError:
                pass

    def test_localized_import_and_source_audio(self) -> None:
        status, body = self.request(
            "/api/kit/select", method="POST", payload={"path": str(self.kit)}
        )
        self.assertEqual(status, 200)
        bank = "_crew_dialogs_ground_en_us"
        bank_item = {
            "id": bank,
            "label": "美军英语 · 陆战乘员",
            "scope": "ground",
            "language": "en_us",
            "source": "crew/en_us",
            "file_count": 1,
            "stream_count": 1,
        }
        stream = {
            "event_id": f"at:{bank}:voice_message_test",
            "event": "voice_message_test",
            "bank": bank,
            "label": "美军英语 · 陆战乘员",
            "scope": "ground",
            "language": "en_us",
            "localized": True,
            "game_only": True,
            "source_file": "voice_message_test.wav",
            "target_file": "voice_message_test.wav",
            "duration_sec": 0.5,
            "assets": [
                {
                    "guid": "",
                    "path": "crew/en_us/voice_message_test.wav",
                    "file": "voice_message_test.wav",
                    "duration_sec": 0.5,
                }
            ],
        }
        make_wav(self.kit / "Assets" / "crew" / "en_us" / "voice_message_test.wav", 0.5)
        with mock.patch.object(server.localized, "banks", return_value=[bank_item]):
            status, body = self.request("/api/localized/banks")
        self.assertEqual(status, 200)
        self.assertEqual(body["banks"][0]["id"], bank)
        with mock.patch.object(server.localized, "streams", return_value=[stream]):
            status, body = self.request(f"/api/localized/streams?bank={bank}")
        self.assertEqual(status, 200)
        self.assertEqual(body["streams"][0]["event"], "voice_message_test")

        status, body = self.request("/api/voice/projects", "POST", {"name": "本地化测试"})
        project_id = body["project"]["id"]
        with mock.patch.object(server.localized, "streams", return_value=[stream]):
            status, body = self.request(
                "/api/voice/import-localized",
                "POST",
                {"project": project_id, "bank": bank},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["added"], 1)
        line = body["project"]["lines"][0]
        self.assertTrue(line["source"]["localized"])

        with mock.patch.object(
            server.localized,
            "coverage",
            return_value={
                "banks": [{"bank": bank, "zh_missing": 1}],
                "zh_banks": {"ground": ["_crew_dialogs_ground_zh"]},
                "extra_game_banks": [],
            },
        ):
            cov_status, cov_body = self.request("/api/localized/coverage")
        self.assertEqual(cov_status, 200)
        self.assertEqual(cov_body["banks"][0]["zh_missing"], 1)

        with mock.patch.object(
            server.localized,
            "copy_official_audio",
            return_value={
                "project": body["project"],
                "source_bank": "_crew_dialogs_ground_zh",
                "source_total": 2,
                "copied": 1,
                "missing": [],
            },
        ):
            status, copied = self.request(
                "/api/voice/copy-official",
                "POST",
                {"project": project_id, "bank": bank},
            )
        self.assertEqual(status, 200)
        self.assertEqual(copied["copied"], 1)

        status, headers, content = self.raw_request(
            f"/api/audio/source?project={project_id}&line={line['id']}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(content.startswith(b"RIFF"))

    def test_localized_translation_task_download(self) -> None:
        status, _ = self.request(
            "/api/kit/select", method="POST", payload={"path": str(self.kit)}
        )
        self.assertEqual(status, 200)
        bank = "_crew_dialogs_naval_en"
        streams = [
            {"name": "voice_message_commander_angle_10", "index": 1, "duration_sec": 1.0},
            {"name": "voice_message_commander_angle_20", "index": 2, "duration_sec": 1.0},
            {"name": "voice_message_commander_we_lost_v1", "index": 3, "duration_sec": 2.0},
            {"name": "voice_message_commander_we_lost_v2", "index": 4, "duration_sec": 2.2},
        ]
        with mock.patch.object(
            server.localized, "game_bank_path", return_value=Path("fake.bank")
        ), mock.patch.object(server.localized, "game_streams", return_value=streams):
            status, headers, content = self.raw_request(
                f"/api/localized/translation-task?bank={bank}"
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/zip")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 4)
            group_name = next(name for name in names if "分组任务" in name)
            group_text = archive.read(group_name).decode("utf-8-sig")
            self.assertIn("task_id", group_text)
            self.assertIn("commander:angle", group_text)
            self.assertIn("数字档位", group_text)
            stream_name = next(name for name in names if "逐条任务" in name)
            stream_text = archive.read(stream_name).decode("utf-8-sig")
            self.assertIn("voice_message_commander_we_lost_v2", stream_text)
            readme = archive.read("README.txt").decode("utf-8")
            self.assertIn("海战", readme)

    def test_localized_translation_task_missing_only(self) -> None:
        status, _ = self.request(
            "/api/kit/select", method="POST", payload={"path": str(self.kit)}
        )
        self.assertEqual(status, 200)
        bank = "_crew_dialogs_naval_en"
        streams = [
            {"name": "voice_message_commander_angle_10", "index": 1, "duration_sec": 1.0},
            {"name": "voice_message_commander_we_lost_v1", "index": 2, "duration_sec": 2.0},
        ]
        with mock.patch.object(
            server.localized, "game_bank_path", return_value=Path("fake.bank")
        ), mock.patch.object(
            server.localized, "game_streams", return_value=streams
        ), mock.patch.object(
            server.localized,
            "official_cn_names",
            return_value={"voice_message_commander_angle_10"},
        ):
            status, headers, content = self.raw_request(
                f"/api/localized/translation-task?bank={bank}&missing=1"
            )
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            stream_name = next(name for name in archive.namelist() if "逐条任务" in name)
            text = archive.read(stream_name).decode("utf-8-sig")
            self.assertIn("voice_message_commander_we_lost_v1", text)
            self.assertNotIn("voice_message_commander_angle_10", text)

    def test_packs_upload_install_and_uninstall(self) -> None:
        status, _ = self.request("/api/game/select", "POST", {"path": str(self.game)})
        self.assertEqual(status, 200)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps({"format": "wtvoice", "name": "API 语音包", "banks": ["api_demo.bank"]}),
            )
            archive.writestr("banks/api_demo.bank", b"API-BANK")
        request = urllib.request.Request(
            self.base + "/api/packs/upload?filename=" + urllib.parse.quote("api.wtvoice"),
            data=buffer.getvalue(),
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        with mock.patch.object(packs, "game_running", return_value=False):
            with self.opener.open(request, timeout=15) as response:
                uploaded = json.loads(response.read().decode("utf-8"))
        self.assertTrue(uploaded["ok"])
        self.assertEqual(uploaded["pack"]["name"], "API 语音包")
        self.assertTrue(uploaded["game"]["valid"])
        token = uploaded["pack"]["token"]

        with mock.patch.object(packs, "game_running", return_value=False):
            status, body = self.request(
                "/api/packs/install",
                "POST",
                {
                    "token": token,
                    "config": {"enable_mod": True, "fmod_sound_enable": True},
                },
            )
            self.assertEqual(status, 200)
            target = self.game / "sound" / "mod" / "api_demo.bank"
            self.assertEqual(target.read_bytes(), b"API-BANK")
            config_text = (self.game / "config.blk").read_text(encoding="utf-8")
            self.assertIn("enable_mod:b=yes", config_text)
            self.assertIn("fmod_sound_enable:b=yes", config_text)
            pack_id = body["pack"]["id"]

            status, body = self.request("/api/packs/installed")
            self.assertEqual(status, 200)
            self.assertEqual(len(body["packs"]), 1)
            self.assertTrue(body["packs"][0]["healthy"])

            status, body = self.request("/api/packs/uninstall", "POST", {"pack_id": pack_id})
            self.assertEqual(status, 200)
            self.assertEqual(body["removed"], 1)
            self.assertFalse(target.exists())

            status, body = self.request("/api/packs/enable-mod", "POST", {"enabled": False})
            self.assertEqual(status, 200)
            self.assertIn(
                "enable_mod:b=no", (self.game / "config.blk").read_text(encoding="utf-8")
            )

        status, body = self.request("/api/packs/discard", "POST", {"token": token})
        self.assertEqual(status, 200)
        self.assertTrue(body["discarded"])

    def test_open_folder_rejects_unknown_target(self) -> None:
        status, body = self.request("/api/system/open-folder", "POST", {"target": "nope"})
        self.assertEqual(status, 400)
        self.assertFalse(body.get("ok"))

    def test_uninstall_requires_installed_build(self) -> None:
        status, body = self.request(
            "/api/system/uninstall", "POST", {"keep_data": True}
        )
        self.assertEqual(status, 400)
        self.assertIn("源码模式", body.get("error", ""))

    def test_unknown_api(self) -> None:
        status, body = self.request("/api/does-not-exist")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])

    def test_static_index(self) -> None:
        status, body = self.request("/")
        self.assertEqual(status, 200)
        raw = body.get("raw", "")
        self.assertIn("工作台", raw)
        self.assertIn("高级编辑", raw)
        self.assertIn("数据目录", raw)

    def test_static_traversal_blocked(self) -> None:
        status, _ = self.request("/%2e%2e/app/server.py")
        self.assertIn(status, (403, 404))

    def test_port_in_use_detection(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            self.assertTrue(server.port_in_use("127.0.0.1", port))
        self.assertFalse(server.port_in_use("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
