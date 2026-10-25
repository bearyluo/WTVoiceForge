from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app import config, packs


def make_game(root: Path) -> Path:
    (root / "win64").mkdir(parents=True)
    (root / "win64" / "aces.exe").write_bytes(b"stub")
    (root / "sound" / "mod").mkdir(parents=True)
    (root / "config.blk").write_text(
        "sound{\n  fmod_sound_enable:b=yes\n  enable_mod:b=no\n}\n", encoding="utf-8"
    )
    return root


def wtvoice_bytes(name: str, banks: dict[str, bytes], manifest: dict | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        payload = manifest if manifest is not None else {
            "schema": 1,
            "format": "wtvoice",
            "name": name,
            "banks": sorted(banks),
            "lines": 3,
        }
        archive.writestr("manifest.json", json.dumps(payload, ensure_ascii=False))
        archive.writestr("README.txt", "install to sound/mod")
        for bank_name, data in banks.items():
            archive.writestr(f"banks/{bank_name}", data)
    return buffer.getvalue()


class PacksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = make_game(self.root / "War Thunder")
        self.data = self.root / "data"
        self.cfg = config.AppConfig(game_path=str(self.game), data_dir=str(self.data))
        self.running = mock.patch.object(packs, "game_running", return_value=False)
        self.running.start()
        self.addCleanup(self.running.stop)

    def upload(self, filename: str, data: bytes) -> dict:
        return packs.receive_upload(self.data, filename, io.BytesIO(data), len(data))

    def install(self, filename="demo.wtvoice", data=None, **kwargs) -> dict:
        result = self.upload(
            filename,
            data
            if data is not None
            else wtvoice_bytes("Demo", {"demo.bank": b"NEW-BANK", "demo.assets.bank": b"NEW-ASSETS"}),
        )
        return packs.install(self.cfg, self.data, result["token"], **kwargs)

    def test_upload_wtvoice_reads_manifest_and_banks(self) -> None:
        result = self.upload(
            "demo.wtvoice",
            wtvoice_bytes("Demo Pack", {"a.bank": b"A", "a.assets.bank": b"AA"}),
        )
        pack = result["pack"]
        self.assertEqual(pack["name"], "Demo Pack")
        self.assertEqual(pack["source_type"], "wtvoice")
        self.assertEqual(pack["bank_count"], 2)
        self.assertEqual([item["name"] for item in pack["banks"]], ["a.assets.bank", "a.bank"])
        self.assertEqual(pack["manifest"]["lines"], 3)
        self.assertTrue((self.data / "packs" / "uploads" / pack["token"] / "pack.json").is_file())

    def test_register_archive_reuses_same_file_and_installs_once(self) -> None:
        path = self.root / "built_20260101.wtvoice"
        path.write_bytes(
            wtvoice_bytes("Quick Build", {"quick.bank": b"QUICK-BANK"})
        )
        first = packs.register_archive(self.data, path)
        second = packs.register_archive(self.data, path)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["token"], second["token"])
        self.assertEqual(second["pack"]["name"], "Quick Build")

        packs.install(self.cfg, self.data, first["token"])
        packs.install(self.cfg, self.data, first["token"])
        installed = packs.list_installed(self.cfg, self.data)
        matching = [
            entry for entry in installed["packs"] if entry["token"] == first["token"]
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual((self.game / "sound" / "mod" / "quick.bank").read_bytes(), b"QUICK-BANK")

    def test_upload_zip_without_manifest_warns(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("nested/loose.bank", b"LOOSE")
        result = self.upload("loose.zip", buffer.getvalue())
        self.assertEqual(result["pack"]["bank_count"], 1)
        self.assertTrue(any("manifest" in item for item in result["pack"]["warnings"]))

    def test_upload_single_bank_keeps_original_name(self) -> None:
        result = self.upload("My Crew.bank", b"SINGLE")
        self.assertEqual(result["pack"]["banks"][0]["name"], "My Crew.bank")
        self.assertEqual(result["pack"]["name"], "My Crew")

    def test_masterbank_requires_explicit_opt_in(self) -> None:
        result = self.upload(
            "wave.wtvoice",
            wtvoice_bytes(
                "W.A.V.E. 风格",
                {
                    "masterbank.bank": b"MB",
                    "masterbank.assets.bank": b"MBA",
                    "crew_dialogs_ground.bank": b"CG",
                    "crew_dialogs_ground.assets.bank": b"CGA",
                },
            ),
        )
        pack = result["pack"]
        self.assertTrue(pack["requires_opt_in"])
        self.assertTrue(pack["wave_like"])
        self.assertTrue(any("masterbank" in item for item in pack["warnings"]))

        with self.assertRaises(packs.PackError):
            packs.install(self.cfg, self.data, result["token"])
        self.assertFalse((self.game / "sound" / "mod" / "masterbank.bank").exists())

        entry = packs.install(self.cfg, self.data, result["token"], allow_masterbank=True)
        self.assertTrue(entry["masterbank"])
        self.assertEqual(
            (self.game / "sound" / "mod" / "masterbank.bank").read_bytes(), b"MB"
        )
        packs.uninstall(self.cfg, self.data, entry["id"])
        self.assertFalse((self.game / "sound" / "mod" / "masterbank.bank").exists())

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../escape.bank", b"X")
        with self.assertRaises(packs.PackError):
            self.upload("escape.zip", buffer.getvalue())
        with self.assertRaises(packs.PackError):
            self.upload("pack.rar", b"X")

    def test_install_backs_up_then_uninstall_restores(self) -> None:
        old = self.game / "sound" / "mod" / "demo.bank"
        old.write_bytes(b"OLD-BANK")
        entry = self.install()
        self.assertEqual(entry["name"], "Demo")
        self.assertEqual(old.read_bytes(), b"NEW-BANK")
        self.assertEqual((self.game / "sound" / "mod" / "demo.assets.bank").read_bytes(), b"NEW-ASSETS")
        record = next(item for item in entry["banks"] if item["name"] == "demo.bank")
        backup = Path(self.data) / record["backup"]
        self.assertEqual(backup.read_bytes(), b"OLD-BANK")

        listed = packs.list_installed(self.cfg, self.data)
        self.assertTrue(listed["valid"])
        self.assertEqual(len(listed["packs"]), 1)
        self.assertTrue(listed["packs"][0]["healthy"])
        self.assertEqual(listed["packs"][0]["banks"][0]["status"], "ok")

        result = packs.uninstall(self.cfg, self.data, entry["id"])
        self.assertEqual(
            result,
            {
                "removed": 2,
                "restored": 1,
                "skipped": 0,
                "missing": 0,
                "config_restored": "",
            },
        )
        self.assertEqual(old.read_bytes(), b"OLD-BANK")
        self.assertFalse((self.game / "sound" / "mod" / "demo.assets.bank").exists())
        self.assertEqual(packs.list_installed(self.cfg, self.data)["packs"], [])

    def test_reinstall_same_pack_keeps_single_record_and_backup(self) -> None:
        old = self.game / "sound" / "mod" / "demo.bank"
        old.write_bytes(b"OLD-BANK")
        result = self.upload(
            "demo.wtvoice",
            wtvoice_bytes("Demo", {"demo.bank": b"NEW-BANK"}),
        )
        first = packs.install(self.cfg, self.data, result["token"])
        record = next(item for item in first["banks"] if item["name"] == "demo.bank")
        original_backup = Path(self.data) / record["backup"]
        self.assertEqual(original_backup.read_bytes(), b"OLD-BANK")

        second = packs.install(self.cfg, self.data, result["token"])
        self.assertNotEqual(first["id"], second["id"])
        listed = packs.list_installed(self.cfg, self.data)
        self.assertEqual(len(listed["packs"]), 1)
        self.assertEqual(listed["packs"][0]["id"], second["id"])
        record = next(item for item in second["banks"] if item["name"] == "demo.bank")
        self.assertEqual((Path(self.data) / record["backup"]).read_bytes(), b"OLD-BANK")

        packs.uninstall(self.cfg, self.data, second["id"])
        self.assertEqual(old.read_bytes(), b"OLD-BANK")

    def test_install_enable_mod_backs_up_config(self) -> None:
        entry = self.install(enable_mod=True)
        config_text = (self.game / "config.blk").read_text(encoding="utf-8")
        self.assertIn("enable_mod:b=yes", config_text)
        self.assertIn("fmod_sound_enable:b=yes", config_text)
        self.assertTrue(entry["config_backup"])
        backup = Path(self.data) / entry["config_backup"]
        self.assertIn("enable_mod:b=no", backup.read_text(encoding="utf-8"))

        packs.set_enable_mod(self.cfg, self.data, False)
        self.assertIn("enable_mod:b=no", (self.game / "config.blk").read_text(encoding="utf-8"))

    def test_install_community_config_flags(self) -> None:
        entry = self.install(
            config={
                "enable_mod": True,
                "fmod_sound_enable": True,
                "use_loops_gun_sound": True,
                "use_loops_gm_gun_sound": False,
            }
        )
        text = (self.game / "config.blk").read_text(encoding="utf-8")
        self.assertIn("enable_mod:b=yes", text)
        self.assertIn("fmod_sound_enable:b=yes", text)
        self.assertIn("useLoopsForGunSound:b=yes", text)
        self.assertIn("useLoopsForGMGunSound:b=no", text)
        self.assertEqual(
            entry["config_flags"],
            {
                "enable_mod": True,
                "fmod_sound_enable": True,
                "useLoopsForGunSound": True,
                "useLoopsForGMGunSound": False,
            },
        )
        listed = packs.list_installed(self.cfg, self.data)
        self.assertEqual(listed["config"]["enable_mod"], True)
        self.assertEqual(listed["config"]["fmod_sound_enable"], True)
        self.assertEqual(listed["config"]["use_loops_gun_sound"], True)

    def test_layout_pair_and_rar_diagnostics(self) -> None:
        sound_mod = io.BytesIO()
        with zipfile.ZipFile(sound_mod, "w") as archive:
            archive.writestr("sound/mod/crew.bank", b"META")
            archive.writestr("sound/mod/crew.assets.bank", b"ASSETS")
            archive.writestr("README.txt", "community layout")
        parsed = self.upload("community.zip", sound_mod.getvalue())["pack"]
        self.assertEqual(parsed["layout"], "sound_mod")
        self.assertTrue(any("sound/mod" in item for item in parsed["warnings"]))
        self.assertEqual(parsed["extra_files"], 0)

        flat = io.BytesIO()
        with zipfile.ZipFile(flat, "w") as archive:
            archive.writestr("only.assets.bank", b"ASSETS")
        parsed = self.upload("flat.zip", flat.getvalue())["pack"]
        self.assertEqual(parsed["layout"], "flat")
        self.assertTrue(any("缺少同名 only.bank" in item for item in parsed["warnings"]))

        with self.assertRaises(packs.PackError) as caught:
            self.upload("pack.7z", b"7z")
        self.assertIn("压缩包", str(caught.exception))

    def test_stale_and_risky_banks(self) -> None:
        self.install()
        base = self.game / "sound" / "demo.bank"
        base.write_bytes(b"GAME-UPDATE")
        future = time.time() + 60
        os.utime(base, (future, future))
        listed = packs.list_installed(self.cfg, self.data)
        record = next(item for item in listed["packs"][0]["banks"] if item["name"] == "demo.bank")
        self.assertTrue(record["stale"])

        (self.game / "sound" / "mod" / "masterbank.bank").write_bytes(b"RISKY")
        (self.game / "sound" / "mod" / "hangar.bank").write_bytes(b"OLD")
        listed = packs.list_installed(self.cfg, self.data)
        risky = {item["name"] for item in listed["risky"]}
        self.assertEqual(risky, {"masterbank.bank", "hangar.bank"})

    def test_install_refuses_running_game(self) -> None:
        result = self.upload("demo.wtvoice", wtvoice_bytes("Demo", {"demo.bank": b"X"}))
        with mock.patch.object(packs, "game_running", return_value=True):
            with self.assertRaises(packs.PackError):
                packs.install(self.cfg, self.data, result["token"])
            with self.assertRaises(packs.PackError):
                packs.set_enable_mod(self.cfg, self.data, True)
        self.assertFalse((self.game / "sound" / "mod" / "demo.bank").exists())

    def test_overlapping_packs_must_uninstall_newest_first(self) -> None:
        old = self.game / "sound" / "mod" / "demo.bank"
        old.write_bytes(b"ORIGINAL")
        first_upload = self.upload("a.wtvoice", wtvoice_bytes("A", {"demo.bank": b"PACK-A"}))
        first = packs.install(self.cfg, self.data, first_upload["token"])
        second_upload = self.upload("b.wtvoice", wtvoice_bytes("B", {"demo.bank": b"PACK-B"}))
        second = packs.install(self.cfg, self.data, second_upload["token"])
        self.assertEqual(old.read_bytes(), b"PACK-B")

        with self.assertRaises(packs.PackError):
            packs.uninstall(self.cfg, self.data, first["id"])

        result = packs.uninstall(self.cfg, self.data, second["id"])
        self.assertEqual(result["restored"], 1)
        self.assertEqual(old.read_bytes(), b"PACK-A")

        result = packs.uninstall(self.cfg, self.data, first["id"])
        self.assertEqual(result["restored"], 1)
        self.assertEqual(old.read_bytes(), b"ORIGINAL")
        self.assertEqual(packs.list_installed(self.cfg, self.data)["packs"], [])

    def test_uninstall_skips_modified_bank(self) -> None:
        entry = self.install()
        target = self.game / "sound" / "mod" / "demo.bank"
        target.write_bytes(b"CHANGED")
        result = packs.uninstall(self.cfg, self.data, entry["id"])
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(target.read_bytes(), b"CHANGED")
        self.assertEqual(packs.list_installed(self.cfg, self.data)["packs"], [])

    def test_inspect_reports_conflicts(self) -> None:
        (self.game / "sound" / "demo.bank").write_bytes(b"BASE")
        result = self.upload("demo.wtvoice", wtvoice_bytes("Demo", {"demo.bank": b"X"}))
        info = packs.inspect_game(self.cfg, self.data, packs.load_pack(self.data, result["token"]))
        self.assertTrue(info["valid"])
        self.assertFalse(info["running"])
        self.assertEqual(info["enable_mod"], False)
        self.assertEqual(
            info["conflicts"],
            [
                {
                    "name": "demo.bank",
                    "base_present": True,
                    "mod_present": False,
                    "mod_same": False,
                    "owner": "",
                    "owner_name": "",
                }
            ],
        )

    def test_inspect_reports_owning_pack(self) -> None:
        first = self.install()
        upload = self.upload(
            "b.wtvoice",
            wtvoice_bytes("Pack B", {"demo.bank": b"PACK-B"}),
        )
        info = packs.inspect_game(self.cfg, self.data, packs.load_pack(self.data, upload["token"]))
        conflict = info["conflicts"][0]
        self.assertEqual(conflict["owner"], first["id"])
        self.assertEqual(conflict["owner_name"], "Demo")
        self.assertTrue(conflict["mod_present"])

    def test_restore_all_uninstalls_newest_first_and_restores_config(self) -> None:
        original = self.game / "sound" / "mod" / "demo.bank"
        original.write_bytes(b"ORIGINAL")
        first_upload = self.upload("a.wtvoice", wtvoice_bytes("A", {"demo.bank": b"PACK-A"}))
        packs.install(self.cfg, self.data, first_upload["token"], enable_mod=True)
        second_upload = self.upload("b.wtvoice", wtvoice_bytes("B", {"demo.bank": b"PACK-B"}))
        packs.install(self.cfg, self.data, second_upload["token"], enable_mod=True)
        self.assertEqual(original.read_bytes(), b"PACK-B")
        self.assertIn("enable_mod:b=yes", (self.game / "config.blk").read_text(encoding="utf-8"))

        result = packs.restore_all(self.cfg, self.data)

        self.assertEqual(result["removed_packs"], 2)
        self.assertEqual(result["remaining_packs"], 0)
        self.assertEqual(result["removed"], 2)
        self.assertEqual(result["restored"], 2)
        self.assertTrue(result["config_restored"])
        self.assertEqual(original.read_bytes(), b"ORIGINAL")
        self.assertIn("enable_mod:b=no", (self.game / "config.blk").read_text(encoding="utf-8"))
        self.assertEqual(packs.list_installed(self.cfg, self.data)["packs"], [])

    def test_discard_upload_and_guard_installed(self) -> None:
        result = self.upload("demo.wtvoice", wtvoice_bytes("Demo", {"demo.bank": b"X"}))
        token = result["token"]
        self.assertTrue(packs.discard_upload(self.data, token)["discarded"])
        with self.assertRaises(packs.PackError):
            packs.load_pack(self.data, token)

        entry = self.install()
        with self.assertRaises(packs.PackError):
            packs.discard_upload(self.data, entry["token"])

    def test_pack_contents_lists_streams_and_reuses_cache(self) -> None:
        manifest = {
            "format": "wtvoice",
            "name": "Content Demo",
            "mapping": [
                {"event": "enemy_destroyed", "bank": "demo.bank", "text": "击毁目标"},
            ],
        }
        result = self.upload(
            "content.wtvoice",
            wtvoice_bytes("Content Demo", {"demo.bank": b"BANK"}, manifest),
        )
        streams = [
            {"index": 1, "name": "enemy_destroyed", "duration_sec": 1.2},
            {"index": 2, "name": "enemy_destroyed", "duration_sec": 1.0},
        ]
        with mock.patch.object(
            packs.localized, "game_streams", return_value=streams
        ) as reader:
            data = packs.contents(self.data, result["token"])
            self.assertEqual(data["lines_total"], 1)
            self.assertEqual(data["lines"][0]["text"], "击毁目标")
            self.assertEqual(data["banks"][0]["stream_total"], 2)
            self.assertEqual(data["banks"][0]["event_total"], 1)
            cached = list((self.data / "cache" / "pack_contents").glob("*.json"))
            self.assertEqual(len(cached), 1)
            again = packs.contents(self.data, result["token"])
            self.assertEqual(again["banks"][0]["stream_total"], 2)
            self.assertEqual(reader.call_count, 1)

    def test_conflicts_report_bank_owners_without_merging(self) -> None:
        self.install()
        result = self.upload(
            "other.wtvoice",
            wtvoice_bytes("Other", {"demo.bank": b"OTHER-BANK"}),
        )
        report = packs.conflicts(self.data, result["token"])
        self.assertFalse(report["merge_supported"])
        self.assertEqual(report["overwrite_count"], 1)
        self.assertEqual(report["banks"][0]["bank"], "demo.bank")
        self.assertEqual(report["banks"][0]["owners"][0]["pack"], "Demo")

        same = self.upload(
            "same.wtvoice",
            wtvoice_bytes("Same", {"demo.bank": b"NEW-BANK"}),
        )
        report = packs.conflicts(self.data, same["token"])
        self.assertEqual(report["shared_count"], 1)
        self.assertEqual(report["overwrite_count"], 0)

        packs.install(self.cfg, self.data, result["token"])
        report = packs.conflicts(self.data)
        self.assertEqual(len(report["banks"]), 1)
        self.assertEqual(len(report["banks"][0]["owners"]), 2)

    def test_uninstall_without_game_path(self) -> None:
        entry = self.install()
        cfg = config.AppConfig(game_path="", data_dir=str(self.data))
        with self.assertRaises(packs.PackError):
            packs.uninstall(cfg, self.data, entry["id"])


class ArchiveImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.archive = self.root / "pack.7z"
        self.archive.write_bytes(b"7z")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, entries: dict[str, bytes]):
        listing = "\n\n".join(
            f"Path = {name}\nSize = {len(data)}\nAttributes = A\nEncrypted = -"
            for name, data in entries.items()
        )

        def fake_run(command, **kwargs):
            if "l" in command:
                return subprocess.CompletedProcess(command, 0, listing, "")
            target = Path(next(item for item in command if str(item).startswith("-o"))[2:])
            for name, data in entries.items():
                path = target / name.replace("\\", "/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return subprocess.CompletedProcess(command, 0, "", "")

        return fake_run

    def test_normalizes_7z_to_zip(self) -> None:
        entries = {
            "sound/mod/readme.txt": b"hi",
            "sound/mod/demo.bank": b"BANK",
            "sound/mod/demo.assets.bank": b"ASSETS",
            "sound/mod/setup.exe": b"ignored",
        }
        with mock.patch.object(packs, "_seven_zip", return_value=Path("7z.exe")), \
             mock.patch.object(packs.subprocess, "run", side_effect=self._run(entries)):
            normalized, ignored = packs._normalize_archive(
                self.archive, self.root / "work"
            )
        self.assertEqual(ignored, 1)
        with zipfile.ZipFile(normalized) as archive:
            names = set(archive.namelist())
        self.assertIn("sound/mod/demo.bank", names)
        self.assertIn("sound/mod/demo.assets.bank", names)
        self.assertIn("sound/mod/readme.txt", names)
        self.assertNotIn("sound/mod/setup.exe", names)

    def test_rejects_traversal_and_encryption(self) -> None:
        def fake_run(command, **kwargs):
            listing = (
                "Path = ..\\evil.bank\nSize = 4\nAttributes = A\nEncrypted = -"
            )
            return subprocess.CompletedProcess(command, 0, listing, "")

        with mock.patch.object(packs, "_seven_zip", return_value=Path("7z.exe")), \
             mock.patch.object(packs.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(packs.PackError):
                packs._normalize_archive(self.archive, self.root / "work")

        def encrypted(command, **kwargs):
            listing = (
                "Path = demo.bank\nSize = 4\nAttributes = A\nEncrypted = +"
            )
            return subprocess.CompletedProcess(command, 0, listing, "")

        with mock.patch.object(packs, "_seven_zip", return_value=Path("7z.exe")), \
             mock.patch.object(packs.subprocess, "run", side_effect=encrypted):
            with self.assertRaises(packs.PackError):
                packs._normalize_archive(self.archive, self.root / "work2")


if __name__ == "__main__":
    unittest.main()
