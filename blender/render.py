"""Run inside Blender 4.5: build an inspectable workflow and stream lossless frames."""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mapper.geometry import mapping_mesh, normalize_config, camera_basis
from mapper.encoding import encoder_command
from mapper.storage import write_json as save_json
from mapper.output import ASPECT, render_size


def emission_material(name, image, frames, uv_name, decoration_path=None, source_aspect=1):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial"); output.location = (540, 0)
    emission = nodes.new("ShaderNodeEmission"); emission.location = (320, 0)
    texture = nodes.new("ShaderNodeTexImage"); texture.location = (-80, 0)
    texture.image, texture.interpolation, texture.extension = image, "Cubic", "CLIP"
    texture.image_user.frame_start = 1
    texture.image_user.frame_duration = frames
    texture.image_user.use_auto_refresh = True
    uv = nodes.new("ShaderNodeUVMap"); uv.uv_map = uv_name; uv.location = (-340, 0)
    links = material.node_tree.links
    links.new(uv.outputs["UV"], texture.inputs["Vector"])
    if decoration_path:
        decoration = nodes.new("ShaderNodeTexImage"); decoration.location = (-80, -300)
        decoration.image = bpy.data.images.load(decoration_path, check_existing=True)
        decoration.image.pack()
        decoration.interpolation, decoration.extension = "Cubic", "CLIP"
        offset = nodes.new("ShaderNodeVectorMath"); offset.operation = "ADD"
        offset.inputs[1].default_value = (.15/source_aspect, .30, 0)
        scale = nodes.new("ShaderNodeVectorMath"); scale.operation = "MULTIPLY"
        scale.inputs[1].default_value = (1/(1+.30/source_aspect), 1/1.44, 1)
        links.new(uv.outputs["UV"], offset.inputs[0]); links.new(offset.outputs[0], scale.inputs[0])
        links.new(scale.outputs[0], decoration.inputs["Vector"])
        mix = nodes.new("ShaderNodeMixRGB"); mix.blend_type = "MIX"
        links.new(texture.outputs["Alpha"], mix.inputs[0])
        links.new(decoration.outputs["Color"], mix.inputs[1]); links.new(texture.outputs["Color"], mix.inputs[2])
        source_color = mix.outputs[0]
    else:
        source_color = texture.outputs["Color"]
    # FLOAT_COLOR stores this scalar in linear light. Apply it after the source
    # and standee frame are composed, before Standard converts linear to sRGB.
    gain = nodes.new("ShaderNodeAttribute"); gain.attribute_name = "BrightnessGain"
    gain.label = "Experimental angular brightness gain (linear)"
    gain.location = (-80, -520)
    multiply = nodes.new("ShaderNodeMixRGB"); multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1
    multiply.use_clamp = False
    multiply.location = (140, 0)
    links.new(source_color, multiply.inputs[1])
    links.new(gain.outputs["Color"], multiply.inputs[2])
    links.new(multiply.outputs[0], emission.inputs["Color"])
    links.new(emission.outputs[0], output.inputs["Surface"])
    return material


def mesh_object(scene, name, vertices, triangles, panel_uv, source_uv, material, brightness_gain):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, [], triangles)
    data.update()
    for uv_name, coordinates in (("PanelUV", panel_uv), ("SourceUV", source_uv)):
        layer = data.uv_layers.new(name=uv_name)
        for loop in data.loops:
            layer.data[loop.index].uv = coordinates[loop.vertex_index]
    data.uv_layers.active = data.uv_layers["SourceUV"]
    gain = data.color_attributes.new(name="BrightnessGain", type="FLOAT_COLOR", domain="POINT")
    gain.data.foreach_set("color", [component for value in brightness_gain for component in (value, value, value, 1)])
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.data.materials.append(material)
    return obj


def setup_scene(scene, width, height, options):
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "16"
    scene.render.image_settings.compression = 10
    scene.render.film_transparent = False
    scene.render.dither_intensity = 0
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure, scene.view_settings.gamma = 0, 1
    if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = 16
    rate = options["fps_num"] / options["fps_den"]
    scene.render.fps, scene.render.fps_base = round(rate), round(rate) / rate
    scene.frame_start, scene.frame_end = 1, options["frames"]
    world = bpy.data.worlds.new(scene.name + "_Black")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0, 0, 0, 1)
    scene.world = world


def add_camera(scene, name, location):
    camera = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    scene.collection.objects.link(camera)
    camera.location = location
    scene.camera = camera
    camera.data.clip_start, camera.data.clip_end = 0.001, 100
    return camera


