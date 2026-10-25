from __future__ import annotations

import base64
import subprocess
import unittest
from unittest import mock

from app import config, dialogs


class DialogTest(unittest.TestCase):
    def test_ps_quote_escapes_single_quote(self) -> None:
        self.assertEqual(dialogs._ps_quote(r"D:\it's here"), r"'D:\it''s here'")

    def test_windows_picker_decodes_base64(self) -> None:
        expected = r"D:\WTVoiceForge\components"
        payload = base64.b64encode(expected.encode("utf-8")).decode("ascii")
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr="")
        with mock.patch.object(dialogs.subprocess, "run", return_value=completed) as run:
            selected = dialogs.pick_folder("选择目录", str(config.DEFAULT_DATA_DIR))
        self.assertEqual(selected, expected)
        command = run.call_args.args[0]
        self.assertIn("-EncodedCommand", command)
        self.assertIn("powershell.exe", command)

    def test_windows_picker_cancel_returns_empty(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch.object(dialogs.subprocess, "run", return_value=completed):
            self.assertEqual(dialogs.pick_folder("选择目录"), "")

    def test_frozen_failure_does_not_fall_back_to_tk(self) -> None:
        with mock.patch.object(dialogs.subprocess, "run", side_effect=OSError("no powershell")):
            with mock.patch.object(config, "FROZEN", True):
                with self.assertRaises(RuntimeError):
                    dialogs.pick_folder("选择目录")


if __name__ == "__main__":
    unittest.main()
