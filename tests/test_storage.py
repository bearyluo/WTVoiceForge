from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import storage


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"

    def write(self, relative: str, size: int) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    def test_scan_breaks_down_data_directory(self) -> None:
        self.write("components/gpt-sovits/model.bin", 1024)
        self.write("components/rvc-nvidia/runtime.bin", 2048)
        self.write("components/rvc-nvidia/RVC.7z", 4096)
        self.write("voice_projects/p1/reference.wav", 512)
        self.write("packs/backups/a.bank", 256)
        self.write("语音包导出/export.zip", 128)
        self.write("cache/catalog.json", 64)
        self.write("webview/cache.bin", 32)
        self.write("logs/app.log", 16)
        self.write("config.json", 8)

        result = storage.scan(self.root)
        items = {item["id"]: item for item in result["items"]}

        self.assertTrue(result["ok"])
        self.assertEqual(result["total_bytes"], 8184)
        self.assertEqual(result["total_files"], 10)
        self.assertEqual(items["gpt-sovits"]["bytes"], 1024)
        self.assertEqual(items["rvc"]["bytes"], 2048)
        self.assertEqual(items["archives"]["bytes"], 4096)
        self.assertEqual(items["voice-projects"]["bytes"], 512)
        self.assertEqual(items["packs"]["bytes"], 384)
        self.assertEqual(items["cache"]["bytes"], 64)
        self.assertEqual(items["webview"]["bytes"], 32)
        self.assertEqual(items["logs"]["bytes"], 16)
        self.assertEqual(items["other"]["bytes"], 8)
        self.assertEqual(sum(item["bytes"] for item in result["items"]), 8184)
        self.assertEqual(result["cleanable"]["bytes"], 64 + 16 + 4096)
        self.assertEqual(result["cleanable"]["ids"], ["archives", "cache", "logs"])
        self.assertGreater(result["disk_total_bytes"], 0)
        self.assertGreater(result["disk_free_bytes"], 0)

    def test_classify_keeps_archives_separate(self) -> None:
        self.assertEqual(storage.classify(("components", "gpt-sovits", "a.7z")), "archives")
        self.assertEqual(
            storage.classify(("components", "rvc-nvidia", "runtime", "a.zip")), "rvc"
        )
        self.assertEqual(
            storage.classify(
                ("components", "gpt-sovits", "GPT", "runtime", "python39.zip")
            ),
            "gpt-sovits",
        )
        self.assertEqual(storage.classify(("components", "gpt-sovits", "model.bin")), "gpt-sovits")
        self.assertEqual(storage.classify(("voice_projects", "p1", "a.wav")), "voice-projects")
        self.assertEqual(storage.classify(("语音包导出", "a.zip")), "packs")
        self.assertEqual(storage.classify(("unknown", "a.bin")), "other")

    def test_scan_missing_directory_is_empty(self) -> None:
        result = storage.scan(self.root / "missing")
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_bytes"], 0)
        self.assertEqual(result["total_files"], 0)
        self.assertTrue(all(item["bytes"] == 0 for item in result["items"]))

    def test_cleanup_only_touches_rebuildable_files(self) -> None:
        self.write("cache/index.json", 64)
        self.write("logs/app.log", 16)
        self.write("components/gpt-sovits/GPT-SoVITS-v4/model.bin", 1024)
        self.write("components/gpt-sovits/GPT-SoVITS-v4.7z.part", 128)
        self.write("components/rvc-nvidia/RVC/runtime.bin", 256)
        self.write("components/rvc-nvidia/RVC.7z", 32)
        self.write("components/rvc-nvidia/RVC/runtime/python39.zip", 2048)
        self.write("components/gpt-sovits/GPT/runtime/Lib/encodings/__init__.py", 12)
        self.write("voice_projects/p1/reference.wav", 512)
        self.write("packs/installed.json", 40)
        self.write("webview/EBWebView/Default/Cache/cache.bin", 8)
        self.write("webview/EBWebView/Default/Code Cache/code.bin", 4)
        self.write("webview/EBWebView/Default/Local Storage/profile.bin", 10)

        result = storage.cleanup(self.root)

        self.assertTrue(result["ok"])
        self.assertEqual(result["freed_bytes"], 252)
        self.assertEqual(result["removed_files"], 6)
        webview = next(item for item in result["categories"] if item["id"] == "webview")
        self.assertEqual(webview["bytes"], 12)
        self.assertFalse((self.root / "cache/index.json").exists())
        self.assertFalse((self.root / "logs/app.log").exists())
        self.assertFalse(
            (self.root / "components/gpt-sovits/GPT-SoVITS-v4.7z.part").exists()
        )
        self.assertFalse((self.root / "components/rvc-nvidia/RVC.7z").exists())
        self.assertTrue(
            (self.root / "components/gpt-sovits/GPT-SoVITS-v4/model.bin").is_file()
        )
        self.assertTrue((self.root / "components/rvc-nvidia/RVC/runtime.bin").is_file())
        self.assertTrue(
            (self.root / "components/rvc-nvidia/RVC/runtime/python39.zip").is_file()
        )
        self.assertTrue(
            (
                self.root
                / "components/gpt-sovits/GPT/runtime/Lib/encodings/__init__.py"
            ).is_file()
        )
        self.assertTrue((self.root / "voice_projects/p1/reference.wav").is_file())
        self.assertTrue((self.root / "packs/installed.json").is_file())
        self.assertFalse(
            (self.root / "webview/EBWebView/Default/Cache/cache.bin").exists()
        )
        self.assertTrue(
            (self.root / "webview/EBWebView/Default/Local Storage/profile.bin").is_file()
        )

    def test_cleanup_skips_archives_during_component_job(self) -> None:
        self.write("components/gpt-sovits/package.7z.part", 100)
        result = storage.cleanup(self.root, ["archives"], allow_archives=False)
        self.assertEqual(result["freed_bytes"], 0)
        self.assertTrue((self.root / "components/gpt-sovits/package.7z.part").exists())
        self.assertTrue(any("组件下载" in item for item in result["skipped"]))

    def test_cleanup_rejects_unknown_category(self) -> None:
        with self.assertRaises(ValueError):
            storage.cleanup(self.root, ["voice-projects"])

    def test_usage_while_scanning_after_cache_invalidation(self) -> None:
        thread = mock.Mock()
        thread.is_alive.return_value = True
        previous = (storage._cache, storage._cache_dir, storage._scan_thread)
        storage._cache = None
        storage._cache_dir = str(self.root)
        storage._scan_thread = thread
        try:
            result = storage.usage(self.root)
        finally:
            storage._cache, storage._cache_dir, storage._scan_thread = previous
        self.assertTrue(result["scanning"])
        self.assertEqual(result["total_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
