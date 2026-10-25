from __future__ import annotations

import threading
import unittest
from unittest import mock

from app import launcher, server


class LauncherTest(unittest.TestCase):
    def test_health_probe_recognizes_this_app(self) -> None:
        httpd = server.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            port = httpd.server_address[1]
            url = launcher._probe_health("127.0.0.1", port)
            self.assertEqual(url, f"http://127.0.0.1:{port}/")
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_health_probe_rejects_other_services(self) -> None:
        payload = mock.MagicMock()
        payload.__enter__.return_value.read.return_value = b'{"ok": true, "app": "other"}'
        with mock.patch.object(launcher.urllib.request, "urlopen", return_value=payload):
            self.assertEqual(launcher._probe_health("127.0.0.1", 8765), "")
        with mock.patch.object(
            launcher.urllib.request, "urlopen", side_effect=OSError("refused")
        ):
            self.assertEqual(launcher._probe_health("127.0.0.1", 8765), "")

    def test_find_running_only_probes_listening_ports(self) -> None:
        expected = "http://127.0.0.1:8766/"
        with mock.patch.object(
            launcher.server, "port_in_use", side_effect=lambda _host, port: port == 8766
        ), mock.patch.object(
            launcher, "_probe_health_info", return_value=(expected, {"page_active": True})
        ) as probe:
            url, info = launcher.find_running_info("127.0.0.1", [8765, 8766, 8767])
        self.assertEqual(url, expected)
        self.assertTrue(info["page_active"])
        probe.assert_called_once_with("127.0.0.1", 8766)

    def test_page_activity_marks_workbench_open(self) -> None:
        server.reset_page_activity()
        try:
            self.assertFalse(server.page_active(now=100.0))
            server.note_page_activity(now=100.0)
            self.assertTrue(server.page_active(now=150.0))
            self.assertFalse(
                server.page_active(now=100.0 + server.PAGE_ACTIVE_SECONDS + 1)
            )
        finally:
            server.reset_page_activity()

    def test_main_keeps_existing_page_without_opening_another(self) -> None:
        with mock.patch.object(
            launcher,
            "find_running_info",
            return_value=("http://127.0.0.1:8765/", {"page_active": True}),
        ), mock.patch.object(
            launcher, "_focus_workbench_window", return_value=False
        ), mock.patch.object(
            launcher, "open_native_panel"
        ) as opened, mock.patch.dict("os.environ", {"WTVF_NO_BROWSER": "0"}):
            launcher.main()
        opened.assert_not_called()

    def test_main_focuses_existing_window(self) -> None:
        with mock.patch.object(
            launcher,
            "find_running_info",
            return_value=("http://127.0.0.1:8765/", {"page_active": True}),
        ), mock.patch.object(
            launcher, "_focus_workbench_window", return_value=True
        ), mock.patch.object(
            launcher, "open_native_panel"
        ) as opened, mock.patch.dict("os.environ", {"WTVF_NO_BROWSER": "0"}):
            launcher.main()
        opened.assert_not_called()

    def test_main_opens_panel_when_page_is_closed(self) -> None:
        with mock.patch.object(
            launcher,
            "find_running_info",
            return_value=("http://127.0.0.1:8765/", {"page_active": False}),
        ), mock.patch.object(
            launcher, "_focus_workbench_window", return_value=False
        ), mock.patch.object(
            launcher, "open_native_panel", return_value=(True, "")
        ) as opened, mock.patch.dict("os.environ", {"WTVF_NO_BROWSER": "0"}):
            launcher.main()
        opened.assert_called_once_with("http://127.0.0.1:8765/")

    def test_main_respects_no_browser(self) -> None:
        with mock.patch.object(
            launcher,
            "find_running_info",
            return_value=("http://127.0.0.1:8765/", {"page_active": False}),
        ), mock.patch.object(
            launcher, "open_native_panel"
        ) as opened, mock.patch.object(
            launcher, "_focus_workbench_window"
        ) as focused, mock.patch.dict("os.environ", {"WTVF_NO_BROWSER": "1"}):
            launcher.main()
        opened.assert_not_called()
        focused.assert_not_called()

    def test_first_launch_starts_server_opens_panel_and_stops_after_close(self) -> None:
        httpd = object()
        with mock.patch.object(
            launcher, "find_running_info", return_value=("", {})
        ), mock.patch.object(
            launcher, "_acquire_instance_mutex", return_value=True
        ), mock.patch.object(
            launcher, "start_local_server", return_value=(httpd, "http://127.0.0.1:8765/")
        ) as started, mock.patch.object(
            launcher, "open_native_panel", return_value=(True, "")
        ) as opened, mock.patch.object(
            launcher, "stop_local_server"
        ) as stopped, mock.patch.object(
            launcher, "_release_instance_mutex"
        ) as released, mock.patch.dict("os.environ", {"WTVF_NO_BROWSER": "0"}):
            launcher.main()
        started.assert_called_once_with()
        opened.assert_called_once_with("http://127.0.0.1:8765/")
        stopped.assert_called_once_with(httpd)
        released.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
