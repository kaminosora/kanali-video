import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ExportContractTests(unittest.TestCase):
    def setUp(self):
        from mapper.jobs import validate_export
        self.validate = validate_export
        self.source = {"kind": "video", "duration": 8.0, "fps": 29.97002997,
                       "fps_num": 30000, "fps_den": 1001, "width": 1920, "height": 1080}

    def test_fractional_rate_and_trim_keep_real_time(self):
        result = self.validate({"start": 1.0, "end": 3.0}, self.source)
        self.assertEqual((result["fps_num"], result["fps_den"]), (30000, 1001))
        self.assertEqual(result["frames"], 60)
        self.assertAlmostEqual(result["duration"], 2.0)

    def test_high_rate_is_limited_to_panel_refresh(self):
        source = dict(self.source, fps=120, fps_num=120, fps_den=1)
        result = self.validate({}, source)
        self.assertEqual((result["fps_num"], result["fps_den"]), (60, 1))

    def test_invalid_ranges_and_names_do_not_create_jobs(self):
        for args in [{"start": -1}, {"start": 5, "end": 2}, {"end": 9},
                     {"fps": float("nan")}, {"fps": 61}, {"name": "../escape"},
                     {"supersample": 3}, {"quality": "unknown"}, {"start": True}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.validate(args, self.source)

    def test_still_has_explicit_duration_and_30fps_default(self):
        source = dict(self.source, kind="image", duration=0, fps=0)
        result = self.validate({"end": 2}, source)
        self.assertEqual(result["frames"], 60)

    def test_cancel_requested_before_process_registration_does_not_start_work(self):
        from mapper.jobs import JobManager
        import threading
        root = Path(__file__).resolve().parents[1] / ".cache" / "tests-jobs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            folder = Path(temporary)
            (folder / "cancel.request").touch()
            marker = folder / "should-not-exist"
            manager = JobManager.__new__(JobManager)
            manager.lock, manager.processes = threading.RLock(), {}
            job = {"id": "cancel-test", "status": "preparing", "directory": str(folder)}
            with (folder / "process.log").open("wb") as log:
                code = manager._process(job, [sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).touch()", str(marker)], log)
            self.assertNotEqual(code, 0)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
