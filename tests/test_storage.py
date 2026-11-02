"""JSON publication survives brief Windows readers without hiding real failures."""
import concurrent.futures
import ctypes
from ctypes import wintypes
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from mapper import storage
except ImportError:
    storage = None


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(storage, "A shared atomic JSON writer is required")
        base = ROOT / '.cache' / 'tests-storage'
        base.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / 'progress.json'
        self.target.write_text('{"frame": 34}', encoding='utf-8')

    def assert_no_temporary_files(self):
        self.assertEqual([path.name for path in self.directory.iterdir()], ['progress.json'])

    @unittest.skipUnless(os.name == 'nt', 'Windows rename sharing semantics')
    def test_real_windows_reader_releases_target_and_publish_recovers(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes, close.restype = (wintypes.HANDLE,), wintypes.BOOL
        # Permit readers/writers, but deliberately deny FILE_SHARE_DELETE.
        handle = create(str(self.target), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        released = threading.Event()
        def release():
            close(handle)
            released.set()
        timer = threading.Timer(0.15, release)
        original_replace = os.replace
        blocked = []
        def replace(source, target):
            try:
                return original_replace(source, target)
            except PermissionError:
                blocked.append(True)
                raise
        timer.start()
        try:
            with patch.object(storage.os, 'replace', side_effect=replace):
                storage.write_json(self.target, {'frame': 35, 'message': '渲染'}, retry_timeout=1)
        finally:
            timer.join()
        self.assertTrue(blocked, 'A real Windows read handle must initially block replacement')
        self.assertEqual(json.loads(self.target.read_text(encoding='utf-8')), {'frame': 35, 'message': '渲染'})
        self.assert_no_temporary_files()

    def test_persistent_permission_failure_is_bounded_and_preserves_previous_json(self):
        failure = PermissionError(errno.EACCES, 'permanent denial')
        with patch.object(storage.os, 'replace', side_effect=failure) as replace:
            started = time.monotonic()
            with self.assertRaises(PermissionError) as caught:
                storage.write_json(self.target, {'frame': 35}, retry_timeout=0.06, retry_interval=0.01)
        self.assertIs(caught.exception, failure)
        self.assertGreater(replace.call_count, 1)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(json.loads(self.target.read_text()), {'frame': 34})
        self.assert_no_temporary_files()

    def test_disk_full_is_not_retried_or_disguised_as_sharing_conflict(self):
        failure = OSError(errno.ENOSPC, 'disk full')
        with patch.object(storage.os, 'replace', side_effect=failure) as replace:
            with self.assertRaises(OSError) as caught:
                storage.write_json(self.target, {'frame': 35})
        self.assertIs(caught.exception, failure)
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(json.loads(self.target.read_text()), {'frame': 34})
        self.assert_no_temporary_files()

    def test_concurrent_writers_have_private_complete_temporary_files(self):
        original_replace = os.replace
        barrier = threading.Barrier(4)
        seen = []
        lock = threading.Lock()
        def replace(source, target):
            with lock:
                first_attempt = str(source) not in seen
                seen.append(str(source))
            self.assertEqual(len(json.loads(Path(source).read_text(encoding='utf-8'))['payload']), 1000)
            if first_attempt:
                barrier.wait(timeout=3)
            return original_replace(source, target)
        with patch.object(storage.os, 'replace', side_effect=replace):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda number: storage.write_json(self.target, {'frame': number, 'payload': 'x'*1000}), range(4)))
        self.assertEqual(len(set(seen)), 4)
        self.assertIn(json.loads(self.target.read_text())['frame'], range(4))
        self.assert_no_temporary_files()


if __name__ == '__main__':
    unittest.main()
