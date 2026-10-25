from __future__ import annotations

import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from app import vgmstream


class VgmstreamTest(unittest.TestCase):
    def test_command_uses_subsong_and_name(self) -> None:
        command = vgmstream.command("vgmstream-cli.exe", "a.assets.bank", "out")
        self.assertEqual(command[1:3], ["-S", "0"])
        self.assertTrue(command[4].endswith("?s_?n.wav"))
        self.assertEqual(command[-1], "a.assets.bank")

    def test_safe_extract_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "ok.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("a/b.txt", "hello")
            destination = root / "out"
            count = vgmstream.safe_extract_zip(archive, destination)
            self.assertEqual(count, 1)
            self.assertEqual((destination / "a" / "b.txt").read_text(), "hello")

    def test_safe_extract_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../evil.txt", "x")
            with self.assertRaises(vgmstream.VgmstreamError):
                vgmstream.safe_extract_zip(archive, root / "out")
            self.assertFalse((root / "evil.txt").exists())

    def test_safe_extract_rejects_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("C:/evil.txt", "x")
            with self.assertRaises(vgmstream.VgmstreamError):
                vgmstream.safe_extract_zip(archive, root / "out")

    def test_safe_extract_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                info = zipfile.ZipInfo("link")
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(info, "target")
            with self.assertRaises(vgmstream.VgmstreamError):
                vgmstream.safe_extract_zip(archive, root / "out")

    def test_find_prefers_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vendor = root / "vendor" / "vgmstream"
            vendor.mkdir(parents=True)
            exe = vendor / vgmstream.EXE_NAME
            exe.write_bytes(b"stub")
            with mock.patch.object(vgmstream.config, "APP_ROOT", root), mock.patch.object(
                vgmstream.config, "BASE_DIR", root
            ), mock.patch.object(vgmstream, "install_dir", return_value=root / "data"), mock.patch.object(
                vgmstream.shutil, "which", return_value=None
            ):
                self.assertEqual(vgmstream.find(), exe)
                self.assertTrue(vgmstream.is_bundled(vgmstream.find()))

    def test_install_to_is_idempotent_when_exe_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / vgmstream.EXE_NAME).write_bytes(b"stub")
            with mock.patch.object(vgmstream, "_download") as download:
                self.assertEqual(vgmstream.install_to(root), root / vgmstream.EXE_NAME)
                download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
