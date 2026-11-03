"""PNG 圖層 → 平面物件，每個圖層有自己的材質。

平面的原點 = anchor（也是縮放 / 旋轉的 pivot，docs/spec.md 2.2）。
最終 alpha = 圖片 alpha × 物件 color[3]（Object Info 節點），
所以透明度動畫的關鍵影格是打在物件上，不會影響其他圖層。
"""

import bpy

from anim_bl import compat
from anim_bl.units import Units

# 頂點順序：左下、右下、右上、左上（法線朝 -Y，也就是朝向攝影機）
_UVS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def _make_mesh(name, width_bu, height_bu, anchor):
    ax, ay = anchor
    x0, x1 = -ax * width_bu, (1 - ax) * width_bu
    z_top, z_bottom = ay * height_bu, -(1 - ay) * height_bu
    verts = [(x0, 0, z_bottom), (x1, 0, z_bottom), (x1, 0, z_top), (x0, 0, z_top)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop in mesh.loops:
        uv.data[loop.index].uv = _UVS[loop.vertex_index]
    mesh.update()
    return mesh


def _make_material(name, image):
    mat = bpy.data.materials.new(name)
    compat.ensure_nodes(mat)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()

    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Linear"
    tex.extension = "CLIP"

    info = nodes.new("ShaderNodeObjectInfo")
    alpha = nodes.new("ShaderNodeMath")
    alpha.operation = "MULTIPLY"
    alpha.use_clamp = True

    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1.0
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    mix = nodes.new("ShaderNodeMixShader")
    output = nodes.new("ShaderNodeOutputMaterial")

    links.new(tex.outputs["Color"], emission.inputs["Color"])
    links.new(tex.outputs["Alpha"], alpha.inputs[0])
    links.new(info.outputs["Alpha"], alpha.inputs[1])
    links.new(alpha.outputs["Value"], mix.inputs["Fac"])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(emission.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])

    compat.set_blended(mat)
    mat.use_backface_culling = False
    return mat


def build_layer(scene, layer, units: Units):
    name = layer["id"]
    image = bpy.data.images.load(layer["image"], check_existing=False)
    image.name = f"img_{name}"
    image.alpha_mode = "STRAIGHT"
    image.colorspace_settings.name = "sRGB"

    width_px, height_px = layer["size"]
    mesh = _make_mesh(f"mesh_{name}", units.length(width_px), units.length(height_px), layer["anchor"])
    mesh.materials.append(_make_material(f"mat_{name}", image))

    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)

    x, z = units.to_xz(*layer["position"])
    obj.location = (x, units.depth(layer["rank"]), z)
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = (0.0, units.rotation(layer["rotation"]), 0.0)
    sx, sy = layer["scale"]
    obj.scale = (sx, 1.0, sy)
    obj.color = (1.0, 1.0, 1.0, layer["opacity"])
    return obj
