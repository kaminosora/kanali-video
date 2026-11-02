from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'.deps'),str(ROOT)]
import numpy as np


class CalibrationTests(unittest.TestCase):
    def test_preview_changes_surface_not_render_configuration_or_uv(self):
        from mapper.calibration import axis_preview_response
        from mapper.geometry import mapping_mesh,normalize_config
        config=normalize_config({})
        before=dict(config)
        render=mapping_mesh(config,2)
        actual=axis_preview_response(config,2,{'actual_bend_center_u':.52})
        self.assertEqual(config,before)
        self.assertNotIn('uv',actual)
        self.assertNotIn('brightness_gain',actual)
        self.assertAlmostEqual(actual['delta_pixels'],43.2)
        self.assertAlmostEqual(actual['actual_center_px'],1123.2)
        self.assertAlmostEqual(actual['render_center_px'],1080)
        self.assertFalse(np.allclose(actual['projected'],render['projected']))

    def test_disabled_preview_is_absent_and_invalid_values_rejected(self):
        from mapper.calibration import axis_preview_response
        self.assertIsNone(axis_preview_response({},2,None))
        for raw in (True,[],{}, {'actual_bend_center_u':True},
                    {'actual_bend_center_u':0},{'actual_bend_center_u':float('nan')}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                axis_preview_response({},2,raw)

    def test_chart_native_size_parallel_lines_and_no_overwrite(self):
        from mapper.calibration import create_parallel_lines
        from PIL import Image
        cache=ROOT/'.cache'/'test-calibration';cache.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as folder:
            path=Path(folder)/'parallel.png';create_parallel_lines(path)
            with Image.open(path) as image:
                self.assertEqual(image.size,(2160,1080))
                # Several horizontal lines across the bend region; unaffected by labels.
                for y in (216,360,504,648,792):
                    for x in (777,1023,1333):
                        self.assertGreater(min(image.getpixel((x,y))),180)
            with self.assertRaises(FileExistsError):create_parallel_lines(path)


if __name__=='__main__':unittest.main()
