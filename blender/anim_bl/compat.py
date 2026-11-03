"""不同 Blender 版本之間的 API 差異集中在這裡處理（支援 4.2 LTS 以上）。"""

import bpy

MIN_VERSION = (4, 2, 0)


def check_version():
    if bpy.app.version < MIN_VERSION:
        raise RuntimeError(
            f"Blender {bpy.app.version_string} 太舊，最低需要 {'.'.join(map(str, MIN_VERSION))}"
        )


def set_engine(scene, name):
    """抽象名稱（eevee / cycles）→ 實際的引擎識別名稱。回傳實際設定的名稱。"""
    candidates = {
        "eevee": ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"],  # 5.x / 4.2~4.x
        "cycles": ["CYCLES"],
    }[name]
    for engine in candidates:
        try:
            scene.render.engine = engine
            return engine
        except TypeError:
            continue
    raise RuntimeError(f"這個 Blender 版本不支援引擎 {name!r}（嘗試過 {candidates}）")


def set_samples(scene, engine, samples):
    if engine == "CYCLES":
        scene.cycles.samples = samples
    else:
        scene.eevee.taa_render_samples = samples


def configure_cycles(scene, layer_count):
    # 每疊一層透明圖層就會用掉一次 transparent bounce
    scene.cycles.transparent_max_bounces = max(8, layer_count + 4)
    scene.cycles.device = "CPU"


def ensure_nodes(idblock):
    # 5.x 的材質 / 世界一律使用節點；舊版需要手動開啟
    if hasattr(idblock, "use_nodes") and not idblock.use_nodes:
        idblock.use_nodes = True


def set_blended(material):
    """讓材質以 alpha 混合方式渲染。"""
    if hasattr(material, "surface_render_method"):  # 4.2+
        material.surface_render_method = "BLENDED"
    else:
        material.blend_method = "BLEND"


def fcurve_for(obj, data_path, index):
    """取得（或建立）obj 某個屬性分量的 F-Curve。

    4.4 以後是 slotted / layered action，5.x 已經移除舊的 action.fcurves。
    """
    anim = obj.animation_data or obj.animation_data_create()
    if anim.action is None:
        anim.action = bpy.data.actions.new(f"{obj.name}_action")
    action = anim.action
    if hasattr(action, "fcurve_ensure_for_datablock"):  # 4.4+
        return action.fcurve_ensure_for_datablock(obj, data_path, index=index)
    fcurve = action.fcurves.find(data_path, index=index)
    return fcurve or action.fcurves.new(data_path, index=index)
