"""Godot sprites: render the animated GLB's clips from the camera preset's
directions with Eevee, a toon shader and an outline, on a transparent background.

Arguments (args.json)::

    {"animated_glb": "animated.glb", "out_dir": "raw",
     "clips": [{"name": "walk", "duration": 1.0, "loop": true}],
     "directions": [{"name": "right", "yaw": 90}], "tilt": 0,
     "size": 512, "frame_size": 256, "fps": 12, "outline": 1.0}

``size`` is the rendered frame's side (``frame_size``, the final one, times the
supersample); ``outline`` is the outline's width in final pixels (0 for none).
The character faces glTF +Z (Blender -Y); ``yaw`` turns the camera around it
(0 looks at its face, 90 at its right-facing side), ``tilt`` raises the camera
by that many degrees.

One orthographic framing is used for every frame, clip and direction: the
camera looks at the origin (the feet), keeps it centred left to right at the
same height, and its scale holds the animation's full bounds over all clips
and directions. So the character keeps one size and ground line everywhere.

Frames are sampled at ``fps`` per clip (``round(duration * fps)`` of them)
and written to ``out_dir/<direction>/<clip>/<n>.png``.

Result: ``{"frames": {direction: {clip: [paths]}}, "ortho_scale": float}``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402
from mathutils import Euler, Vector  # noqa: E402

from _common import empty_scene, import_gltf, progress, run  # noqa: E402

MARGIN = 1.06  # room around the animation's bounds
TOON_STEPS = ((0.0, 0.45), (0.35, 0.75), (0.7, 1.0))  # (light from, brightness): three flat bands
OUTLINE_COLOR = (0.03, 0.025, 0.03, 1.0)


# ---- clips ------------------------------------------------------------------


def clip_tracks(objects) -> dict[str, list]:
    """``{clip: [NLA tracks]}``: the glTF importer puts each animation on an NLA track named after it."""
    tracks: dict[str, list] = {}
    for obj in objects:
        data = obj.animation_data
        if data is None:
            continue
        if data.action is not None:  # the importer also makes the first clip active; the tracks hold them all
            data.action = None
        for track in data.nla_tracks:
            names = {track.name, *(strip.action.name for strip in track.strips if strip.action)}
            for name in names:
                tracks.setdefault(name, []).append(track)
    return tracks


def play(clip: str, tracks: dict[str, list]) -> None:
    """Solos the clip's tracks."""
    wanted = {id(t) for t in tracks.get(clip, [])}
    for clip_tracks_ in tracks.values():
        for track in clip_tracks_:
            track.mute = id(track) not in wanted


def frame_times(clip: dict, fps: int) -> list[float]:
    count = max(1, round(float(clip["duration"]) * fps))
    return [i / fps for i in range(count)]


def go_to(scene, seconds: float) -> None:
    frame = seconds * scene.render.fps / scene.render.fps_base
    whole = math.floor(frame)
    scene.frame_set(whole, subframe=frame - whole)


# ---- camera -----------------------------------------------------------------


def camera_rotation(yaw: float, tilt: float) -> Euler:
    return Euler((math.radians(90 - tilt), 0.0, math.radians(-yaw)), "XYZ")


def camera_axes(yaw: float, tilt: float) -> tuple[Vector, Vector, Vector]:
    """(right, up, back) of a camera ``yaw`` degrees around the character and ``tilt`` degrees up.

    yaw 0 is in front of the character (it faces -Y), 90 at its right side (-X),
    so the character faces screen right."""
    matrix = camera_rotation(yaw, tilt).to_matrix()
    return matrix.col[0].copy(), matrix.col[1].copy(), matrix.col[2].copy()


def world_corners(meshes, depsgraph) -> list[Vector]:
    points = []
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        matrix = evaluated.matrix_world
        points.extend(matrix @ Vector(corner) for corner in evaluated.bound_box)
    return points


