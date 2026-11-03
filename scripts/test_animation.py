
import bpy
import math
import os

# =====================================
# 1. 基本設定
# =====================================

BASE_DIR = r"C:\Users\LCSBeniLinT14\Desktop\AI-Animation"

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 清除場景
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

scene = bpy.context.scene

# 渲染引擎
scene.render.engine = 'BLENDER_EEVEE'

# 影片規格
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.resolution_percentage = 50
scene.render.fps = 30

scene.frame_start = 1
scene.frame_end = 150

# =====================================
# 2. 建立背景
# =====================================

bpy.ops.mesh.primitive_plane_add(
    size=20,
    location=(0, 2, 0),
    rotation=(math.radians(90), 0, 0)
)

background = bpy.context.object
background.name = "Background"

mat_bg = bpy.data.materials.new("Background Material")
mat_bg.diffuse_color = (0.015, 0.025, 0.08, 1)

mat_bg.use_nodes = True
nodes = mat_bg.node_tree.nodes
nodes.clear()

output = nodes.new("ShaderNodeOutputMaterial")
emission = nodes.new("ShaderNodeEmission")

emission.inputs["Color"].default_value = (
    0.015, 0.025, 0.08, 1
)

mat_bg.node_tree.links.new(
    emission.outputs["Emission"],
    output.inputs["Surface"]
)

background.data.materials.append(mat_bg)

# =====================================
# 3. 建立動畫物件
# =====================================

bpy.ops.mesh.primitive_uv_sphere_add(
    segments=32,
    ring_count=16,
    radius=1,
    location=(-4, 0, 0)
)

ball = bpy.context.object
ball.name = "Animated Object"

# 發光材質
mat = bpy.data.materials.new("Glow Material")
mat.diffuse_color = (0.1, 0.4, 1, 1)
mat.use_nodes = True

nodes = mat.node_tree.nodes
nodes.clear()

output = nodes.new("ShaderNodeOutputMaterial")
emission = nodes.new("ShaderNodeEmission")

emission.inputs["Color"].default_value = (
    0.05, 0.3, 1, 1
)

emission.inputs["Strength"].default_value = 3

mat.node_tree.links.new(
    emission.outputs["Emission"],
    output.inputs["Surface"]
)

ball.data.materials.append(mat)

# =====================================
# 4. 設定動畫
# =====================================

ball.location = (-4, 0, 0)
ball.keyframe_insert(
    data_path="location",
    frame=1
)

ball.location = (4, 0, 0)
ball.keyframe_insert(
    data_path="location",
    frame=150
)

# =====================================
# 5. 建立攝影機
# =====================================

bpy.ops.object.camera_add(
    location=(0, -12, 0)
)

camera = bpy.context.object
camera.name = "Camera"

camera.data.type = 'ORTHO'
camera.data.ortho_scale = 12

camera.rotation_euler = (
    math.radians(90),
    0,
    0
)

scene.camera = camera

# =====================================
# 6. 設定影片輸出
# =====================================

import subprocess

# 先輸出 PNG 影格
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGB'
scene.render.filepath = os.path.join(OUTPUT_DIR, "frame_")

print("Starting PNG rendering...")
bpy.ops.render.render(animation=True)
print("PNG rendering completed!")

# 使用 FFmpeg 合成 MP4
ffmpeg_path = "ffmpeg"

input_pattern = os.path.join(OUTPUT_DIR, "frame_%04d.png")
output_video = os.path.join(OUTPUT_DIR, "test.mp4")

command = [
    ffmpeg_path,
    "-y",
    "-framerate", "30",
    "-i", input_pattern,
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-crf", "20",
    output_video
]

print("Starting MP4 encoding...")
subprocess.run(command, check=True)

print("Animation rendering completed!")
print("Video:", output_video)

