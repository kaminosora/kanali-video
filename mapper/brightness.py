"""Empirical OLED angular luminance, not Lambertian luminous intensity.

No panel-specific optical measurements are assumed. Gains only attenuate and
must be applied to linear RGB after compositing the source and decoration.
"""
import numpy as np


def angular_response(mu, floor, power):
    return floor + (1.0 - floor) * np.clip(np.asarray(mu, dtype=float), 0, 1) ** power


def _cosines(config, positions, arcs, eye):
    length = config['curve_length_mm']
    phase = np.clip(arcs / length + .5, 0, 1) if length else np.zeros_like(arcs)
    theta = np.deg2rad(config['bend_deg']) * (phase * phase * (3 - 2 * phase) - .5)
    normals = np.column_stack((np.sin(theta), -np.cos(theta), np.zeros_like(theta)))
    rays = np.asarray(eye) - positions
    return np.clip(np.einsum('ij,ij->i', normals, rays) / np.linalg.norm(rays, axis=1), 0, 1)


def brightness_fields(config, positions, atlas, eye):
    from .geometry import _curve_points
    width, height = config['screen_width_mm'], config['screen_height_mm']
    floor, power = config['brightness_floor'], config['brightness_power']
    mu = _cosines(config, positions, (atlas[:, 0] - .5) * width, eye)
    response = angular_response(mu, floor, power)
    # On this convex extruded profile, d(mu)/ds has the sign of ray dot tangent,
    # which decreases monotonically while the entire surface faces the eye.
    # Hence the minimum is at an arc endpoint, and at the farther Z endpoint.
    # Use all four corners: an exact, crop/grid/content independent reference.
    corner_arcs = np.array([-width/2, width/2, -width/2, width/2])
    corners = np.column_stack((_curve_points(config, corner_arcs),
                               [-height/2, -height/2, height/2, height/2]))
    corner_mu = _cosines(config, corners, corner_arcs, eye)
    target = float(angular_response(corner_mu, floor, power).min())
    strength = config['brightness_strength'] if config['brightness_enabled'] else 0.0
    ratio = np.divide(target, response, out=np.ones_like(response), where=response > 0)
    gain = (1.0 - strength) + strength * np.minimum(1.0, ratio)
    # Fixed sampling for UI maxima only; never drives the compensation reference.
    arcs = np.linspace(-width/2, width/2, 2049)
    closest_z = np.clip(eye[2], -height/2, height/2)
    reference = np.column_stack((_curve_points(config, arcs), np.full(arcs.shape, closest_z)))
    maximum_mu = float(_cosines(config, reference, arcs, eye).max())
    maximum = float(angular_response(maximum_mu, floor, power))
    return {
        'brightness_gain': gain.tolist(),
        'angular_response': response.tolist(),
        'brightness_summary': {
            'angle_min_deg': float(np.rad2deg(np.arccos(maximum_mu))),
            'angle_max_deg': float(np.rad2deg(np.arccos(corner_mu.min()))),
            'response_min': target, 'response_max': maximum,
            'gain_min': float((1 - strength) + strength * (target / maximum if maximum > 0 else 1)),
            'gain_max': 1.0, 'target_response': target,
            'enabled': config['brightness_enabled'], 'floor': floor,
            'power': power, 'strength': config['brightness_strength'],
        },
    }
