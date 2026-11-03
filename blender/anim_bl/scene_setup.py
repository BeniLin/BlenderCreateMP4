"""場景基本設定：清空場景、解析度、fps、引擎、色彩管理、背景色、正交攝影機。"""

import math

import bpy

from anim_bl import compat
from anim_bl.units import CAMERA_DISTANCE, Units


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras,
                       bpy.data.lights, bpy.data.images, bpy.data.actions):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)


def setup_scene(resolved, layer_count):
    scene = bpy.context.scene
    canvas = resolved["canvas"]
    render_cfg = resolved["render"]

    scene.render.resolution_x = canvas["width"]
    scene.render.resolution_y = canvas["height"]
    scene.render.resolution_percentage = render_cfg["resolution_percentage"]
    scene.render.pixel_aspect_x = 1
    scene.render.pixel_aspect_y = 1
    scene.render.fps = canvas["fps"]
    scene.render.fps_base = 1
    scene.frame_start = canvas["frame_start"]
    scene.frame_end = canvas["frame_end"]

    engine = compat.set_engine(scene, render_cfg["engine"])
    compat.set_samples(scene, engine, render_cfg["samples"])
    if engine == "CYCLES":
        compat.configure_cycles(scene, layer_count)

    # 保留原圖顏色：Standard、無 look、無曝光調整、無 dither 雜訊
    scene.view_settings.view_transform = render_cfg["view_transform"]
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.render.dither_intensity = 0
    scene.render.film_transparent = False

    _setup_world(scene, canvas["background_color"])
    units = Units(canvas["width"], canvas["height"])
    _setup_camera(scene, units)
    return scene, engine, units


def _setup_world(scene, rgba):
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    compat.ensure_nodes(world)
    nodes = world.node_tree.nodes
    nodes.clear()
    background = nodes.new("ShaderNodeBackground")
    output = nodes.new("ShaderNodeOutputWorld")
    r, g, b, _a = rgba
    background.inputs["Color"].default_value = (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), 1)
    background.inputs["Strength"].default_value = 1.0
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])


def _setup_camera(scene, units):
    data = bpy.data.cameras.new("Camera")
    data.type = "ORTHO"
    data.ortho_scale = units.ortho_scale
    data.sensor_fit = "AUTO"
    data.clip_start = 0.1
    data.clip_end = CAMERA_DISTANCE * 2
    cam = bpy.data.objects.new("Camera", data)
    scene.collection.objects.link(cam)
    cam.location = (0, -CAMERA_DISTANCE, 0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.camera = cam
    return cam
