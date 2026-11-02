"""Bend-axis calibration: geometry, error simulation, and real line recovery."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / '.deps'), str(ROOT)]
import numpy as np
from mapper import geometry as g


class AxisTests(unittest.TestCase):
    def test_old_config_defaults_and_explicit_center_are_identical(self):
        config = g.normalize_config({})
        self.assertEqual(config['bend_center_u'], .5)
        self.assertEqual(config['brightness_shoulder'], 1)
        a = g.mapping_mesh({}, 2, 16, 4)
        b = g.mapping_mesh({'bend_center_u': .5, 'brightness_shoulder': 1}, 2, 16, 4)
        self.assertEqual(a, b)

    def test_center_selects_physical_bend_and_preserves_left_right_arc_lengths(self):
        config = g.normalize_config({'bend_center_u': .57})
        width = config['screen_width_mm']
        delta = (.57 - .5) * width
        np.testing.assert_array_equal(g.curve_points(config, [delta]), [[0, 0]])
        points = g.curve_points(config, [-width / 2, delta, width / 2])
        self.assertGreater(abs(points[0, 0]), abs(points[2, 0]))
        mirrored = g.curve_points({'bend_center_u': .43}, [-width / 2, -delta, width / 2])
        np.testing.assert_allclose(points, mirrored[::-1] * [-1, 1], atol=1e-12)

    def test_normals_agree_with_tangents_in_shifted_transition_and_wings(self):
        config = g.normalize_config({'bend_center_u': .57})
        arcs = np.linspace(-config['screen_width_mm']/2 + 1, config['screen_width_mm']/2 - 1, 257)
        step = 1e-4
        tangent = (g._curve_points(config, arcs + step) - g._curve_points(config, arcs - step)) / (2 * step)
        expected = np.column_stack((tangent[:, 1], -tangent[:, 0], np.zeros(len(arcs))))
        expected /= np.linalg.norm(expected, axis=1)[:, None]
        np.testing.assert_allclose(g._curve_normals(config, arcs), expected, atol=2e-9)
        np.testing.assert_allclose(np.linalg.norm(g._curve_normals(config, arcs), axis=1), 1, atol=1e-14)

    def test_flat_screen_axis_has_no_geometric_or_brightness_effect(self):
        config = dict(bend_deg=0, brightness_enabled=True)
        first = g.mapping_mesh(dict(config, bend_center_u=.001), 2, 16, 4)
        last = g.mapping_mesh(dict(config, bend_center_u=.999), 2, 16, 4)
        for key in ('positions', 'projected', 'uv', 'bounds', 'angular_response', 'brightness_gain'):
            np.testing.assert_array_equal(first[key], last[key])

    def test_axis_and_shoulder_validation(self):
        for patch in ({'bend_center_u': 0}, {'bend_center_u': 1}, {'bend_center_u': True},
                      {'bend_center_u': '0.5'}, {'bend_center_u': float('nan')},
                      {'bend_center_u': .02}, {'bend_center_u': .98},
                      {'brightness_shoulder': .24}, {'brightness_shoulder': 8.01},
                      {'brightness_shoulder': True}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                g.normalize_config(patch)
        base = g.normalize_config({})
        edge = base['curve_length_mm'] / (2 * base['screen_width_mm'])
        for center in (edge, 1-edge):
            self.assertAlmostEqual(g.normalize_config({'bend_center_u': center})['bend_center_u'], center)

    def test_axis_preview_preserves_formal_config_and_returns_actual_geometry_only(self):
        config = {'bend_center_u': .5, 'brightness_enabled': False}
        original = copy.deepcopy(config)
        preview = g.axis_preview_mesh(config, 2, .53, columns=16, rows=4)
        self.assertEqual(config, original)
        self.assertEqual(preview['actual_bend_center_u'], .53)
        self.assertEqual(preview['render_bend_center_u'], .5)
        self.assertAlmostEqual(preview['delta_u'], .03)
        self.assertNotIn('uv', preview)
        self.assertNotIn('brightness_gain', preview)
        self.assertNotIn('config', preview)
        expected = g.mapping_mesh(dict(config, bend_center_u=.53), 2, 16, 4)
        for key in ('projected', 'bounds', 'angular_response'):
            self.assertEqual(preview[key], expected[key])

    @staticmethod
    def line_residual(render_center, actual_center, source_v):
        """Intersect a straight source line with renderer rays, reproject on actual panel."""
        config = g.normalize_config({'bend_center_u': render_center})
        basis = g.camera_basis(config)
        _, _, up, normal = basis
        distance = config['view_distance_mm']
        center, dimensions, _ = g._source_frame(config, 2, g._outline(config, basis), basis)
        arcs = np.linspace(-config['screen_width_mm']/2, config['screen_width_mm']/2, 501)
        xy_render = g._curve_points(config, arcs)
        target_y = center[1] + dimensions[1] * (source_v - .5)
        z = (target_y * (distance - xy_render @ normal[:2]) - distance * (xy_render @ up[:2])) / (distance * up[2] + target_y * normal[2])
        actual = g.normalize_config(dict(config, bend_center_u=actual_center))
        xyz = np.column_stack((g._curve_points(actual, arcs), z))
        projected = g._project(xyz, actual, basis)
        x, y = projected.T
        return y - np.polyval(np.polyfit(x, y, 1), x)

    def test_offset_bends_parallel_lines_and_matching_axis_restores_them(self):
        for source_v in (.25, .5, .75):
            with self.subTest(source_v=source_v):
                right = self.line_residual(.5, .52, source_v)
                left = self.line_residual(.5, .48, source_v)
                self.assertGreater(np.ptp(right), .45)
                self.assertGreater(right.max(), .1)
                self.assertLess(right.min(), -.1)
                np.testing.assert_allclose(right, left[::-1], atol=1e-11)
                for center in (.5, .52, .48):
                    self.assertLess(np.ptp(self.line_residual(center, center, source_v)), 1e-10)

    def test_asymmetric_outline_bounds_and_cover_do_not_clip_shifted_curve(self):
        for axis in (.1, .9):
            config = g.normalize_config({'bend_center_u': axis, 'zoom': 1, 'pan_x': 9, 'pan_y': -9})
            mesh = g.mapping_mesh(config, 16/9, columns=1001, rows=8)
            xy, uv, bounds = np.asarray(mesh['projected']), np.asarray(mesh['uv']), mesh['bounds']
            self.assertTrue(np.all(xy >= np.asarray(bounds[:2]) - 1e-10))
            self.assertTrue(np.all(xy <= np.asarray(bounds[2:]) + 1e-10))
            self.assertTrue(np.all(uv >= -1e-12))
            self.assertTrue(np.all(uv <= 1+1e-12))

    def test_explicit_extra_columns_are_unique_and_keep_mesh_contracts(self):
        mesh = g.mapping_mesh({}, 2, 4, 2, extra_u=[.125, .125, .8, 0, 1])
        atlas = np.asarray(mesh['atlas'])
        np.testing.assert_array_equal(np.unique(atlas[:, 0]), [0, .125, .25, .5, .75, .8, 1])
        self.assertEqual(len(atlas), 21)
        self.assertEqual(len(mesh['triangles']), 24)
        self.assertEqual(len(mesh['boundary_uv']), 16)
        self.assertEqual(max(max(triangle) for triangle in mesh['triangles']), len(atlas)-1)
        self.assertEqual(g.mapping_mesh({}, 2, 4, 2, extra_u=[]), g.mapping_mesh({}, 2, 4, 2))

    def test_extra_columns_validate_values_and_recheck_total_vertex_limit(self):
        for extra in ([-.1], [1.1], [float('nan')], [True], ['.5'], .5, [[.5]]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                g.mapping_mesh({}, 2, 4, 2, extra_u=extra)
        # The original regular grid would contain 1,999,998 vertices. Fail
        # before allocating it once even one new column takes it over budget.
        with self.assertRaisesRegex(ValueError, '2000000'):
            g.mapping_mesh({}, 2, 1000, 1997, extra_u=[.000123])

    def test_adaptive_brightness_samples_are_local_bounded_and_opt_in(self):
        config = dict(brightness_enabled=True, brightness_shoulder=8, curve_length_mm=1, bend_center_u=.537)
        for wrapper, columns in ((g.render_mapping_mesh, 512), (g.preview_mapping_mesh, 192)):
            with self.subTest(columns=columns):
                mesh = wrapper(config, 2, columns, 2)
                us = np.unique(np.asarray(mesh['atlas'])[:, 0])
                self.assertGreater(len(us), columns+1)
                self.assertLessEqual(len(us), columns+1+65)
                half = 1 / (2 * mesh['config']['screen_width_mm'])
                local = us[(us >= .537-half-1e-14) & (us <= .537+half+1e-14)]
                self.assertGreaterEqual(len(local), 65)
                for neutral in ({'brightness_enabled':False}, {'brightness_floor':1},
                                {'brightness_strength':0}, {'bend_deg':0}):
                    self.assertEqual(wrapper(dict(config, **neutral), 2, columns, 2),
                                     g.mapping_mesh(dict(config, **neutral), 2, columns, 2))
        # Existing defaults already have enough transition samples.
        self.assertEqual(g.render_mapping_mesh({'brightness_enabled':True}, 2),
                         g.mapping_mesh({'brightness_enabled':True}, 2, 512, 128))
        self.assertEqual(g.preview_mapping_mesh({'brightness_enabled':True}, 2),
                         g.mapping_mesh({'brightness_enabled':True}, 2, 192, 64))

    def test_local_density_reduces_narrow_shoulder_gain_interpolation_error(self):
        from mapper.brightness import brightness_fields
        config = g.normalize_config(dict(brightness_enabled=True, brightness_shoulder=8,
                                         curve_length_mm=1, bend_center_u=.537))
        width = config['screen_width_mm']
        probe_u = np.linspace(.537-.7/width, .537+.7/width, 4097)
        atlas = np.column_stack((probe_u, np.full(len(probe_u), .5)))
        xyz = np.column_stack((g._curve_points(config, (probe_u-.5)*width), np.zeros(len(probe_u))))
        exact = np.asarray(brightness_fields(config, xyz, atlas, g._basis(config)[0])['brightness_gain'])
        errors = []
        for maker in (g.mapping_mesh, g.render_mapping_mesh):
            mesh = maker(config, 2, 512, 2)
            mesh_atlas = np.asarray(mesh['atlas']);middle = mesh_atlas[:, 1] == .5
            approximate = np.interp(probe_u, mesh_atlas[middle, 0], np.asarray(mesh['brightness_gain'])[middle])
            errors.append(float(np.abs(approximate-exact).max()))
        self.assertLess(errors[1], errors[0]*.15)
        self.assertLess(errors[1], .005)

    def test_axis_simulation_keeps_the_adaptive_render_atlas_indices(self):
        from mapper.brightness import brightness_fields
        config = dict(brightness_enabled=True, brightness_shoulder=8, curve_length_mm=1, bend_center_u=.537)
        rendered = g.preview_mapping_mesh(config, 2, 192, 8)
        simulated = g.axis_preview_mesh(config, 2, .56, 192, 8)
        atlas = np.asarray(rendered['atlas'])
        self.assertEqual(len(simulated['projected']), len(atlas))
        actual = g.normalize_config(dict(config, bend_center_u=.56))
        xyz = np.column_stack((g._curve_points(actual, (atlas[:, 0]-.5)*actual['screen_width_mm']),
                               (atlas[:, 1]-.5)*actual['screen_height_mm']))
        np.testing.assert_allclose(simulated['projected'], g._project(xyz, actual), atol=1e-10)
        response = brightness_fields(actual, xyz, atlas, g._basis(actual)[0])['angular_response']
        np.testing.assert_allclose(simulated['angular_response'], response, atol=1e-14)
        for forbidden in ('uv', 'brightness_gain', 'config', 'triangles'):
            self.assertNotIn(forbidden, simulated)


if __name__ == '__main__':
    unittest.main()
