"""Arc-length screen geometry and a finite-distance inverse video projection.

All physical coordinates are millimetres.  X points right, Y away from the
default viewer, and Z up.  The centre of the curved profile is the origin.
Both atlas and source UV use v-up; image decoders must handle their own flip.
The public mesh contains JSON-compatible lists plus its effective config dict,
never NumPy objects.
"""

from collections.abc import Mapping
import math
from numbers import Integral, Real

import numpy as np


DEFAULT_CONFIG = {
    "screen_height_mm": 73.6,
    # Physical starting estimate; do not derive millimetres from KANALI's upload raster.
    "screen_width_mm": 152.65185185185183,
    "bend_deg": 90.0,
    "curve_length_mm": 14.0,
    "view_azimuth_deg": 45.0,
    "view_elevation_deg": 10.0,
    "view_distance_mm": 650.0,
    "fit": "cover",
    "presentation": "plain",
    "zoom": 1.0,
    "pan_x": 0.0,
    "pan_y": 0.0,
}

# Integrating the tangent avoids confusing horizontal distance with arc length.
_QUAD_X, _QUAD_W = np.polynomial.legendre.leggauss(24)
_OUTLINE_INTERVALS = 2048


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def normalize_config(raw):
    """Return validated geometry keys, ignoring unrelated/unknown keys.

    Missing keys use DEFAULT_CONFIG. Booleans and numeric strings are rejected.
    Pan moves content right/up by the projected bounding-box width/height.
    Contain guarantees the full source frame at zoom=1 and zero pan; explicit
    pan or zoom may intentionally move part of that frame outside the screen.
    Invalid views are rejected rather than producing folded/infinite UVs.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("configuration must be a JSON object")
    config = {}
    for key, default in DEFAULT_CONFIG.items():
        value = raw.get(key, default)
        if key == "fit":
            if value not in ("cover", "contain"):
                raise ValueError("fit must be 'cover' or 'contain'")
            config[key] = value
        elif key == "presentation":
            if value not in ("plain", "standee"):
                raise ValueError("presentation must be 'plain' or 'standee'")
            config[key] = value
        else:
            config[key] = _number(value, key)
    for key in ("screen_height_mm", "screen_width_mm"):
        if not 0.001 <= config[key] <= 1_000_000:
            raise ValueError(f"{key} must be between 0.001 and 1000000 mm")
    if not 0 <= config["bend_deg"] < 180:
        raise ValueError("bend_deg must be at least 0 and less than 180")
    if not 0 <= config["curve_length_mm"] <= config["screen_width_mm"]:
        raise ValueError("curve_length_mm must lie within the unfolded width")
    if config["bend_deg"] > 0 and config["curve_length_mm"] <= 0:
        raise ValueError("a bent screen needs a positive G2 transition length")
    if not -135 <= config["view_azimuth_deg"] <= 225:
        raise ValueError("view_azimuth_deg must lie between -135 and 225")
    if not -85 <= config["view_elevation_deg"] <= 85:
        raise ValueError("view_elevation_deg must lie between -85 and 85")
    if not 0.001 <= config["view_distance_mm"] <= 10_000_000:
        raise ValueError("view_distance_mm must lie between 0.001 and 10000000 mm")
    if not 0.01 <= config["zoom"] <= 100:
        raise ValueError("zoom must lie between 0.01 and 100")
    for key in ("pan_x", "pan_y"):
        if not -10 <= config[key] <= 10:
            raise ValueError(f"{key} must lie between -10 and 10")
    _validate_view(config)
    return config


def _basis(config):
    yaw = math.radians(config["view_azimuth_deg"] - 45)
    pitch = math.radians(config["view_elevation_deg"])
    normal = np.array([math.sin(yaw) * math.cos(pitch),
                       -math.cos(yaw) * math.cos(pitch), math.sin(pitch)])
    right = np.array([math.cos(yaw), math.sin(yaw), 0.0])
    up = np.cross(normal, right)
    eye = normal * config["view_distance_mm"]
    return eye, right, up, normal


def camera_basis(config):
    """Return (eye, right, up, normal) as four NumPy XYZ vectors.

    Eye is in millimetres; normal points from the screen centre to the viewer.
    A Blender camera at eye should look along -normal, with local +Y = up.
    """
    return _basis(normalize_config(config))


def _curve_points(config, arcs):
    arcs = np.asarray(arcs, dtype=np.float64).reshape(-1)
    beta = math.radians(config["bend_deg"])
    transition = config["curve_length_mm"]
    if beta == 0:
        return np.column_stack((arcs, np.zeros_like(arcs)))
    magnitude = np.abs(arcs)
    curved = np.minimum(magnitude, transition / 2)
    sample_arc = curved[:, None] * (_QUAD_X[None, :] + 1) / 2
    q = 0.5 + sample_arc / transition
    angle = beta * (q * q * (3 - 2 * q) - 0.5)
    x = curved / 2 * (np.cos(angle) @ _QUAD_W)
    y = curved / 2 * (np.sin(angle) @ _QUAD_W)
    straight = np.maximum(magnitude - transition / 2, 0)
    x += straight * math.cos(beta / 2)
    y += straight * math.sin(beta / 2)
    return np.column_stack((np.copysign(x, arcs), y))


def curve_points(config, arc_positions):
    """Return Nx2 XY points for centred arc positions in [-width/2,width/2].

    This helper takes physical arc positions, not a sample count. The curve
    tangent turns through beta*smoothstep(q); its curvature is zero at both
    joins with straight wings, giving a G2 transition.
    """
    config = normalize_config(config)
    try:
        arcs = np.asarray(arc_positions, dtype=np.float64).reshape(-1)
    except (TypeError, ValueError) as error:
        raise ValueError("arc positions must be finite numbers") from error
    if not np.all(np.isfinite(arcs)) or np.any(np.abs(arcs) > config["screen_width_mm"] / 2 + 1e-10):
        raise ValueError("arc positions lie outside the unfolded screen")
    return _curve_points(config, arcs)


def _minimum_depth(config, normal):
    """Exact candidates for max normal dot P on this convex profile."""
    width, transition = config["screen_width_mm"], config["curve_length_mm"]
    beta = math.radians(config["bend_deg"])
    arcs = [-width / 2, 0.0, width / 2]
    yaw = math.radians(config["view_azimuth_deg"] - 45)
    if beta > 0 and -beta / 2 < yaw < beta / 2:
        target = 0.5 + yaw / beta
        low, high = 0.0, 1.0
        for _ in range(55):
            q = (low + high) / 2
            if q * q * (3 - 2 * q) < target:
                low = q
            else:
                high = q
        arcs.append(transition * ((low + high) / 2 - 0.5))
    horizontal = _curve_points(config, arcs) @ normal[:2]
    farthest = float(np.max(horizontal)) + abs(normal[2]) * config["screen_height_mm"] / 2
    return config["view_distance_mm"] - farthest


def _validate_view(config):
    eye, _, _, normal = _basis(config)
    beta = math.radians(config["bend_deg"])
    ends = _curve_points(config, [-config["screen_width_mm"] / 2,
                                  config["screen_width_mm"] / 2])
    normals = np.array([[-math.sin(beta / 2), -math.cos(beta / 2)],
                        [math.sin(beta / 2), -math.cos(beta / 2)]])
    # For a convex profile with <180 degrees of turn, the two extreme tangent
    # half-planes imply all intermediate tangent half-planes are front-facing.
    facing = np.sum(normals * (eye[:2] - ends), axis=1)
    margin = 1e-8 * max(1.0, config["screen_width_mm"], config["screen_height_mm"],
                        config["view_distance_mm"])
    if np.min(facing) <= margin:
        raise ValueError("viewpoint cannot see the front of both screen wings; projection would fold")
    if _minimum_depth(config, normal) <= margin:
        raise ValueError("screen crosses or approaches the camera plane")


def _project(points, config, basis=None):
    _, right, up, normal = basis if basis is not None else _basis(config)
    distance = config["view_distance_mm"]
    denominator = distance - points @ normal
    if np.any(denominator <= 0):
        raise ValueError("screen crosses the camera plane")
    scale = distance / denominator
    return np.column_stack((points @ right, points @ up)) * scale[:, None]


def _outline(config, basis):
    width, height = config["screen_width_mm"], config["screen_height_mm"]
    transition = config["curve_length_mm"]
    if config["bend_deg"] == 0:
        arcs = np.array([-width / 2, width / 2])
    else:
        arcs = np.unique(np.concatenate(([-width / 2],
                    np.linspace(-transition / 2, transition / 2, _OUTLINE_INTERVALS + 1),
                    [width / 2])))
    profile = _curve_points(config, arcs)
    lower = np.column_stack((profile, np.full(len(arcs), -height / 2)))
    upper = np.column_stack((profile[::-1], np.full(len(arcs), height / 2)))
    return _project(np.concatenate((lower, upper)), config, basis)


def _point_inside(point, polygon):
    start, end = polygon, np.roll(polygon, -1, axis=0)
    crossing = (start[:, 1] > point[1]) != (end[:, 1] > point[1])
    if not np.any(crossing):
        return False
    a, b = start[crossing], end[crossing]
    xs = a[:, 0] + (point[1] - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return bool(np.count_nonzero(xs > point[0]) % 2)


def _rectangle_radius(polygon, center, aspect):
    """Distance to polygon boundary in an aspect-weighted L-infinity norm.

    Twice the result is the largest centred rectangle height. This checks all
    edges, not just four corners, so inward-curving top/bottom borders count.
    """
    start = (polygon - center) / np.array([aspect, 1.0])
    delta = np.roll(start, -1, axis=0) - start
    parameters = [np.zeros(len(start)), np.ones(len(start))]
    for numerator, denominator in (
        (start[:, 0], delta[:, 0]), (start[:, 1], delta[:, 1]),
        (start[:, 0] - start[:, 1], delta[:, 0] - delta[:, 1]),
        (start[:, 0] + start[:, 1], delta[:, 0] + delta[:, 1]),
    ):
        parameter = np.zeros(len(start))
        np.divide(-numerator, denominator, out=parameter, where=np.abs(denominator) > 1e-30)
        parameters.append(np.clip(parameter, 0, 1))
    parameters = np.stack(parameters, axis=1)
    candidates = start[:, None, :] + parameters[:, :, None] * delta[:, None, :]
    return float(np.min(np.max(np.abs(candidates), axis=2)))


def _outline_error(config, basis, aspect):
    """Conservative weighted chord-error bound for the smooth outline.

    Straight edges project to exact straight edges. On the transition, bound
    each coordinate's second derivative and use M*delta_s**2/8. Subtracting
    this from the inscribed radius keeps the rectangle inside the continuous
    curve as well as the sampled polygon, without a visible arbitrary crop.
    """
    beta = math.radians(config["bend_deg"])
    if beta == 0:
        return 0.0
    transition = config["curve_length_mm"]
    curvature = 1.5 * beta / transition
    depth = _minimum_depth(config, basis[3])
    radius = math.hypot(config["screen_width_mm"] / 2, config["screen_height_mm"] / 2)
    distance = config["view_distance_mm"]
    second_derivative = distance * (curvature / depth + 2 / depth**2
                                    + radius * curvature / depth**2 + 2 * radius / depth**3)
    return second_derivative * (transition / _OUTLINE_INTERVALS)**2 / 8 * max(1.0, 1 / aspect)


def _source_frame(config, aspect, polygon, basis):
    lower, upper = polygon.min(axis=0), polygon.max(axis=0)
    # Sampled extrema can miss a smooth edge extremum between samples. Expand
    # by the proved chord-error bound so cover never exposes a black sliver.
    outline_error = _outline_error(config, basis, 1.0)
    lower -= outline_error
    upper += outline_error
    size = upper - lower
    center = (lower + upper) / 2
    standee = config["fit"] == "contain" and config["presentation"] == "standee"
    if config["fit"] == "cover":
        # A few machine epsilons keep exact edge contacts inside after the
        # aspect multiplication and UV division (invisible numerical padding).
        height = max(size[1], size[0] / aspect) * (1 + 8 * np.finfo(float).eps)
    else:
        if not _point_inside(center, polygon):
            # The optical-axis point is always inside a valid visible screen.
            center = np.zeros(2)
        # Standee decoration occupies U=[-.15/aspect,1+.15/aspect] and
        # V=[-.30,1.14]. Fit its entire 1.44-high outer frame, then recover
        # the source-video dimensions and reserve one percent safety space.
        fitting_aspect = (aspect + 0.30) / 1.44 if standee else aspect
        radius = _rectangle_radius(polygon, center, fitting_aspect)
        radius -= _outline_error(config, basis, fitting_aspect)
        if radius <= 0:
            raise ValueError("viewpoint leaves no numerically stable inscribed video frame")
        height = 2 * radius * 0.99 / 1.44 if standee else 2 * radius
    dimensions = np.array([height * aspect, height]) * config["zoom"]
    if standee:
        # The decoration's vertical UV centre is .42, so raise the video
        # centre by .08 video heights to keep the whole decoration centred.
        center[1] += 0.08 * dimensions[1]
    center += np.array([config["pan_x"], config["pan_y"]]) * size
    return center, dimensions, np.concatenate((lower, upper))


def constrain_framing(raw, source_aspect):
    """Validate and return the effective framing for a given source aspect.

    Cover always fills the complete visible screen: zoom is at least one and
    pan is restricted to the excess source width/height at that zoom. Contain
    preserves explicit free framing. The caller's dictionary is not mutated.
    Applying this function again to its result leaves the config unchanged.
    """
    config = normalize_config(raw)
    aspect = _number(source_aspect, "source_aspect")
    if not 0.0001 <= aspect <= 10000:
        raise ValueError("source_aspect must lie between 0.0001 and 10000")
    if config["fit"] == "contain":
        return config
    config["zoom"] = max(1.0, config["zoom"])
    basis = _basis(config)
    _, dimensions, bounds = _source_frame(config, aspect, _outline(config, basis), basis)
    size = bounds[2:] - bounds[:2]
    ratios = dimensions / size
    limit = np.maximum(0.0, (ratios - 1.0) / 2)
    # Back away from the edge by an arithmetic roundoff bound. This is much
    # less than a pixel and prevents black lines at the end of a drag gesture.
    limit = np.maximum(0.0, limit - 16 * np.finfo(float).eps * np.maximum(1.0, ratios))
    for index, key in enumerate(("pan_x", "pan_y")):
        config[key] = float(np.clip(config[key], -limit[index], limit[index])) if limit[index] else 0.0
    return config


def _boundary_indices(columns, rows):
    stride = columns + 1
    return (list(range(stride))
            + [row * stride + columns for row in range(1, rows + 1)]
            + [rows * stride + col for col in range(columns - 1, -1, -1)]
            + [row * stride for row in range(rows - 1, 0, -1)])


def mapping_mesh(config, source_aspect, columns=192, rows=64):
    """Build the shared mesh; columns and rows count subdivision intervals.

    Vertices are row-major, left-to-right then bottom-to-top. Counts are
    (columns+1)*(rows+1) vertices and 2*columns*rows front-facing triangles.
    boundary_uv starts at bottom-left and proceeds counterclockwise, with no
    repeated closing point. Bounds are [xmin,ymin,xmax,ymax] in the plane
    through the screen origin perpendicular to the viewing axis (millimetres),
    conservatively expanded by the continuous-curve chord-error bound.
    The extra config field contains the effective constrained configuration.
    Cover UV stays within 0..1. In contain mode, source UV may extend beyond
    0..1 and consumers must render those pixels black.
    """
    config = constrain_framing(config, source_aspect)
    aspect = _number(source_aspect, "source_aspect")
    for value, name in ((columns, "columns"), (rows, "rows")):
        if isinstance(value, bool) or not isinstance(value, Integral) or not 1 <= value <= 8192:
            raise ValueError(f"{name} must be an integer between 1 and 8192")
    columns, rows = int(columns), int(rows)
    if (columns + 1) * (rows + 1) > 2_000_000:
        raise ValueError("mapping grid exceeds the 2000000 vertex limit")
    basis = _basis(config)
    u = np.linspace(0.0, 1.0, columns + 1)
    v = np.linspace(0.0, 1.0, rows + 1)
    profile = _curve_points(config, (u - 0.5) * config["screen_width_mm"])
    positions = np.column_stack((np.tile(profile, (rows + 1, 1)),
                                 np.repeat((v - 0.5) * config["screen_height_mm"], columns + 1)))
    atlas = np.column_stack((np.tile(u, rows + 1), np.repeat(v, columns + 1)))
    projected = _project(positions, config, basis)
    polygon = _outline(config, basis)
    center, dimensions, bounds = _source_frame(config, aspect, polygon, basis)
    uv = (projected - center) / dimensions + 0.5
    lower_left = (np.arange(rows)[:, None] * (columns + 1) + np.arange(columns)[None, :]).ravel()
    first = np.column_stack((lower_left, lower_left + 1, lower_left + columns + 1))
    second = np.column_stack((lower_left + 1, lower_left + columns + 2, lower_left + columns + 1))
    triangles = np.stack((first, second), axis=1).reshape(-1, 3)
    return {
        "positions": positions.tolist(),
        "atlas": atlas.tolist(),
        "uv": uv.tolist(),
        "projected": projected.tolist(),
        "triangles": triangles.tolist(),
        "boundary_uv": uv[_boundary_indices(columns, rows)].tolist(),
        "bounds": bounds.tolist(),
        "config": config,
    }
