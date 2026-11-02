import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]
from PIL import Image


class DecorationTests(unittest.TestCase):
    def test_simultaneous_first_requests_produce_one_complete_readable_asset(self):
        from mapper.decoration import standee_asset
        folder = ROOT / ".cache" / "tests-decoration"
        folder.mkdir(parents=True, exist_ok=True)
        barrier = threading.Barrier(2)
        original = Image.Image.save
        # Synchronize real disk writes so both callers reach atomic publication.
        def save_then_wait(image, *args, **kwargs):
            original(image, *args, **kwargs)
            barrier.wait(timeout=10)
        with tempfile.TemporaryDirectory(dir=folder) as temporary:
            with patch.object(Image.Image, "save", save_then_wait), ThreadPoolExecutor(2) as executor:
                paths = list(executor.map(lambda _: standee_asset(16/9, temporary), range(2)))
            self.assertEqual(paths[0], paths[1])
            with Image.open(paths[0]) as result:
                result.load()
                self.assertGreater(result.width, 1000)
                self.assertEqual(result.height, 2160)


if __name__ == "__main__":
    unittest.main()
