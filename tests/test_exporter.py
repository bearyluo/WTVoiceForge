from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import (
    dynamic_bank,
    event_override,
    exporter,
    kit_compat,
    localized,
    voice,
)
from tests.test_localized import write_bank
from tests.test_voice import make_wav


CATALOG = {
    "events": [
        {
            "event_id": "evt-1",
            "event": "enemy_destroyed",
            "bank": "aircraft_gui",
            "asset_guids": ["g1"],
        }
    ],
    "assets": {"g1": {"path": "gui/enemy_destroyed.wav"}},
}

# 与真实工程一致：enemy_destr_by_plane 和 obj_complete 共用同一个 AudioFile。
SHARED_GUID = "3379784f-2bda-415d-aa8e-1d026a43f308"
PLANE_EVENT_FILE = "{99d19487-6854-4dee-8ad5-2fa49ca03944}.xml"
OBJ_EVENT_FILE = "{7489539e-a6f2-4d2b-92c2-83e7a7940b22}.xml"


def event_xml(asset_guid: str, length: float) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<objects serializationModel="Studio.02.02.00">'
        '<object class="Event" id="{00000000-0000-0000-0000-000000000001}"/>'
        '<object class="SingleSound" id="{00000000-0000-0000-0000-000000000002}">'
        f'<property name="length"><value>{length}</value></property>'
        f'<relationship name="audioFile"><destination>{{{asset_guid}}}</destination></relationship>'
        "</object></objects>"
    )


def audio_file_xml(guid: str, asset_path: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<objects serializationModel="Studio.02.02.00">'
        f'<object class="AudioFile" id="{{{guid}}}">'
        f'<property name="assetPath"><value>{asset_path}</value></property>'
        '<property name="frequencyInKHz"><value>44.0999985</value></property>'
        '<property name="channelCount"><value>2</value></property>'
        '<property name="length"><value>1.7684353741496599</value></property>'
        '<relationship name="masterAssetFolder">'
        "<destination>{81dc2e2f-1fe8-4962-9976-ac27d7c0b961}</destination>"
        "</relationship></object></objects>"
    )


def event_reference(path: Path) -> tuple[str, str]:
    """返回事件 XML 里的 (SingleSound.length, audioFile destination)。"""
    root = ET.parse(path).getroot()
    length = ""
    destination = ""
    for obj in root.iter("object"):
        if obj.get("class") != "SingleSound":
            continue
        value = obj.find("property[@name='length']/value")
        relation = obj.find("relationship[@name='audioFile']")
        length = (value.text or "") if value is not None else ""
        destination = (relation.findtext("destination") or "") if relation is not None else ""
    return length.strip(), destination.strip()


class FakeJob:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self.percent = 0
        self.messages: list[str] = []

    def update(self, **fields) -> None:
        if fields.get("percent") is not None:
            self.percent = fields["percent"]
        if fields.get("message"):
            self.messages.append(fields["message"])


class FakePopen:
    """代替 FMOD CLI：按命令里的 -banks 参数写出 bank 文件。"""

    written = True

    def __init__(self, command, **kwargs) -> None:
        self.cwd = Path(kwargs["cwd"])
        self.returncode = 0
        if FakePopen.written:
            banks = command[command.index("-banks") + 1]
            sound = self.cwd / "Build" / "sound"
            sound.mkdir(parents=True, exist_ok=True)
            for bank in banks.split(","):
                (sound / f"{bank}.bank").write_bytes(b"bank")
                (sound / f"{bank}.assets.bank").write_bytes(b"assets")
                (sound / f"{bank}.strings.bank").write_bytes(b"strings")
        self.stdout = iter([b"FMOD build output\n"])

    def wait(self) -> int:
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


class ExporterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.project = voice.create_project(self.data, "测试语音包")

        self.kit = self.root / "kit"
        self.kit.mkdir()
        (self.kit / "sounds.fspro").write_text("{}", encoding="utf-8")
        asset = self.kit / "Assets" / "gui" / "enemy_destroyed.wav"
        make_wav(asset, 0.4)
        (self.kit / "Metadata").mkdir()
        (self.kit / "Metadata" / "Event.xml").write_text("<event/>", encoding="utf-8")

        self.cli = self.root / "fmodstudiocl.exe"
        self.cli.write_bytes(b"")
        self.cfg = SimpleNamespace(
            fmod_studio_path=str(self.cli),
            kit_path=str(self.kit),
            data_dir=str(self.data),
            game_path="",
        )
        self.line = self.add_line()

        self.patches = [
            mock.patch.object(exporter.config, "load_config", lambda: self.cfg),
            mock.patch.object(exporter, "_catalog", lambda _data: CATALOG),
            mock.patch.object(
                exporter,
                "_copy_tree",
                lambda src, dst: shutil.copytree(src, dst, dirs_exist_ok=True),
            ),
            mock.patch.object(exporter.subprocess, "Popen", FakePopen),
        ]
        for patch in self.patches:
            patch.start()
        FakePopen.written = True
        localized.clear_cache()

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        localized.clear_cache()
        self.tmp.cleanup()

    def add_line(self, event_id: str = "evt-1", audio: bool = True) -> dict:
        result = voice.add_line(
            self.data,
            self.project["id"],
            "击毁目标",
            source={
                "event_id": event_id,
                "event": "enemy_destroyed",
                "bank": "aircraft_gui",
                "assets": [{"guid": "g1", "path": "gui/enemy_destroyed.wav"}],
            },
        )
        line_id = result["line"]["id"]
        if audio:
            out = voice.line_audio_path(self.data, self.project["id"], line_id)
            make_wav(out, 1.0)
            voice.mark_line(
                self.data,
                self.project["id"],
                line_id,
                status="ready",
                audio=f"audio/{line_id}.wav",
                duration_sec=1.0,
            )
        return result["line"]

    def load_project(self) -> dict:
        return voice.load_project(self.data, self.project["id"])

    def test_readiness(self) -> None:
        project = self.load_project()
        ready = exporter.readiness(project)
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["ready_lines"], 1)
        self.assertEqual(ready["banks"], [{"name": "aircraft_gui", "lines": 1}])
        self.assertEqual(ready["fmod"]["version"], self.root.name)

        with mock.patch.object(exporter, "_cli", lambda: None):
            missing = exporter.readiness(project)
        self.assertFalse(missing["ready"])
        self.assertIn("FMOD", missing["reason"])

    def test_build_package_and_replacement(self) -> None:
        job = FakeJob()
        result = exporter.build(job, self.data, self.project["id"])
        package = Path(result["export"])
        self.assertTrue(package.is_file())
        self.assertEqual(result["lines"], 1)
        self.assertEqual(
            sorted(result["files"]),
            ["aircraft_gui.assets.bank", "aircraft_gui.bank", "aircraft_gui.strings.bank"],
        )

        with zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("README.txt", names)
            self.assertIn("sound/mod/aircraft_gui.bank", names)
            self.assertIn("sound/mod/aircraft_gui.assets.bank", names)
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        self.assertEqual(manifest["format"], "wtvoice")
        self.assertEqual(manifest["banks"], result["files"])
        self.assertEqual(manifest["mapping"][0]["event_id"], "evt-1")
        self.assertEqual(manifest["mapping"][0]["target_file"], "enemy_destroyed.wav")
        self.assertEqual(manifest["install"]["target"], "War Thunder/sound/mod")
        self.assertEqual(manifest["install"]["layout"], "sound/mod")
        self.assertTrue(manifest["config"]["enable_mod"])
        self.assertTrue(manifest["config"]["fmod_sound_enable"])

        replaced = (
            self.data
            / "voice_projects"
            / self.project["id"]
            / "build"
            / "assets"
            / "gui"
            / "enemy_destroyed.wav"
        )
        self.assertEqual(replaced.read_bytes(), voice.line_audio_path(
            self.data, self.project["id"], self.line["id"]
        ).read_bytes())

        export = self.load_project()["export"]
        self.assertEqual(export["name"], package.name)
        self.assertEqual(export["lines"], 1)
        base = self.data / "voice_projects" / self.project["id"] / "build" / "base"
        self.assertTrue((base / ".ready.json").is_file())
        self.assertTrue((base / "Metadata" / "Event.xml").is_file())

    def test_build_skips_unbound_lines(self) -> None:
        added = voice.add_line(self.data, self.project["id"], "手动台词", event="manual")
        manual = added["line"]
        make_wav(
            voice.line_audio_path(self.data, self.project["id"], manual["id"]),
            0.3,
        )
        voice.mark_line(
            self.data,
            self.project["id"],
            manual["id"],
            status="ready",
            audio=f"audio/{manual['id']}.wav",
        )
        result = exporter.build(FakeJob(), self.data, self.project["id"])
        self.assertEqual(result["skipped_unbound"], 1)
        self.assertEqual(result["lines"], 1)

    def test_build_accepts_relative_data_dir(self) -> None:
        previous = os.getcwd()
        os.chdir(self.root)
        try:
            result = exporter.build(FakeJob(), "data", self.project["id"])
            self.assertTrue(Path(result["export"]).is_absolute())
            self.assertTrue(Path(result["export"]).is_file())
        finally:
            os.chdir(previous)

    def test_build_localized_audio_table(self) -> None:
        bank = "_crew_dialogs_ground_en_us"
        write_bank(
            self.kit,
            "33333333-3333-3333-3333-333333333333",
            bank,
            "Assets/crew/en_us",
        )
        source_rel = "crew/en_us/med/commander/voice_message_test.wav"
        make_wav(self.kit / "Assets" / source_rel, 0.4)
        localized.clear_cache()
        project = voice.create_project(self.data, "本地化验证")
        added = voice.add_line(
            self.data,
            project["id"],
            "测试台词",
            source={
                "event_id": f"at:{bank}:voice_message_test",
                "event": "voice_message_test",
                "bank": bank,
                "assets": [{"guid": "", "path": source_rel, "file": "voice_message_test.wav"}],
                "localized": True,
            },
        )
        line_id = added["line"]["id"]
        make_wav(voice.line_audio_path(self.data, project["id"], line_id), 1.0)
        voice.mark_line(
            self.data,
            project["id"],
            line_id,
            status="ready",
            audio=f"audio/{line_id}.wav",
        )

        result = exporter.build(FakeJob(), self.data, project["id"])
        self.assertIn(f"{bank}.assets.bank", result["files"])
        base = self.data / "voice_projects" / project["id"] / "build" / "base"
        staged = base / "Assets" / source_rel
        self.assertTrue(staged.is_file())
        self.assertEqual(
            staged.read_bytes(),
            voice.line_audio_path(self.data, project["id"], line_id).read_bytes(),
        )
        with zipfile.ZipFile(result["export"]) as archive:
            self.assertIn(f"sound/mod/{bank}.assets.bank", archive.namelist())

    def test_build_dynamic_game_bank(self) -> None:
        bank = "_crew_dialogs_ground_fr"
        game = self.root / "game"
        (game / "sound").mkdir(parents=True)
        (game / "sound" / f"{bank}.assets.bank").write_bytes(b"game-bank")
        self.cfg.game_path = str(game)

        project = voice.create_project(self.data, "动态语音表")
        source_rel = dynamic_bank.source_rel(bank) + "/3/voice_message_test.wav"
        added = voice.add_line(
            self.data,
            project["id"],
            "",
            source={
                "event_id": f"at:{bank}:voice_message_test",
                "event": "voice_message_test",
                "bank": bank,
                "localized": True,
                "game_only": True,
                "assets": [
                    {
                        "guid": "",
                        "path": source_rel,
                        "file": "voice_message_test.wav",
                        "game_index": 3,
                    }
                ],
            },
        )
        line_id = added["line"]["id"]
        make_wav(voice.line_audio_path(self.data, project["id"], line_id), 1.0)
        voice.mark_line(
            self.data,
            project["id"],
            line_id,
            status="ready",
            audio=f"audio/{line_id}.wav",
        )

        def fake_extract(assets_dir, name, _game, job=None, data_dir=None):
            make_wav(Path(assets_dir) / source_rel, 0.4)
            return 1

        with mock.patch.object(
            exporter.dynamic_bank, "extract_originals", side_effect=fake_extract
        ):
            result = exporter.build(FakeJob(), self.data, project["id"])

        self.assertIn(f"{bank}.assets.bank", result["files"])
        base = self.data / "voice_projects" / project["id"] / "build" / "base"
        metadata = list((base / "Metadata" / "Bank").glob("*.xml"))
        self.assertTrue(
            any(bank in path.read_text(encoding="utf-8") for path in metadata),
            "构建工程里应补上动态 AudioTable",
        )
        with zipfile.ZipFile(result["export"]) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["dynamic_banks"], [bank])
            self.assertIn(f"sound/mod/{bank}.assets.bank", archive.namelist())

    def test_build_refreshes_cached_metadata_after_kit_update(self) -> None:
        exporter.build(FakeJob(), self.data, self.project["id"])
        base = self.data / "voice_projects" / self.project["id"] / "build" / "base"
        added = self.kit / "Metadata" / "Event" / "{new-event}.xml"
        added.parent.mkdir(parents=True, exist_ok=True)
        added.write_text("<event/>", encoding="utf-8")
        self.assertFalse((base / "Metadata" / "Event" / added.name).is_file())

        exporter.build(FakeJob(), self.data, self.project["id"])
        self.assertTrue((base / "Metadata" / "Event" / added.name).is_file())

    def test_build_restores_old_kit_kill_assist(self) -> None:
        def fake_extract(_bank, _index, target: Path) -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"kill")
            return target

        self.cfg.game_path = str(self.root / "game")
        with mock.patch.object(
            kit_compat.localized,
            "game_bank_path",
            return_value=Path("aircraft_gui.assets.bank"),
        ), mock.patch.object(
            kit_compat.localized,
            "game_streams",
            return_value=[{"index": 163, "name": "ui_kill_assist", "duration_sec": 2.0}],
        ), mock.patch.object(
            kit_compat.localized, "extract_stream", side_effect=fake_extract
        ):
            result = exporter.build(FakeJob(), self.data, self.project["id"])

        self.assertEqual(
            sorted(result["compat"]["restored"]), ["audio", "audio_meta", "event"]
        )
        base = self.data / "voice_projects" / self.project["id"] / "build" / "base"
        self.assertTrue(
            (base / "Metadata" / "Event" / kit_compat.KILL_EVENT_FILE).is_file()
        )
        self.assertTrue(
            (base / "Metadata" / "AudioFile" / kit_compat.KILL_AUDIO_FILE).is_file()
        )
        asset = (
            self.data
            / "voice_projects"
            / self.project["id"]
            / "build"
            / "assets"
            / kit_compat.KILL_ASSET
        )
        self.assertEqual(asset.read_bytes(), b"kill")

    def _shared_catalog(self) -> dict:
        make_wav(self.kit / "Assets" / "gui" / "obj_complete.wav", 0.3)
        audio_meta = self.kit / "Metadata" / "AudioFile"
        audio_meta.mkdir(parents=True, exist_ok=True)
        (audio_meta / f"{{{SHARED_GUID}}}.xml").write_text(
            audio_file_xml(SHARED_GUID, "gui/obj_complete.wav"), encoding="utf-8"
        )
        event_meta = self.kit / "Metadata" / "Event"
        event_meta.mkdir(parents=True, exist_ok=True)
        (event_meta / PLANE_EVENT_FILE).write_text(
            event_xml(SHARED_GUID, 1.8), encoding="utf-8"
        )
        (event_meta / OBJ_EVENT_FILE).write_text(
            event_xml(SHARED_GUID, 1.8), encoding="utf-8"
        )
        return {
            "events": [
                {
                    "event_id": "fx:aircraft_gui:obj_complete",
                    "event": "obj_complete",
                    "bank": "aircraft_gui",
                    "source_file": OBJ_EVENT_FILE,
                    "asset_guids": [SHARED_GUID],
                },
                {
                    "event_id": "fx:aircraft_gui:enemy_destr_by_plane",
                    "event": "enemy_destr_by_plane",
                    "bank": "aircraft_gui",
                    "source_file": PLANE_EVENT_FILE,
                    "asset_guids": [SHARED_GUID],
                },
            ],
            "assets": {
                SHARED_GUID: {
                    "path": "gui/obj_complete.wav",
                    "length": 1.76843537414966,
                    "channels": 2,
                    "rate_khz": 44.1,
                    "exists": True,
                    "size_bytes": 0,
                    "missing_metadata": False,
                }
            },
        }

    def _add_line(self, project_id: str, event: str, asset: str, text: str, seconds: float) -> dict:
        added = voice.add_line(
            self.data,
            project_id,
            text,
            source={
                "event_id": f"fx:aircraft_gui:{event}",
                "event": event,
                "bank": "aircraft_gui",
                "assets": [{"guid": "", "path": asset}],
            },
        )
        line = added["line"]
        make_wav(voice.line_audio_path(self.data, project_id, line["id"]), seconds)
        voice.mark_line(
            self.data,
            project_id,
            line["id"],
            status="ready",
            audio=f"audio/{line['id']}.wav",
            duration_sec=seconds,
        )
        return line

    def _base(self, project_id: str) -> Path:
        return self.data / "voice_projects" / project_id / "build" / "base"

    def _built_assets(self, project_id: str) -> Path:
        return self.data / "voice_projects" / project_id / "build" / "assets"

    def test_build_isolates_plane_kill_shared_audio(self) -> None:
        shared = self._shared_catalog()
        project = voice.create_project(self.data, "空战击杀隔离")
        line = self._add_line(
            project["id"], "enemy_destr_by_plane", "gui/obj_complete.wav", "击落敌机", 1.0
        )
        original_audio = (self.kit / "Assets" / "gui" / "obj_complete.wav").read_bytes()
        with mock.patch.object(exporter, "_catalog", lambda _data: shared):
            result = exporter.build(FakeJob(), self.data, project["id"])

        cloned = event_override.clone_audio_guid(SHARED_GUID)
        isolation = result["isolation"]
        self.assertTrue(isolation["applied"])
        self.assertEqual(isolation["original_guid"], SHARED_GUID)
        self.assertEqual(isolation["cloned_guid"], cloned)

        base = self._base(project["id"])
        target_length, target_ref = event_reference(base / "Metadata" / "Event" / PLANE_EVENT_FILE)
        self.assertEqual(target_ref, f"{{{cloned}}}")
        self.assertEqual(target_length, "1.000000")
        obj_length, obj_ref = event_reference(base / "Metadata" / "Event" / OBJ_EVENT_FILE)
        self.assertEqual(obj_ref, f"{{{SHARED_GUID}}}")
        self.assertEqual(obj_length, "1.8")

        cloned_meta = base / "Metadata" / "AudioFile" / f"{{{cloned}}}.xml"
        self.assertTrue(cloned_meta.is_file())
        root = ET.parse(cloned_meta).getroot()
        audio = next(obj for obj in root.iter("object") if obj.get("class") == "AudioFile")
        self.assertEqual(audio.get("id"), f"{{{cloned}}}")
        self.assertEqual(
            audio.findtext("property[@name='assetPath']/value"),
            "gui/wtvf_enemy_destr_by_plane.wav",
        )
        untouched = ET.parse(base / "Metadata" / "AudioFile" / f"{{{SHARED_GUID}}}.xml")
        self.assertEqual(
            untouched.getroot().findtext("object[@class='AudioFile']/property[@name='assetPath']/value"),
            "gui/obj_complete.wav",
        )

        built = self._built_assets(project["id"])
        self.assertEqual((built / "gui" / "obj_complete.wav").read_bytes(), original_audio)
        self.assertEqual(
            (built / "gui" / "wtvf_enemy_destr_by_plane.wav").read_bytes(),
            voice.line_audio_path(self.data, project["id"], line["id"]).read_bytes(),
        )

        # 官方工程不被写：原事件仍指向原 GUID、原长度，也没有克隆 AudioFile。
        kit_length, kit_ref = event_reference(self.kit / "Metadata" / "Event" / PLANE_EVENT_FILE)
        self.assertEqual(kit_ref, f"{{{SHARED_GUID}}}")
        self.assertEqual(kit_length, "1.8")
        self.assertEqual(
            sorted(item.name for item in (self.kit / "Metadata" / "AudioFile").glob("*.xml")),
            [f"{{{SHARED_GUID}}}.xml"],
        )

        # 本次 catalog 副本用于构建，不能污染调用方传入的 catalog。
        self.assertEqual(shared["events"][1]["asset_guids"], [SHARED_GUID])
        self.assertNotIn(cloned, shared["assets"])

        # 连续构建：同一克隆 GUID，克隆 AudioFile 不随次数增长。
        with mock.patch.object(exporter, "_catalog", lambda _data: shared):
            again = exporter.build(FakeJob(), self.data, project["id"])
        self.assertEqual(again["isolation"]["cloned_guid"], cloned)
        self.assertEqual(
            sorted(item.name for item in (base / "Metadata" / "AudioFile").glob("*.xml")),
            sorted([f"{{{SHARED_GUID}}}.xml", f"{{{cloned}}}.xml"]),
        )
        self.assertEqual(
            event_reference(base / "Metadata" / "Event" / PLANE_EVENT_FILE)[1],
            f"{{{cloned}}}",
        )

    def test_build_missing_plane_kill_event_errors(self) -> None:
        shared = self._shared_catalog()
        del shared["events"][1]
        project = voice.create_project(self.data, "缺少目标事件")
        self._add_line(
            project["id"], "enemy_destr_by_plane", "gui/obj_complete.wav", "击落敌机", 1.0
        )
        with mock.patch.object(exporter, "_catalog", lambda _data: shared):
            with self.assertRaises(exporter.ExportError) as caught:
                exporter.build(FakeJob(), self.data, project["id"])
        self.assertIn("enemy_destr_by_plane", str(caught.exception))

    def test_build_keeps_obj_complete_when_plane_kill_also_selected(self) -> None:
        shared = self._shared_catalog()
        project = voice.create_project(self.data, "同包普通流")
        obj_line = self._add_line(
            project["id"], "obj_complete", "gui/obj_complete.wav", "任务完成", 1.25
        )
        plane_line = self._add_line(
            project["id"], "enemy_destr_by_plane", "gui/obj_complete.wav", "击落敌机", 0.75
        )
        with mock.patch.object(exporter, "_catalog", lambda _data: shared):
            result = exporter.build(FakeJob(), self.data, project["id"])

        cloned = event_override.clone_audio_guid(SHARED_GUID)
        self.assertTrue(result["isolation"]["applied"])
        base = self._base(project["id"])
        obj_length, obj_ref = event_reference(base / "Metadata" / "Event" / OBJ_EVENT_FILE)
        plane_length, plane_ref = event_reference(
            base / "Metadata" / "Event" / PLANE_EVENT_FILE
        )
        self.assertEqual(obj_ref, f"{{{SHARED_GUID}}}")
        self.assertEqual(obj_length, "1.250000")
        self.assertEqual(plane_ref, f"{{{cloned}}}")
        self.assertEqual(plane_length, "0.750000")

        built = self._built_assets(project["id"])
        self.assertEqual(
            (built / "gui" / "obj_complete.wav").read_bytes(),
            voice.line_audio_path(self.data, project["id"], obj_line["id"]).read_bytes(),
        )
        self.assertEqual(
            (built / "gui" / "wtvf_enemy_destr_by_plane.wav").read_bytes(),
            voice.line_audio_path(self.data, project["id"], plane_line["id"]).read_bytes(),
        )

    def test_build_rejects_plane_kill_with_unexpected_asset(self) -> None:
        shared = self._shared_catalog()
        project = voice.create_project(self.data, "错误素材")
        self._add_line(
            project["id"], "enemy_destr_by_plane", "gui/enemy_destroyed.wav", "击落敌机", 1.0
        )
        with mock.patch.object(exporter, "_catalog", lambda _data: shared):
            with self.assertRaises(exporter.ExportError) as caught:
                exporter.build(FakeJob(), self.data, project["id"])
        self.assertIn("obj_complete", str(caught.exception))

    def test_build_fails_without_target_bank(self) -> None:
        FakePopen.written = False
        with self.assertRaises(exporter.ExportError):
            exporter.build(FakeJob(), self.data, self.project["id"])

    def test_build_rejects_unsafe_asset_path(self) -> None:
        added = voice.add_line(
            self.data,
            self.project["id"],
            "越界路径",
            source={
                "event_id": "evt-bad",
                "event": "bad",
                "bank": "aircraft_gui",
                "assets": [{"guid": "g9", "path": "../../outside.wav"}],
            },
        )
        line = added["line"]
        make_wav(voice.line_audio_path(self.data, self.project["id"], line["id"]), 0.5)
        voice.mark_line(
            self.data,
            self.project["id"],
            line["id"],
            status="ready",
            audio=f"audio/{line['id']}.wav",
        )
        with self.assertRaises(exporter.ExportError):
            exporter.build(FakeJob(), self.data, self.project["id"])

    def test_build_rejects_shared_asset(self) -> None:
        voice.add_line(
            self.data,
            self.project["id"],
            "第二个事件",
            source={
                "event_id": "evt-2",
                "event": "enemy_destroyed_2",
                "bank": "aircraft_gui",
                "assets": [{"guid": "g1", "path": "gui/enemy_destroyed.wav"}],
            },
        )
        second = self.load_project()["lines"][1]
        make_wav(voice.line_audio_path(self.data, self.project["id"], second["id"]), 0.5)
        voice.mark_line(
            self.data,
            self.project["id"],
            second["id"],
            status="ready",
            audio=f"audio/{second['id']}.wav",
        )
        with self.assertRaises(exporter.ExportError):
            exporter.build(FakeJob(), self.data, self.project["id"])


if __name__ == "__main__":
    unittest.main()