def framing(scene, meshes, clips, tracks, fps, directions, tilt) -> tuple[float, float]:
    """(ortho scale, height of the frame's centre above the feet, along the camera's up)."""
    axes = [camera_axes(d["yaw"], tilt)[:2] for d in directions]
    half_width, low, high = 0.0, 0.0, 0.0
    for clip in clips:
        play(clip["name"], tracks)
        for seconds in frame_times(clip, fps):
            go_to(scene, seconds)
            for point in world_corners(meshes, bpy.context.evaluated_depsgraph_get()):
                for right, up in axes:
                    half_width = max(half_width, abs(point.dot(right)))
                    v = point.dot(up)
                    low, high = min(low, v), max(high, v)
    span = max(2 * half_width, high - low, 1e-3) * MARGIN
    return span, (low + high) / 2


def place_camera(camera, yaw: float, tilt: float, centre: float, distance: float) -> None:
    _, up, back = camera_axes(yaw, tilt)
    camera.rotation_euler = camera_rotation(yaw, tilt)
    camera.location = up * centre + back * distance


# ---- look -------------------------------------------------------------------


def base_color_socket(material):
    """The output that gives the material's colour: the Principled BSDF's base colour input link or value."""
    nodes = material.node_tree.nodes if material.use_nodes and material.node_tree else []
    for node in nodes:
        if node.type == "BSDF_PRINCIPLED":
            socket = node.inputs["Base Color"]
            return (socket.links[0].from_socket if socket.is_linked else None), tuple(socket.default_value)
    return None, tuple(material.diffuse_color)


