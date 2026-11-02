"""KANALI upload raster, independent of physical screen dimensions in millimetres."""
WIDTH = 2160
HEIGHT = 1080
ASPECT = WIDTH / HEIGHT


def profile():
    return {'width': WIDTH, 'height': HEIGHT, 'aspect': ASPECT, 'label': 'KANALI 2:1'}


def render_size(supersample=1):
    return WIDTH * supersample, HEIGHT * supersample
