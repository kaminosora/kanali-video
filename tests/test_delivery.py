"""A bitrate estimate never substitutes for the final decimal-byte gate."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.deps'))
import av
from tests.tool_paths import find_tool

FFMPEG = find_tool('ffmpeg')
try:
    delivery = importlib.import_module('mapper.delivery')
except ModuleNotFoundError:
    delivery = None


class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = ROOT / '.cache' / 'tests-delivery'
        root.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(dir=root)
        cls.directory = Path(cls.temporary.name)
        cls.candidate = cls.directory / 'high-quality.mp4'
        if FFMPEG:
            result = subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30000/1001', '-frames:v', '60',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '4', '-pix_fmt', 'yuv420p',
                '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709',
                '-color_trc', 'bt709', str(cls.candidate)], capture_output=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stderr.decode(errors='replace'))
        else:
            # The size/rollback contract deliberately does not decode its input.
            # Mock-runner tests remain meaningful without an external encoder.
            cls.candidate.write_bytes(b'candidate fixture' * 10_000)
        cls.original_hash = hashlib.sha256(cls.candidate.read_bytes()).digest()
        cls.options = {'frames': 60, 'fps_num': 30000, 'fps_den': 1001, 'quality': 'high'}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.assertIsNotNone(delivery, 'Strict delivery-size acceptance is not implemented')
        self.case = self.directory / self._testMethodName
        self.case.mkdir()
        self.final = self.case / 'delivery.mp4'

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.candidate.read_bytes()).digest(), self.original_hash)
        if hasattr(self, 'case'):
            self.assertFalse(list(self.case.rglob('*.partial.mp4')), 'Partial files must never survive terminal failure')

    def test_budget_uses_encoded_frame_duration_instead_of_unrounded_selection(self):
        bitrate = delivery.budget_bitrate(dict(self.options, duration=1), 500_000_000)
        self.assertLess(bitrate * (60 * 1001 / 30000) / 8, 480_000_000)
        self.assertGreater(bitrate * (60 * 1001 / 30000) / 8, 470_000_000)
        with self.assertRaises(ValueError):
            delivery.budget_bitrate(self.options, 500_000_001)

    def test_under_limit_candidate_is_copied_without_reencoding(self):
        def unexpected(command):
            self.fail('A compliant candidate must not be recompressed')
        result = delivery.finalize_mp4(self.candidate, self.final, self.options,
            ffmpeg_path=FFMPEG, runner=unexpected, limit=self.candidate.stat().st_size + 1)
        self.assertFalse(result['reencoded'])
        self.assertEqual(self.final.read_bytes(), self.candidate.read_bytes())
        self.assertTrue(Path(result['log_path']).is_file())

    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: real encoding test')
    def test_real_low_limit_reencoding_keeps_frames_timebase_and_colour(self):
        events = []
        self.assertGreater(self.candidate.stat().st_size, 35_000)
        result = delivery.finalize_mp4(self.candidate, self.final, self.options,
            ffmpeg_path=FFMPEG, limit=35_000, progress=events.append)
        self.assertLess(self.final.stat().st_size, 35_000)
        self.assertTrue(result['reencoded'])
        self.assertTrue(any(event['phase'] == 'encoding' for event in events))
        with av.open(str(self.final)) as container:
            stream = container.streams.video[0]
            frames = list(container.decode(stream))
            self.assertEqual((stream.width, stream.height, len(frames)), (320, 180, 60))
            self.assertEqual(str(stream.average_rate), '30000/1001')
            self.assertEqual(stream.codec_context.format.name, 'yuv420p')
            self.assertEqual((stream.codec_context.colorspace, stream.codec_context.color_primaries,
                              stream.codec_context.color_trc), (1, 1, 1))

    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: real encoding test')
    def test_exactly_equal_real_candidate_is_not_accepted(self):
        result = delivery.finalize_mp4(self.candidate, self.final, self.options,
            ffmpeg_path=FFMPEG, limit=self.candidate.stat().st_size)
        self.assertTrue(result['reencoded'])
        self.assertLess(self.final.stat().st_size, self.candidate.stat().st_size)

    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: real deep-path encoding test')
    def test_real_two_pass_in_deep_unicode_space_directory(self):
        from mapper.jobs import JobManager
        deep = self.case
        while len(str(deep)) < 270:
            deep /= '较深 目录 with spaces and long project name'
        deep.mkdir(parents=True)
        candidate = deep/'原始 高质量.mp4'
        candidate.write_bytes(self.candidate.read_bytes())
        options = self.options
        for managed in (False, True):
            with self.subTest(managed=managed):
                target = deep/('managed.mp4' if managed else 'default.mp4')
                manager = JobManager.__new__(JobManager)
                manager.lock, manager.processes = threading.RLock(), {}
                job = {'id': 'deep-delivery', 'status': 'encoding', 'directory': str(deep)}
                with (deep/'manager.log').open('wb') as log:
                    runner = (lambda command: manager._process(job, command, log)) if managed else None
                    try:
                        result = delivery.finalize_mp4(candidate, target, options,
                            ffmpeg_path=FFMPEG, limit=35_000, runner=runner)
                    except Exception:
                        # Preserve the native diagnostic even after TemporaryDirectory cleanup.
                        evidence = ROOT/'.cache'/'delivery-deep-path-failure.log'
                        evidence.write_text('\n'.join(path.read_text(encoding='utf-8', errors='replace')
                            for path in deep.rglob('*.log')), encoding='utf-8')
                        raise
                self.assertTrue(result['reencoded'])
                self.assertLess(target.stat().st_size, 35_000)
                with av.open(str(target)) as movie:
                    self.assertEqual(len(list(movie.decode(video=0))), 60)
                self.assertEqual(manager.processes, {})

    def test_measured_overshoot_retries_lower_bitrate_from_original_candidate(self):
        limit, calls, rates = 50_000, [], []
        def runner(command):
            calls.append(command)
            self.assertEqual(Path(command[command.index('-i')+1]), self.candidate)
            if command[command.index('-pass')+1] == '2':
                rates.append(int(command[command.index('-b:v')+1]))
                Path(command[-1]).write_bytes(b'x' * (limit if len(rates) == 1 else limit-1))
            return 0
        result = delivery.finalize_mp4(self.candidate, self.final, self.options,
            ffmpeg_path=FFMPEG, runner=runner, limit=limit)
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(len(calls), 4)
        self.assertLess(rates[1], rates[0])
        self.assertEqual(self.final.stat().st_size, limit-1)

    def test_attempt_limit_never_publishes_oversized_file(self):
        calls = []
        def runner(command):
            calls.append(command)
            if command[command.index('-pass')+1] == '2':
                Path(command[-1]).write_bytes(b'x' * 50_001)
            return 0
        with self.assertRaises(delivery.DeliveryBudgetError):
            delivery.finalize_mp4(self.candidate, self.final, self.options,
                ffmpeg_path=FFMPEG, runner=runner, limit=50_000, max_attempts=2)
        self.assertEqual(len(calls), 4)
        self.assertFalse(self.final.exists())
        self.assertTrue(list(self.case.rglob('*.log')), 'Failure diagnostics must survive')

    def test_encoder_failure_preserves_candidate_and_does_not_publish_partial(self):
        def runner(command):
            if command[command.index('-pass')+1] == '2':
                Path(command[-1]).write_bytes(b'broken')
                return 7
            return 0
        with self.assertRaises(RuntimeError):
            delivery.finalize_mp4(self.candidate, self.final, self.options,
                ffmpeg_path=FFMPEG, runner=runner, limit=50_000)
        self.assertFalse(self.final.exists())
        self.assertTrue(list(self.case.rglob('*.log')))

    def test_cancel_after_encoder_exit_prevents_publication(self):
        cancelled = []
        def runner(command):
            if command[command.index('-pass')+1] == '2':
                Path(command[-1]).write_bytes(b'candidate within budget')
                cancelled.append(True)
            return 0
        with self.assertRaises(InterruptedError):
            delivery.finalize_mp4(self.candidate, self.final, self.options,
                ffmpeg_path=FFMPEG, runner=runner, cancelled=lambda: bool(cancelled), limit=50_000)
        self.assertFalse(self.final.exists())

    def test_cancel_in_completion_callback_does_not_return_a_delivery(self):
        cancelled = []
        def report(event):
            if event['phase'] == 'completed':
                cancelled.append(True)
        with self.assertRaises(InterruptedError):
            delivery.finalize_mp4(self.candidate, self.final, self.options,
                ffmpeg_path=FFMPEG, progress=report, cancelled=lambda: bool(cancelled))
        self.assertFalse(self.final.exists())

    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: real encoding test')
    def test_real_ffmpeg_failure_keeps_stderr_and_no_final(self):
        broken = self.case / 'broken-candidate.mp4'
        broken.write_bytes(b'not an MP4' * 6000)
        with self.assertRaises(RuntimeError):
            delivery.finalize_mp4(broken, self.final, self.options,
                                 ffmpeg_path=FFMPEG, limit=50_000)
        self.assertFalse(self.final.exists())
        logs = list(self.case.rglob('delivery.log'))
        self.assertEqual(len(logs), 1)
        diagnostic = logs[0].read_text(encoding='utf-8', errors='replace')
        self.assertIn('Invalid data', diagnostic)
        self.assertEqual(broken.read_bytes(), b'not an MP4' * 6000)

    @unittest.skipUnless(FFMPEG, 'FFmpeg unavailable: real encoding test')
    def test_real_runner_can_cancel_after_its_first_progress_report(self):
        cancelled = []
        def report(event):
            if event['phase'] == 'encoding' and 'frame' in event:
                cancelled.append(True)
        with self.assertRaises(InterruptedError):
            delivery.finalize_mp4(self.candidate, self.final, self.options,
                ffmpeg_path=FFMPEG, progress=report, cancelled=lambda: bool(cancelled), limit=35_000)
        self.assertTrue(cancelled)
        self.assertFalse(self.final.exists())

    def test_existing_final_and_source_alias_are_never_overwritten(self):
        self.final.write_bytes(b'user-owned existing output')
        with self.assertRaises(FileExistsError):
            delivery.finalize_mp4(self.candidate, self.final, self.options, ffmpeg_path=FFMPEG)
        self.assertEqual(self.final.read_bytes(), b'user-owned existing output')
        with self.assertRaises((ValueError, FileExistsError)):
            delivery.finalize_mp4(self.candidate, self.candidate, self.options, ffmpeg_path=FFMPEG)


if __name__ == '__main__':
    unittest.main()
