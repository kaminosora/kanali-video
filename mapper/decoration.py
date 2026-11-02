"""Procedural standee frame in the same source-UV plane as the video.

This is an optical styling layer, not stereo depth reconstruction. The frame,
edge faces and stand supply fixed-view depth cues while preserving every video
pixel inside UV [0,1]. Both Blender and WebGL consume this exact raster asset.
"""
import hashlib
import math
import threading
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

_PUBLISH_LOCK = threading.Lock()


def standee_asset(source_aspect, directory):
    aspect = float(source_aspect)
    if not math.isfinite(aspect) or not 0.01 <= aspect <= 100:
        raise ValueError("Invalid standee aspect")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"standee-v1:{aspect:.9f}".encode()).hexdigest()[:16]
    target = directory / (key + ".png")
    if target.exists():
        return target
    mx = .15 / aspect
    height = 2160
    width = max(128, min(8192, round(height * (aspect + .30) / 1.44)))
    image = Image.new("RGB", (width, height), (0, 0, 0))
    def xy(u, v):
        return ((u+mx)/(1+2*mx)*width, (1.14-v)/1.44*height)
    def box(u0, v0, u1, v1):
        x0, y0 = xy(u0, v1); x1, y1 = xy(u1, v0)
        return (x0, y0, x1, y1)
    # A very subdued floor pool and contact shadow anchor the little screen.
    floor = Image.new("RGB", image.size)
    draw = ImageDraw.Draw(floor)
    draw.ellipse(box(.5-.48/aspect, -.285, .5+.48/aspect, -.16), fill=(23, 34, 40))
    floor = floor.filter(ImageFilter.GaussianBlur(height*.012))
    image.paste(floor)
    draw = ImageDraw.Draw(image)
    draw.ellipse(box(.5-.33/aspect, -.252, .5+.36/aspect, -.195), fill=(2, 4, 6))
    # Stand, bevelled base, and an offset case create consistent depth cues.
    draw.polygon([xy(.5-.055/aspect, -.02), xy(.5+.055/aspect, -.02),
                  xy(.5+.075/aspect, -.20), xy(.5-.075/aspect, -.20)], fill=(36, 53, 62))
    draw.line([xy(.5-.04/aspect, -.04), xy(.5-.055/aspect, -.185)], fill=(92, 125, 133), width=2)
    draw.polygon([xy(.5-.24/aspect, -.178), xy(.5+.24/aspect, -.178),
                  xy(.5+.33/aspect, -.228), xy(.5-.33/aspect, -.228)], fill=(45, 64, 73))
    draw.polygon([xy(.5-.33/aspect, -.228), xy(.5+.33/aspect, -.228),
                  xy(.5+.33/aspect, -.245), xy(.5-.33/aspect, -.245)], fill=(18, 29, 35))
    draw.line([xy(.5-.33/aspect, -.228), xy(.5+.33/aspect, -.228)], fill=(103, 139, 146), width=2)
    bx, by = .032/aspect, .032
    dx, dy = .025/aspect, -.022
    draw.polygon([xy(1+bx, 1+by), xy(1+bx+dx, 1+by+dy),
                  xy(1+bx+dx, -by+dy), xy(1+bx, -by)], fill=(56, 79, 88))
    draw.polygon([xy(-bx, -by), xy(1+bx, -by), xy(1+bx+dx, -by+dy),
                  xy(-bx+dx, -by+dy)], fill=(29, 43, 52))
    draw.rounded_rectangle(box(-bx, -by, 1+bx, 1+by), radius=height*.010,
                           fill=(23, 35, 42), outline=(81, 110, 117), width=2)
    draw.line([xy(-bx+.01/aspect, 1+by-.004), xy(1+bx-.01/aspect, 1+by-.004)], fill=(150, 182, 184), width=2)
    draw.rectangle(box(0, 0, 1, 1), fill=(0, 0, 0))
    draw.line([xy(.5-.06/aspect, -.018), xy(.5+.06/aspect, -.018)], fill=(113, 224, 209), width=3)
    temporary = target.with_name(target.stem + "." + uuid.uuid4().hex + ".tmp.png")
    image.save(temporary, compress_level=6)
    # Concurrent initial previews can finish in any order; readers only see a
    # complete file, and no caller overwrites an asset already being served.
    with _PUBLISH_LOCK:
        if target.exists():
            temporary.unlink(missing_ok=True)
        else:
            temporary.replace(target)
    return target
