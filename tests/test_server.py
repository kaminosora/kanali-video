import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class PathContractTests(unittest.TestCase):
    def test_static_resolution_rejects_escape(self):
        from app import safe_child
        root = Path(__file__).resolve().parents[1] / "web"
        self.assertEqual(safe_child(root, "app.js"), root / "app.js")
        for value in ["../app.py", "../../secret", "X:/external/private.ini", "nested/../../app.py"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_child(root, value)

    def test_single_range_is_bounded_and_suffix_is_supported(self):
        from app import parse_range
        self.assertEqual(parse_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(parse_range("bytes=90-", 100), (90, 99))
        self.assertEqual(parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(parse_range("bytes=90-500", 100), (90, 99))
        for value in ["bytes=100-", "bytes=-0", "bytes=9-2", "bytes=0-1,4-5", "nonsense"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_range(value, 100)


if __name__ == "__main__":
    unittest.main()
