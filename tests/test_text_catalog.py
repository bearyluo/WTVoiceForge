from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import jobs, text_catalog


class TextCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.game = self.root / "War Thunder"
        (self.game / "sound").mkdir(parents=True)
        self.banks = {}
        for bank in (
            "_crew_dialogs_common_zh",
            "_crew_dialogs_ground_zh",
            "_crew_dialogs_ground_sm_zh",
        ):
            path = self.game / "sound" / f"{bank}.assets.bank"
            path.write_bytes(b"bank")
            self.banks[bank] = path

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_status_is_empty_without_cache(self) -> None:
        status = text_catalog.status(self.root / "data")
        self.assertFalse(status["ready"])
        self.assertEqual(status["scopes"], {})

    def test_build_writes_scope_cache(self) -> None:
        def fake_bank_path(_game, bank):
            return self.banks.get(bank)

        def fake_unique(bank_path, _out_dir, _temp_root, *, prefix="", skip_names=None):
            if "common_zh" in bank_path.name:
                return {"c_0001.wav": "塔台，准备起飞"}
            if "ground_sm_zh" in bank_path.name:
                return {"sm_0002.wav": "补充台词"}
            return {"g_0001.wav": "击毁敌方目标"}

        def fake_transcribe(folder, names, _job=None):
            text = "通用台词" if folder.name == "common" else "陆战台词"
            return {name: text for name in names.values()}

        job = jobs.Job("quick_prepare")
        with mock.patch.object(
            text_catalog.localized, "game_bank_path", side_effect=fake_bank_path
        ), mock.patch.object(
            text_catalog, "_unique_streams", side_effect=fake_unique
        ), mock.patch.object(
            text_catalog, "_convert"
        ), mock.patch.object(
            text_catalog, "_transcribe", side_effect=fake_transcribe
        ), mock.patch.object(
            text_catalog.vgmstream, "find", return_value="vgmstream"
        ), mock.patch.object(
            text_catalog.gpt_sovits, "find_install", return_value=self.root / "gpt"
        ):
            catalog = text_catalog.build(
                job, self.root / "data", self.game, force=True
            )

        self.assertEqual(catalog["scopes"]["common"], {"塔台，准备起飞": "通用台词"})
        self.assertEqual(catalog["scopes"]["ground"]["击毁敌方目标"], "陆战台词")
        self.assertIn("补充台词", catalog["scopes"]["ground"])
        status = text_catalog.status(self.root / "data")
        self.assertTrue(status["ready"])
        self.assertEqual(status["scopes"], {"common": 1, "ground": 2})
        job_result = job.snapshot()
        self.assertEqual(job_result["state"], "queued")  # Job 由 manager 标记完成，这里只验证步骤
        self.assertEqual(job_result["current"], 6)

    def test_ensure_reuses_complete_cache(self) -> None:
        data = self.root / "data"
        path = text_catalog.cache_path(data)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"scopes": {"common": {"a": "甲"}, "ground": {"b": "乙"}}}),
            encoding="utf-8",
        )
        with mock.patch.object(text_catalog, "build") as build:
            cached = text_catalog.ensure(None, data, None)
        self.assertEqual(cached["scopes"]["ground"], {"b": "乙"})
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