def build(settings):
    config, options = normalize_config(settings["config"]), settings["options"]
    # Grid convergence is checked by the external verification suite.
    mesh = mapping_mesh(config, settings["source_aspect"], columns=512, rows=128)
    image = bpy.data.images.load(settings["source_path"], check_existing=False)
    if settings["source_kind"] == "video":
        image.source = "MOVIE"
    image.colorspace_settings.name = "sRGB"
    image.alpha_mode = "NONE"
    if settings["source_kind"] == "image":
        image.pack()
    material = emission_material("Projected_Source_Emission", image, options["frames"], "SourceUV",
                                 settings.get("decoration_path"), settings["source_aspect"])
    uncorrected = emission_material("Uncorrected_Source_Emission", image, options["frames"], "PanelUV")
    atlas = bpy.context.scene
    atlas.name = "01_EXPORT_Unfolded"
    for obj in list(atlas.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scale = options["supersample"]
    setup_scene(atlas, *render_size(scale), options)
    aspect = ASPECT
    vertices = [((u-.5)*aspect, v-.5, 0) for u, v in mesh["atlas"]]
    mesh_object(atlas, "Unfolded_Pixel_Surface", vertices, mesh["triangles"], mesh["atlas"], mesh["uv"], material,
                mesh["brightness_gain"])
    camera = add_camera(atlas, "Export_Orthographic", (0, 0, 3))
    camera.data.type, camera.data.sensor_fit, camera.data.ortho_scale = "ORTHO", "HORIZONTAL", aspect
    eye, right, up, normal = camera_basis(config)
    bounds = mesh["bounds"]
    simulated_gain = [gain * response for gain, response in zip(mesh["brightness_gain"], mesh["angular_response"])]
    for name, mat, gains in (("02_TARGET_Corrected", material, mesh["brightness_gain"]),
                             ("03_TARGET_Uncorrected", uncorrected, mesh["brightness_gain"]),
                             ("04_TARGET_Simulated", material, simulated_gain)):
        scene = bpy.data.scenes.new(name)
        setup_scene(scene, 1400, 1000, options)
        mesh_object(scene, "Curved_Screen_mm", [[c*.001 for c in p] for p in mesh["positions"]],
                    mesh["triangles"], mesh["atlas"], mesh["uv"], mat, gains)
        cam = add_camera(scene, "Target_Eye", [c*.001 for c in eye])
        cam.rotation_euler = (-cam.location).to_track_quat("-Z", "Y").to_euler()
        view_width = max(bounds[2]-bounds[0], (bounds[3]-bounds[1])*1.4) * 1.15
        cam.data.sensor_fit = "HORIZONTAL"
        cam.data.lens = config["view_distance_mm"] * 36 / view_width
        cam.data.sensor_width = 36
    atlas["mapping_config_json"] = json.dumps(config, ensure_ascii=False)
    atlas["brightness_summary_json"] = json.dumps(mesh["brightness_summary"], ensure_ascii=False)
    atlas["instructions"] = "01 exports the unfolded texture; 02/03 compare geometry from the design eye; 04 simulates angular panel luminance and is not exported."
    text = bpy.data.texts.new("READ_ME")
    text.write("PANORAMA Mapper\n\n01_EXPORT_Unfolded is the delivery texture, not a photograph of the cooler.\n"
               "02_TARGET_Corrected and 03_TARGET_Uncorrected compare the viewing result.\n"
               "BrightnessGain is a linear scalar applied to video AND decoration in 01/02/03.\n"
               "04_TARGET_Simulated additionally applies the empirical angular response; this is a model, not a panel measurement.\n"
               "Standard / emission preserve the source appearance. Both UV layers are explicit.\n"
               "Rebuild mesh after geometry or viewing changes using the local panel.\n\n" + json.dumps(settings, ensure_ascii=False, indent=2))
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.region_3d.view_perspective = "CAMERA"
                area.spaces.active.shading.type = "MATERIAL"
    return atlas


def main():
    config_path = Path(sys.argv[sys.argv.index("--") + 1])
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    folder = Path(settings["directory"])
    options = settings["options"]
    progress_path = folder / "progress.json"
    save_json(progress_path, {"frame": 0, "message": "建立弧长曲面、投影 UV 与预览场景"})
    scene = build(settings)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "panorama.blend"))
    if settings.get('build_only'):
        save_json(progress_path,{'frame':0,'message':'Blender 工程已生成，启动 GPU 视频映射'})
        return
    extension = ".mp4" if options["quality"] == "high" else ".mkv"
    final_path = Path(settings.get('candidate_path',str(folder / (options["name"] + extension))))
    final_path.parent.mkdir(parents=True,exist_ok=True)
    partial_path = final_path.with_name(final_path.stem + '.partial' + extension)
    frame_path = folder / "work" / "current-frame.png"
    frame_path.parent.mkdir(exist_ok=True)
    command = encoder_command(settings["ffmpeg_path"], partial_path, options)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (folder / "encode.log").open("wb") as log:
        encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, creationflags=flags)
        try:
            still_frame_bytes = None
            for frame in range(1, options["frames"] + 1):
                if (folder / "cancel.request").exists():
                    raise InterruptedError("Cancelled")
                if still_frame_bytes is None:
                    scene.frame_set(frame)
                    scene.render.filepath = str(frame_path)
                    bpy.ops.render.render(write_still=True, scene=scene.name)
                    frame_bytes = frame_path.read_bytes()
                    if settings["source_kind"] == "image":
                        still_frame_bytes = frame_bytes
                else:
                    frame_bytes = still_frame_bytes
                if frame == 1:
                    # Retain a lossless reviewable first frame alongside the MP4.
                    (folder / "first-frame.png").write_bytes(frame_bytes)
                encoder.stdin.write(frame_bytes)
                save_json(progress_path, {"frame": frame, "message": f"Blender 渲染 {frame} / {options['frames']}"})
            encoder.stdin.close()
            if encoder.wait() != 0:
                raise RuntimeError("FFmpeg encoder failed; see encode.log")
            if (folder / "cancel.request").exists():
                raise InterruptedError("Cancelled")
            partial_path.replace(final_path)
            frame_path.unlink(missing_ok=True)
            save_json(progress_path, {"frame": options["frames"], "message": "完成"})
        except BaseException:
            if encoder.poll() is None:
                encoder.terminate()
            encoder.wait()
            partial_path.unlink(missing_ok=True)
            raise


if __name__ == "__main__":
    main()
