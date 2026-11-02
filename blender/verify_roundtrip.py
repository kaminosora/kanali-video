"""Reproject the actual encoded MP4 frame onto PanelUV without saving the blend."""
import bpy
from pathlib import Path

folder = Path(bpy.data.filepath).parent
scene = bpy.data.scenes["02_TARGET_Corrected"]
obj = next(o for o in scene.objects if o.type == "MESH")
material = bpy.data.materials.new("Actual_Encoded_Frame_Roundtrip")
material.use_nodes = True
nodes = material.node_tree.nodes
nodes.clear()
uv = nodes.new("ShaderNodeUVMap"); uv.uv_map = "PanelUV"
image = nodes.new("ShaderNodeTexImage")
image.image = bpy.data.images.load(str(folder / "decoded-first-frame.png"))
image.interpolation, image.extension = "Cubic", "CLIP"
emission = nodes.new("ShaderNodeEmission")
output = nodes.new("ShaderNodeOutputMaterial")
links = material.node_tree.links
links.new(uv.outputs[0], image.inputs["Vector"])
links.new(image.outputs["Color"], emission.inputs["Color"])
links.new(emission.outputs[0], output.inputs["Surface"])
obj.data.materials.clear(); obj.data.materials.append(material)
scene.frame_set(1)
scene.render.filepath = str(folder / "observer-roundtrip.png")
bpy.ops.render.render(write_still=True, scene=scene.name)
