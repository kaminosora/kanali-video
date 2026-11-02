"""Independent geometry contracts; no Blender or display hardware required."""

import json
import math
import unittest

import numpy as np

try:
    from mapper import geometry
except ImportError:
    geometry = None


def inside_polygon(point, polygon, tolerance=1e-7):
    """Test helper: crossing number with an explicit point-on-edge check."""
    x, y = point
    inside = False
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0)):
        delta = end - start
        length2 = float(delta @ delta)
        fraction = min(1.0, max(0.0, float((point - start) @ delta) / length2)) if length2 else 0
        if np.linalg.norm(point - (start + fraction * delta)) <= tolerance:
            return True
        if (start[1] > y) != (end[1] > y):
            cross_x = start[0] + (y - start[1]) * delta[0] / delta[1]
            if x < cross_x:
                inside = not inside
    return inside


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(geometry, "The shared geometry implementation is missing")

    def flat(self, **overrides):
        return dict(screen_width_mm=200, screen_height_mm=100, bend_deg=0,
                    curve_length_mm=0, view_azimuth_deg=45, view_elevation_deg=0,
                    view_distance_mm=500, **overrides)

    def test_unknown_config_keys_cannot_escape_normalization(self):
        config = geometry.normalize_config({"zoom": 2, "unexpected": "discard me"})
        self.assertNotIn("unexpected", config)
        self.assertEqual(config["zoom"], 2)
        json.dumps(config, allow_nan=False)

    def test_nonfinite_wrong_type_and_out_of_range_configuration_is_rejected(self):
        bad = [
            {"zoom": math.nan}, {"pan_x": math.inf}, {"view_distance_mm": -math.inf},
            {"view_distance_mm": 10**1000},
            {"zoom": True}, {"pan_y": "0"}, {"fit": "stretch"},
            {"presentation": "unknown"},
            {"screen_width_mm": 0}, {"screen_height_mm": -1}, {"zoom": 0},
            {"curve_length_mm": 1000}, {"curve_length_mm": 0},
            {"bend_deg": 180}, {"view_elevation_deg": 90},
        ]
        for config in bad:
            with self.subTest(config=config), self.assertRaises(ValueError):
                geometry.normalize_config(config)

    def test_invisible_and_camera_plane_crossing_views_are_rejected(self):
        for config in [
            {"view_azimuth_deg": 135},
            {"view_azimuth_deg": 225},
            {"view_distance_mm": 1},
            {"view_distance_mm": 20, "view_elevation_deg": 75},
        ]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                geometry.normalize_config(config)

    def test_flat_frontal_identity_and_millimetre_bounds(self):
        mesh = geometry.mapping_mesh(self.flat(), 2, columns=4, rows=2)
        np.testing.assert_allclose(mesh["uv"], mesh["atlas"], atol=1e-12)
        np.testing.assert_allclose(mesh["bounds"], [-100, -50, 100, 50], atol=1e-12)
        np.testing.assert_allclose(mesh["positions"][0], [-100, 0, -50], atol=1e-12)
        np.testing.assert_allclose(mesh["positions"][-1], [100, 0, 50], atol=1e-12)

    def test_blender_camera_basis_is_right_handed_and_faces_the_screen(self):
        eye, right, up, normal = geometry.camera_basis(self.flat())
        np.testing.assert_allclose(eye, [0, -500, 0], atol=1e-12)
        np.testing.assert_allclose(right, [1, 0, 0], atol=1e-12)
        np.testing.assert_allclose(up, [0, 0, 1], atol=1e-12)
        np.testing.assert_allclose(np.cross(right, up), normal, atol=1e-12)
        self.assertAlmostEqual(float(np.dot(eye, normal)), 500, places=12)

    def test_square_cover_and_contain_on_flat_two_to_one_screen(self):
        cover = geometry.mapping_mesh(self.flat(fit="cover"), 1, 4, 2)
        contain = geometry.mapping_mesh(self.flat(fit="contain"), 1, 4, 2)
        np.testing.assert_allclose(cover["uv"][0], [0, 0.25], atol=1e-12)
        np.testing.assert_allclose(cover["uv"][-1], [1, 0.75], atol=1e-12)
        np.testing.assert_allclose(contain["uv"][0], [-0.5, 0], atol=1e-10)
        np.testing.assert_allclose(contain["uv"][-1], [1.5, 1], atol=1e-10)

    def test_elevated_finite_distance_is_not_a_column_only_warp(self):
        config = self.flat()
        config["view_elevation_deg"] = 30
        mesh = geometry.mapping_mesh(config, 2, 2, 2)
        expected_top = 500 * 50 * math.sqrt(3) / 2 / 475
        expected_bottom = -500 * 50 * math.sqrt(3) / 2 / 525
        self.assertAlmostEqual(mesh["projected"][7][1], expected_top, places=10)
        self.assertAlmostEqual(mesh["projected"][1][1], expected_bottom, places=10)
        self.assertGreater(mesh["projected"][-1][0], mesh["projected"][2][0])

    def test_projection_matches_independent_linear_ray_intersection(self):
        config = geometry.normalize_config({"view_azimuth_deg": 60, "view_elevation_deg": 23})
        mesh = geometry.mapping_mesh(config, 16 / 9, 24, 8)
        az, el = math.radians(15), math.radians(23)
        eye = config["view_distance_mm"] * np.array([
            math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
        normal = eye / np.linalg.norm(eye)
        right = np.array([math.cos(az), math.sin(az), 0])
        up = np.cross(normal, right)
        for index in [0, 12, 24, 105, len(mesh["positions"]) - 1]:
            point = np.array(mesh["positions"][index])
            matrix = np.column_stack((right, up, -(point - eye)))
            solution = np.linalg.solve(matrix, eye)
            np.testing.assert_allclose(mesh["projected"][index], solution[:2], atol=1e-10)

    def test_curve_uses_arc_length_and_g2_straight_wing_connections(self):
        config = geometry.normalize_config({})
        length, transition = config["screen_width_mm"], config["curve_length_mm"]
        arcs = np.linspace(-length / 2, length / 2, 20001)
        points = geometry.curve_points(config, arcs)
        measured = np.linalg.norm(np.diff(points, axis=0), axis=1).sum()
        self.assertAlmostEqual(measured, length, delta=2e-6)
        np.testing.assert_allclose(points[:, 0], -points[::-1, 0], atol=1e-12)
        np.testing.assert_allclose(points[:, 1], points[::-1, 1], atol=1e-12)
        step = 1e-3
        at_join = geometry.curve_points(config, np.array([transition / 2 - step,
                                                        transition / 2,
                                                        transition / 2 + step]))
        tangent = (at_join[2] - at_join[0]) / (2 * step)
        np.testing.assert_allclose(tangent, [math.sqrt(0.5), math.sqrt(0.5)], atol=1e-7)
        curvature = np.linalg.norm((at_join[2] - 2 * at_join[1] + at_join[0]) / step**2)
        self.assertLess(curvature, 2e-5)

    def test_grid_intervals_boundary_order_and_front_facing_triangles(self):
        columns, rows = 8, 4
        mesh = geometry.mapping_mesh({}, 16 / 9, columns, rows)
        self.assertEqual(len(mesh["positions"]), (columns + 1) * (rows + 1))
        self.assertEqual(len(mesh["triangles"]), 2 * columns * rows)
        boundary = (list(range(columns + 1))
                    + [row * (columns + 1) + columns for row in range(1, rows + 1)]
                    + [rows * (columns + 1) + col for col in range(columns - 1, -1, -1)]
                    + [row * (columns + 1) for row in range(rows - 1, 0, -1)])
        self.assertEqual(len(mesh["boundary_uv"]), 2 * (columns + rows))
        np.testing.assert_allclose(mesh["boundary_uv"], np.array(mesh["uv"])[boundary])
        positions = np.array(mesh["positions"])
        for triangle in mesh["triangles"]:
            p, q, r = positions[triangle]
            self.assertLess(np.cross(q - p, r - p)[1], 0)
        for key, value in mesh.items():
            self.assertIsInstance(value, dict if key == "config" else list)
        json.dumps(mesh, allow_nan=False)

    def test_pan_sign_and_zoom_scale_source_coordinates(self):
        base = geometry.mapping_mesh({"fit": "contain"}, 16 / 9, 8, 4)
        panned = geometry.mapping_mesh({"fit": "contain", "pan_x": 0.1, "pan_y": 0.2}, 16 / 9, 8, 4)
        zoomed = geometry.mapping_mesh({"fit": "contain", "zoom": 2}, 16 / 9, 8, 4)
        self.assertTrue(np.all(np.array(panned["uv"]) < np.array(base["uv"])))
        np.testing.assert_allclose(np.array(zoomed["uv"]) - 0.5,
                                   (np.array(base["uv"]) - 0.5) / 2, atol=1e-12)

    def test_contain_fits_all_video_edges_inside_curved_projected_silhouette(self):
        config = {"fit": "contain", "view_elevation_deg": 38, "view_azimuth_deg": 58}
        mesh = geometry.mapping_mesh(config, 2, 1024, 2)
        projected, uv = np.array(mesh["projected"]), np.array(mesh["uv"])
        width = (projected[-1, 0] - projected[0, 0]) / (uv[-1, 0] - uv[0, 0])
        height = (projected[-1, 1] - projected[0, 1]) / (uv[-1, 1] - uv[0, 1])
        origin = projected[0] - uv[0] * [width, height]
        self.assertAlmostEqual(width / height, 2, places=10)
        cols, rows = 1024, 2
        boundary_indices = (list(range(cols + 1))
                            + [row * (cols + 1) + cols for row in range(1, rows + 1)]
                            + [rows * (cols + 1) + col for col in range(cols - 1, -1, -1)]
                            + [cols + 1])
        polygon = projected[boundary_indices]
        for fraction in np.linspace(0, 1, 101):
            for unit in ([fraction, 0], [fraction, 1], [0, fraction], [1, fraction]):
                point = origin + np.array(unit) * [width, height]
                self.assertTrue(inside_polygon(point, polygon, tolerance=1e-4), point)

    def test_invalid_source_aspect_and_grid_sizes_are_rejected(self):
        for aspect in [0, -1, math.nan, math.inf, True, "2"]:
            with self.subTest(aspect=aspect), self.assertRaises(ValueError):
                geometry.mapping_mesh({}, aspect, 4, 2)
        for columns, rows in [(0, 2), (2, 0), (2.5, 2), (True, 2), (100000, 100000)]:
            with self.subTest(columns=columns, rows=rows), self.assertRaises(ValueError):
                geometry.mapping_mesh({}, 2, columns, rows)

    def test_cover_has_no_black_sliver_at_between_sample_curve_extremum(self):
        # This elevated view puts a vertical extremum between outline samples.
        config = dict(bend_deg=142.39164867659647, curve_length_mm=88.03200777622286,
                      view_azimuth_deg=43.1857022441429,
                      view_elevation_deg=56.00363064188569,
                      view_distance_mm=204.45872222316186)
        uv = np.array(geometry.mapping_mesh(config, 2, 1024, 4)["uv"])
        self.assertGreaterEqual(float(uv.min()), -1e-14)
        self.assertLessEqual(float(uv.max()), 1 + 1e-14)

    def test_cover_locks_underzoom_and_large_pan_without_black_borders(self):
        raw = self.flat(zoom=0.3, pan_x=9, pan_y=-9)
        mesh = geometry.mapping_mesh(raw, 2, 8, 4)
        uv = np.array(mesh["uv"])
        self.assertGreaterEqual(float(uv.min()), 0)
        self.assertLessEqual(float(uv.max()), 1)
        self.assertEqual(mesh["config"]["zoom"], 1)
        self.assertEqual(mesh["config"]["pan_x"], 0)
        self.assertEqual(mesh["config"]["pan_y"], 0)
        np.testing.assert_allclose(uv, mesh["atlas"], atol=1e-12)
        self.assertEqual(geometry.constrain_framing(raw, 2), mesh["config"])
        self.assertEqual(raw["zoom"], 0.3, "normalization must not mutate caller input")

    def test_cover_zoom_two_allows_distinct_crops_with_hand_computed_limits(self):
        right = geometry.mapping_mesh(self.flat(zoom=2, pan_x=9, pan_y=9), 2, 8, 4)
        left = geometry.mapping_mesh(self.flat(zoom=2, pan_x=-9, pan_y=-9), 2, 8, 4)
        self.assertAlmostEqual(right["config"]["pan_x"], 0.5, places=12)
        self.assertAlmostEqual(right["config"]["pan_y"], 0.5, places=12)
        self.assertAlmostEqual(left["config"]["pan_x"], -0.5, places=12)
        np.testing.assert_allclose(right["uv"][0], [0, 0], atol=1e-12)
        np.testing.assert_allclose(right["uv"][-1], [0.5, 0.5], atol=1e-12)
        np.testing.assert_allclose(left["uv"][0], [0.5, 0.5], atol=1e-12)
        np.testing.assert_allclose(left["uv"][-1], [1, 1], atol=1e-12)
        self.assertFalse(np.allclose(right["uv"], left["uv"]))

    def test_cover_limits_handle_portrait_wide_and_asymmetric_high_views(self):
        for aspect in (9 / 16, 16 / 9, 4, 0.25):
            for zoom in (0.2, 1, 2):
                for pan_x, pan_y in ((9, -9), (-9, 9)):
                    with self.subTest(aspect=aspect, zoom=zoom, pan_x=pan_x):
                        raw = dict(view_azimuth_deg=63, view_elevation_deg=48,
                                   view_distance_mm=210, zoom=zoom, pan_x=pan_x, pan_y=pan_y)
                        mesh = geometry.mapping_mesh(raw, aspect, 256, 16)
                        uv = np.array(mesh["uv"])
                        self.assertGreaterEqual(float(uv.min()), 0)
                        self.assertLessEqual(float(uv.max()), 1)
                        actual = mesh["config"]
                        self.assertEqual(actual, geometry.constrain_framing(raw, aspect))
                        self.assertEqual(actual, geometry.constrain_framing(actual, aspect))

    def test_contain_keeps_explicit_free_framing_and_reports_actual_config(self):
        raw = dict(fit="contain", zoom=0.2, pan_x=9, pan_y=-9)
        mesh = geometry.mapping_mesh(raw, 16 / 9, 16, 8)
        actual = geometry.constrain_framing(raw, 16 / 9)
        self.assertEqual(actual, geometry.normalize_config(raw))
        self.assertEqual(mesh["config"], actual)
        self.assertTrue(np.any(np.array(mesh["uv"]) < 0))

    def test_standee_has_hand_computed_video_size_and_upward_offset(self):
        mesh = geometry.mapping_mesh(self.flat(fit="contain", presentation="standee"), 2, 8, 4)
        # Total decoration is 158.125 x 99 mm in this 200 x 100 mm screen.
        # Video is 137.5 x 68.75 mm and its centre is 5.5 mm above the origin.
        np.testing.assert_allclose(mesh["uv"][0],
                                   [0.5 - 100 / 137.5, 0.5 - 55.5 / 68.75], atol=1e-12)
        np.testing.assert_allclose(mesh["uv"][-1],
                                   [0.5 + 100 / 137.5, 0.5 + 44.5 / 68.75], atol=1e-12)
        self.assertEqual(mesh["config"]["presentation"], "standee")

    def test_complete_standee_decoration_fits_curved_screen_silhouette(self):
        for aspect in (9 / 16, 16 / 9):
            with self.subTest(aspect=aspect):
                config = dict(fit="contain", presentation="standee", view_elevation_deg=38,
                              view_azimuth_deg=58)
                cols, rows = 512, 2
                mesh = geometry.mapping_mesh(config, aspect, cols, rows)
                projected, uv = np.array(mesh["projected"]), np.array(mesh["uv"])
                dimensions = (projected[-1] - projected[0]) / (uv[-1] - uv[0])
                origin = projected[0] - uv[0] * dimensions
                boundary_indices = (list(range(cols + 1))
                                    + [row * (cols + 1) + cols for row in range(1, rows + 1)]
                                    + [rows * (cols + 1) + col for col in range(cols - 1, -1, -1)]
                                    + [cols + 1])
                polygon = projected[boundary_indices]
                left, right = -0.15 / aspect, 1 + 0.15 / aspect
                for fraction in np.linspace(0, 1, 25):
                    for unit in ([left + (right - left) * fraction, -0.30],
                                 [left + (right - left) * fraction, 1.14],
                                 [left, -0.30 + 1.44 * fraction],
                                 [right, -0.30 + 1.44 * fraction]):
                        self.assertTrue(inside_polygon(origin + np.array(unit) * dimensions, polygon))

    def test_cover_framing_is_identical_with_inactive_standee_presentation(self):
        raw = dict(zoom=2, pan_x=9, pan_y=-9, view_elevation_deg=38, view_azimuth_deg=58)
        plain = geometry.mapping_mesh(dict(raw, presentation="plain"), 9 / 16, 64, 8)
        standee = geometry.mapping_mesh(dict(raw, presentation="standee"), 9 / 16, 64, 8)
        np.testing.assert_array_equal(plain["uv"], standee["uv"])
        self.assertEqual(standee["config"]["presentation"], "standee")
        self.assertEqual(plain["config"]["pan_x"], standee["config"]["pan_x"])
        self.assertEqual(plain["config"]["pan_y"], standee["config"]["pan_y"])


if __name__ == "__main__":
    unittest.main()
