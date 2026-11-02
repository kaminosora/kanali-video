import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'.deps')]
import numpy as np


class BrightnessTests(unittest.TestCase):
    def test_angular_response_is_an_explicit_empirical_curve(self):
        from mapper.brightness import angular_response
        np.testing.assert_allclose(angular_response(np.cos(np.deg2rad([0,45,60,90])),.6,2),[1,.8,.7,.6])
        np.testing.assert_allclose(angular_response([0,.3,1],1,2),[1,1,1])

    def test_shoulder_preserves_anchors_and_is_monotone(self):
        from mapper.brightness import angular_response
        mu=np.linspace(0,1,10001)
        original=.6+.4*mu**2
        np.testing.assert_array_equal(angular_response(mu,.6,2,1),original)
        for shoulder in (.25,.5,2,4,8):
            with self.subTest(shoulder=shoulder):
                result=angular_response(mu,.6,2,shoulder)
                self.assertTrue(np.isfinite(result).all())
                self.assertTrue((np.diff(result)>=0).all())
                np.testing.assert_allclose(angular_response([0,2**-.5,1],.6,2,shoulder),[.6,.8,1])
                if shoulder>1:
                    self.assertGreater(angular_response(np.cos(np.deg2rad(30)),.6,2,shoulder),.9)

    def test_shoulder_extremes_and_unity_do_not_create_invalid_response(self):
        from mapper.brightness import angular_response
        for floor in (0,.6,1):
            for power in (.25,8):
                for shoulder in (.25,8):
                    result=angular_response([0,1e-8,.5,1-1e-12,1],floor,power,shoulder)
                    self.assertTrue(np.isfinite(result).all())
                    self.assertTrue(((result>=floor)&(result<=1)).all())

    def test_shoulder_can_explain_bright_shoulders_with_matched_center_and_wings(self):
        from mapper.brightness import angular_response
        # A reference at 45 degrees and the forward-facing centre both match;
        # the older family still leaves brighter residuals at intermediate angles.
        angles=np.array([0,15,30,45])
        old=angular_response(np.cos(np.deg2rad(angles)),.6,2,1)
        actual=angular_response(np.cos(np.deg2rad(angles)),.6,2,2)
        np.testing.assert_allclose(actual[[0,3]],old[[0,3]])
        self.assertGreater((actual/old)[2],1.06)

    def test_shoulder_mesh_gains_equalize_response_at_shifted_axis(self):
        from mapper.geometry import mapping_mesh
        mesh=mapping_mesh({'brightness_enabled':True,'brightness_shoulder':2,
                           'bend_center_u':.55},2,columns=127,rows=15)
        self.assertEqual(mesh['brightness_summary']['shoulder'],2)
        response=np.asarray(mesh['angular_response']);gain=np.asarray(mesh['brightness_gain'])
        np.testing.assert_allclose(response*gain,mesh['brightness_summary']['target_response'],rtol=1e-10,atol=0)
        self.assertLessEqual(gain.max(),1)

    def test_default_is_off_and_old_presets_stay_off(self):
        from mapper.geometry import mapping_mesh
        mesh=mapping_mesh({},2,columns=32,rows=8)
        self.assertFalse(mesh['config']['brightness_enabled'])
        self.assertEqual(mesh['config']['brightness_shoulder'],1)
        np.testing.assert_array_equal(mesh['brightness_gain'],1)

    def test_full_strength_equalizes_model_without_boosting(self):
        from mapper.geometry import mapping_mesh
        mesh=mapping_mesh({'brightness_enabled':True},2,columns=64,rows=16)
        gain=np.asarray(mesh['brightness_gain']);response=np.asarray(mesh['angular_response'])
        self.assertLess(gain.min(),.9)
        self.assertLessEqual(gain.max(),1)
        np.testing.assert_allclose(gain*response,mesh['brightness_summary']['target_response'],rtol=1e-10)
        # Centre normal faces the observer more directly than the planar wings.
        center=8*65+32;left=8*65
        self.assertLess(gain[center],gain[left])

    def test_reference_does_not_follow_crop_or_preview_resolution(self):
        from mapper.geometry import mapping_mesh
        a=mapping_mesh({'brightness_enabled':True},2,columns=16,rows=4)
        b=mapping_mesh({'brightness_enabled':True,'zoom':3,'pan_x':.1},16/9,columns=64,rows=12)
        self.assertEqual(a['brightness_summary']['target_response'],b['brightness_summary']['target_response'])

    def test_neutral_parameters_and_partial_strength(self):
        from mapper.geometry import mapping_mesh
        for config in ({'brightness_enabled':True,'brightness_floor':1},
                       {'brightness_enabled':True,'brightness_strength':0}):
            mesh=mapping_mesh(config,2,columns=16,rows=4)
            np.testing.assert_array_equal(mesh['brightness_gain'],1)
        full=mapping_mesh({'brightness_enabled':True},2,columns=16,rows=4)
        half=mapping_mesh({'brightness_enabled':True,'brightness_strength':.5},2,columns=16,rows=4)
        np.testing.assert_allclose(half['brightness_gain'],(np.asarray(full['brightness_gain'])+1)/2)

    def test_elevation_and_distance_change_angles_not_inverse_square_brightness(self):
        from mapper.geometry import mapping_mesh
        flat={'bend_deg':0,'view_elevation_deg':0,'brightness_enabled':True}
        near=mapping_mesh(flat,2,columns=8,rows=2)
        far=mapping_mesh(dict(flat,view_distance_mm=6500),2,columns=8,rows=2)
        self.assertAlmostEqual(near['angular_response'][13],1)
        self.assertAlmostEqual(far['angular_response'][13],1)
        raised=mapping_mesh(dict(flat,view_elevation_deg=30),2,columns=8,rows=2)
        self.assertAlmostEqual(raised['angular_response'][13],.9)
        self.assertNotEqual(raised['angular_response'][4],raised['angular_response'][-5])

    def test_invalid_parameters_are_rejected(self):
        from mapper.geometry import normalize_config
        for patch in ({'brightness_enabled':1},{'brightness_floor':-1},{'brightness_floor':1.01},
                      {'brightness_power':0},{'brightness_power':9},{'brightness_strength':float('nan')},
                      {'brightness_strength':2},{'brightness_floor':True},
                      {'brightness_shoulder':0},{'brightness_shoulder':9},
                      {'brightness_shoulder':True},{'brightness_shoulder':float('nan')}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):normalize_config(patch)

    def test_corner_reference_bounds_asymmetric_close_and_flat_views(self):
        from mapper.geometry import mapping_mesh
        for patch in ({'view_azimuth_deg':25,'view_elevation_deg':30,'view_distance_mm':200},
                      {'view_azimuth_deg':70,'view_elevation_deg':-25,'view_distance_mm':300},
                      {'bend_deg':0,'view_azimuth_deg':85},
                      {'curve_length_mm':152.65185185185183,'brightness_floor':0},
                      {'bend_deg':0,'view_azimuth_deg':134.9,'view_elevation_deg':0,
                       'brightness_floor':0,'brightness_power':8},
                      {'bend_deg':0,'view_azimuth_deg':134.9,'view_elevation_deg':0,
                       'brightness_floor':0,'brightness_power':8,'brightness_shoulder':8}):
            with self.subTest(patch=patch):
                mesh=mapping_mesh(dict(patch,brightness_enabled=True),2,columns=193,rows=23)
                response=np.asarray(mesh['angular_response'])
                self.assertLessEqual(mesh['brightness_summary']['target_response'],response.min()+1e-12)
                np.testing.assert_allclose(response*np.asarray(mesh['brightness_gain']),
                                           mesh['brightness_summary']['target_response'],rtol=1e-10,atol=0)

    def test_gray_calibration_is_uniform_native_canvas_and_preserves_existing_file(self):
        import tempfile
        from PIL import Image
        from mapper.media import create_brightness_calibration
        cache=ROOT/'.cache'/'brightness-tests';cache.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            path=Path(directory)/'gray.png'
            create_brightness_calibration(path)
            with Image.open(path) as image:
                self.assertEqual(image.size,(2160,1080))
                self.assertEqual(image.getextrema(),((128,128),)*3)
            with self.assertRaises(FileExistsError):create_brightness_calibration(path)


if __name__=='__main__':unittest.main()