def toon_material(material) -> None:
    """Flat light bands (Shader to RGB through a constant colour ramp) times the base colour, emitted."""
    link_from, color = base_color_socket(material)
    material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        if link_from is None or not _feeds(node, link_from.node):
            tree.nodes.remove(node)
    nodes, links = tree.nodes, tree.links
    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.inputs["Color"].default_value = (1, 1, 1, 1)
    to_rgb = nodes.new("ShaderNodeShaderToRGB")
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    elements = ramp.color_ramp.elements
    while len(elements) < len(TOON_STEPS):
        elements.new(0.5)
    for element, (position, value) in zip(elements, TOON_STEPS):
        element.position, element.color = position, (value, value, value, 1.0)
    multiply = nodes.new("ShaderNodeMix")
    multiply.data_type, multiply.blend_type = "RGBA", "MULTIPLY"
    factor, mix_a, mix_b = (socket(multiply.inputs, name) for name in ("Factor_Float", "A_Color", "B_Color"))
    mix_out = socket(multiply.outputs, "Result_Color")
    factor.default_value = 1.0
    emission = nodes.new("ShaderNodeEmission")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(diffuse.outputs["BSDF"], to_rgb.inputs["Shader"])
    links.new(to_rgb.outputs["Color"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], mix_a)
    if link_from is not None:
        links.new(link_from, mix_b)
    else:
        mix_b.default_value = color
    links.new(mix_out, emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    material.use_backface_culling = False


def socket(sockets, identifier: str):
    """The Mix node has a float, vector and colour socket of each name; pick by identifier."""
    return next(s for s in sockets if s.identifier == identifier)


def _feeds(node, target) -> bool:
    """True when ``node`` is ``target`` or one of its inputs (so the texture chain is kept)."""
    seen, stack = set(), [target]
    while stack:
        current = stack.pop()
        if current == node:
            return True
        if current.name in seen:
            continue
        seen.add(current.name)
        stack.extend(link.from_node for socket in current.inputs for link in socket.links)
    return False


def outline_material():
    material = bpy.data.materials.new("Outline")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = OUTLINE_COLOR
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    material.use_backface_culling = True
    return material


def add_outline(meshes, thickness: float) -> None:
    """Inverted hull: a solidify shell with flipped normals and a back-face-culled dark material."""
    outline = outline_material()
    for obj in meshes:
        obj.data.materials.append(outline)
        modifier = obj.modifiers.new("Outline", "SOLIDIFY")
        modifier.thickness = -thickness
        modifier.offset = 1.0
        modifier.use_flip_normals = True
        modifier.use_rim = False
        modifier.material_offset = len(obj.data.materials) - 1
        modifier.material_offset_rim = len(obj.data.materials) - 1


def holdout_material():
    material = bpy.data.materials.new("Holdout")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(nodes.new("ShaderNodeHoldout").outputs[0], output.inputs["Surface"])
    return material


def set_up_render(scene, size: int) -> None:
    scene.render.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in _engines() else "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    eevee = scene.eevee
    if hasattr(eevee, "taa_render_samples"):
        eevee.taa_render_samples = 16
    world = bpy.data.worlds.new("Sprites") if scene.world is None else scene.world
    scene.world = world
    world.use_nodes = True
    background = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if background is not None:
        background.inputs["Color"].default_value = (1, 1, 1, 1)
        background.inputs["Strength"].default_value = 0.15


def _engines() -> list[str]:
    return [item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]


def add_light(camera) -> None:
    """A sun from the camera's upper left, parented to it so every direction is lit alike."""
    data = bpy.data.lights.new("Key", "SUN")
    data.energy = 3.0
    data.use_shadow = False  # the outline shell around the body would shade all of it
    sun = bpy.data.objects.new("Key", data)
    bpy.context.scene.collection.objects.link(sun)
    sun.parent = camera
    sun.rotation_euler = Euler((math.radians(-35), math.radians(-40), 0.0), "XYZ")


# ---- main -------------------------------------------------------------------


def main(args: dict) -> dict:
    progress(0.0, "Importing the animated mesh")
    empty_scene()
    scene = bpy.context.scene
    objects = import_gltf(args["animated_glb"])
    meshes = [obj for obj in objects if obj.type == "MESH"]
    if not meshes:
        raise ValueError("the animated GLB has no mesh")
    tracks = clip_tracks(objects)
    clips, directions = args["clips"], args["directions"]
    missing = [clip["name"] for clip in clips if clip["name"] not in tracks]
    if missing and tracks:
        raise ValueError(f"the animated GLB has no clip named {', '.join(missing)}")
    fps, size, tilt = int(args["fps"]), int(args["size"]), float(args["tilt"])

    progress(0.03, "Framing the animation")
    span, centre = framing(scene, meshes, clips, tracks, fps, directions, tilt)

    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = span
    distance = span * 4 + 10
    camera.data.clip_start, camera.data.clip_end = 0.01, distance * 2

    progress(0.05, "Setting up the toon look")
    # The mannequin wearing gear or holding a weapon is not drawn, but still hides what is behind
    # it, so the item's layer composites over the character's own body layer.
    hidden = [obj for obj in meshes if obj.name.lower().startswith("mannequin")]
    shown = [obj for obj in meshes if obj not in hidden]
    for obj in hidden:
        obj.data.materials.clear()
        obj.data.materials.append(holdout_material())
    for material in {slot.material for obj in shown for slot in obj.material_slots if slot.material}:
        toon_material(material)
    for obj in shown:
        if not obj.data.materials:
            obj.data.materials.append(bpy.data.materials.new("Plain"))
            toon_material(obj.data.materials[0])
    outline_px = float(args.get("outline", 1.0))
    if outline_px > 0:
        add_outline(shown, span / int(args["frame_size"]) * outline_px)
    set_up_render(scene, size)
    add_light(camera)

    out_dir = Path(args["out_dir"])
    frames: dict[str, dict[str, list[str]]] = {}
    total = sum(len(frame_times(clip, fps)) for clip in clips) * len(directions)
    done = 0
    for direction in directions:
        place_camera(camera, float(direction["yaw"]), tilt, centre, distance)
        frames[direction["name"]] = {}
        for clip in clips:
            progress(0.05 + 0.95 * done / max(1, total), f"Rendering {clip['name']} ({direction['name']})")
            play(clip["name"], tracks)
            paths = []
            for index, seconds in enumerate(frame_times(clip, fps)):
                go_to(scene, seconds)
                path = out_dir / direction["name"] / clip["name"] / f"{index:03d}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
                paths.append(str(path))
                done += 1
            frames[direction["name"]][clip["name"]] = paths
    progress(1.0, "Frames rendered")
    return {"frames": frames, "ortho_scale": span}


if __name__ == "__main__":
    run(main)
