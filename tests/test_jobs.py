from __future__ import annotations

import time
import unittest

from app.jobs import JobBusy, JobCancelled, Manager, check_cancel


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class JobTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = Manager()

    def test_job_completes_and_reports_progress(self) -> None:
        def runner(job):
            job.update(stage="work", message="处理中", current=1, total=2)
            job.step("第二步")
            return {"value": 42}

        job = self.manager.start("test", runner)
        self.assertIn(job["state"], ("running", "done"))
        self.assertTrue(wait_for(lambda: self.manager.snapshot()["state"] != "running"))
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["state"], "done")
        self.assertEqual(snapshot["result"], {"value": 42})
        self.assertEqual(snapshot["percent"], 100.0)
        self.assertFalse(snapshot["active"])

    def test_only_one_active_job(self) -> None:
        import threading

        release = threading.Event()

        def runner(job):
            release.wait(5)
            return {}

        first = self.manager.start("slow", runner)
        self.assertEqual(first["state"], "running")
        with self.assertRaises(JobBusy):
            self.manager.start("second", runner)
        release.set()
        self.assertTrue(wait_for(lambda: self.manager.snapshot()["state"] == "done"))

    def test_cancel_sets_cancelled_state(self) -> None:
        import threading

        started = threading.Event()

        def runner(job):
            started.set()
            while True:
                check_cancel(job)
                time.sleep(0.02)

        self.manager.start("cancel", runner)
        self.assertTrue(started.wait(2))
        snapshot = self.manager.cancel()
        self.assertTrue(snapshot["active"])
        self.assertTrue(wait_for(lambda: self.manager.snapshot()["state"] == "cancelled"))
        self.assertFalse(self.manager.snapshot()["active"])

    def test_error_is_reported(self) -> None:
        def runner(job):
            raise ValueError("坏了")

        self.manager.start("bad", runner)
        self.assertTrue(wait_for(lambda: self.manager.snapshot()["state"] == "error"))
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["error"], "坏了")
        self.assertEqual(snapshot["message"], "坏了")

    def test_cancel_after_finish_is_noop(self) -> None:
        def runner(job):
            return {"ok": True}

        self.manager.start("quick", runner)
        self.assertTrue(wait_for(lambda: self.manager.snapshot()["state"] == "done"))
        snapshot = self.manager.cancel()
        self.assertEqual(snapshot["state"], "done")
        self.assertFalse(snapshot["active"])


if __name__ == "__main__":
    unittest.main()
