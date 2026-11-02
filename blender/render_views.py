"""Render the target-eye comparison scenes from a generated .blend."""
import bpy
from pathlib import Path

folder = Path(bpy.data.filepath).parent
for scene_name, filename in (("02_TARGET_Corrected", "observer-corrected.png"),
                             ("03_TARGET_Uncorrected", "observer-uncorrected.png")):
    scene = bpy.data.scenes[scene_name]
    scene.frame_set(1)
    scene.render.filepath = str(folder / filename)
    bpy.ops.render.render(write_still=True, scene=scene.name)
