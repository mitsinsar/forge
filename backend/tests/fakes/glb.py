"""A small glTF 2.0 binary (GLB) writer and reader for the test doubles.

Builds ellipsoid meshes whose triangle count matches a budget, plus node
animations (translation, rotation, scale), so viewers such as three.js and
model-viewer can show and play the placeholders.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

FLOAT, USHORT, UINT = 5126, 5123, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


@dataclass
class Part:
    """An ellipsoid: centre and radii in metres (y up, facing +z)."""

    center: tuple[float, float, float]
    radii: tuple[float, float, float]
    weight: float = 1.0  # share of the triangle budget


@dataclass
class MeshData:
    positions: list[float] = field(default_factory=list)
    normals: list[float] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)

    @property
    def triangles(self) -> int:
        return len(self.indices) // 3

    def extend(self, other: MeshData) -> None:
        base = len(self.positions) // 3
        self.positions += other.positions
        self.normals += other.normals
        self.indices += [i + base for i in other.indices]


def ellipsoid(part: Part, triangles: int) -> MeshData:
    """UV ellipsoid with about ``triangles`` triangles (at least 24)."""
    span = max(2, round(math.sqrt(max(triangles, 24) / 4)))
    rings, segments = span + 1, span * 2
    cx, cy, cz = part.center
    rx, ry, rz = part.radii
    mesh = MeshData()
    for r in range(rings):
        phi = math.pi * r / (rings - 1)
        for s in range(segments + 1):
            theta = 2 * math.pi * s / segments
            nx = math.sin(phi) * math.cos(theta)
            ny = math.cos(phi)
            nz = math.sin(phi) * math.sin(theta)
            mesh.positions += [cx + rx * nx, cy + ry * ny, cz + rz * nz]
            length = math.sqrt((nx / rx) ** 2 + (ny / ry) ** 2 + (nz / rz) ** 2) or 1.0
            mesh.normals += [nx / rx / length, ny / ry / length, nz / rz / length]
    row = segments + 1
    for r in range(rings - 1):
        for s in range(segments):
            a, b = r * row + s, (r + 1) * row + s
            if r != 0:
                mesh.indices += [a, a + 1, b]
            if r != rings - 2:
                mesh.indices += [a + 1, b + 1, b]
    return mesh


def build_mesh(parts: list[Part], triangle_budget: int) -> MeshData:
    total_weight = sum(p.weight for p in parts) or 1.0
    mesh = MeshData()
    for part in parts:
        mesh.extend(ellipsoid(part, int(triangle_budget * part.weight / total_weight)))
    return mesh


@dataclass
class Animation:
    name: str
    times: list[float]
    translations: list[tuple[float, float, float]]
    rotations: list[tuple[float, float, float, float]]  # quaternion x, y, z, w
    scales: list[tuple[float, float, float]]


class GlbBuilder:
    def __init__(self, extras: dict | None = None):
        self.gltf: dict = {
            "asset": {"version": "2.0", "generator": "assetgen test double"},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "materials": [],
            "accessors": [],
            "bufferViews": [],
            "buffers": [],
        }
        if extras:
            self.gltf["extras"] = extras
        self.blob = bytearray()

    def _view(self, data: bytes, target: int | None = None) -> int:
        while len(self.blob) % 4:
            self.blob.append(0)
        view = {"buffer": 0, "byteOffset": len(self.blob), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.blob += data
        self.gltf["bufferViews"].append(view)
        return len(self.gltf["bufferViews"]) - 1

    def _accessor(self, values: list, component: int, kind: str, count: int,
                  target: int | None = None, bounds: bool = False) -> int:
        fmt = {FLOAT: "f", USHORT: "H", UINT: "I"}[component]
        view = self._view(struct.pack(f"<{len(values)}{fmt}", *values), target)
        accessor = {"bufferView": view, "componentType": component, "count": count, "type": kind}
        if bounds:
            width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[kind]
            columns = [values[i::width] for i in range(width)]
            accessor["min"] = [min(c) for c in columns]
            accessor["max"] = [max(c) for c in columns]
        self.gltf["accessors"].append(accessor)
        return len(self.gltf["accessors"]) - 1

    def add_material(self, name: str, color: tuple[float, float, float]) -> int:
        self.gltf["materials"].append({
            "name": name,
            "pbrMetallicRoughness": {"baseColorFactor": [*color, 1.0], "metallicFactor": 0.0,
                                     "roughnessFactor": 0.8},
        })
        return len(self.gltf["materials"]) - 1

    def add_mesh(self, name: str, mesh: MeshData, material: int, extras: dict | None = None) -> int:
        vertex_count = len(mesh.positions) // 3
        component = UINT if vertex_count > 65535 else USHORT
        primitive = {
            "attributes": {
                "POSITION": self._accessor(mesh.positions, FLOAT, "VEC3", vertex_count, ARRAY_BUFFER, True),
                "NORMAL": self._accessor(mesh.normals, FLOAT, "VEC3", vertex_count, ARRAY_BUFFER),
            },
            "indices": self._accessor(mesh.indices, component, "SCALAR", len(mesh.indices),
                                      ELEMENT_ARRAY_BUFFER),
            "material": material,
        }
        entry = {"name": name, "primitives": [primitive]}
        if extras:
            entry["extras"] = extras
        self.gltf["meshes"].append(entry)
        return len(self.gltf["meshes"]) - 1

    def add_node(self, name: str, mesh: int | None = None, parent: int | None = None,
                 translation: tuple[float, float, float] | None = None, extras: dict | None = None) -> int:
        node: dict = {"name": name}
        if mesh is not None:
            node["mesh"] = mesh
        if translation:
            node["translation"] = list(translation)
        if extras:
            node["extras"] = extras
        self.gltf["nodes"].append(node)
        index = len(self.gltf["nodes"]) - 1
        if parent is None:
            self.gltf["scenes"][0]["nodes"].append(index)
        else:
            self.gltf["nodes"][parent].setdefault("children", []).append(index)
        return index

    def add_animation(self, node: int, animation: Animation, extras: dict | None = None) -> None:
        times = self._accessor(animation.times, FLOAT, "SCALAR", len(animation.times), bounds=True)
        samplers, channels = [], []
        for path, values, kind in (("translation", animation.translations, "VEC3"),
                                   ("rotation", animation.rotations, "VEC4"),
                                   ("scale", animation.scales, "VEC3")):
            flat = [v for item in values for v in item]
            output = self._accessor(flat, FLOAT, kind, len(values))
            samplers.append({"input": times, "output": output, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
        entry = {"name": animation.name, "samplers": samplers, "channels": channels}
        if extras:
            entry["extras"] = extras
        self.gltf.setdefault("animations", []).append(entry)

    def to_bytes(self) -> bytes:
        while len(self.blob) % 4:
            self.blob.append(0)
        self.gltf["buffers"] = [{"byteLength": len(self.blob)}]
        for key in ("materials", "meshes", "accessors", "bufferViews"):
            if not self.gltf[key]:
                del self.gltf[key]
        payload = json.dumps(self.gltf, separators=(",", ":")).encode()
        payload += b" " * (-len(payload) % 4)
        total = 12 + 8 + len(payload) + 8 + len(self.blob)
        return (struct.pack("<4sII", b"glTF", 2, total)
                + struct.pack("<I4s", len(payload), b"JSON") + payload
                + struct.pack("<I4s", len(self.blob), b"BIN\x00") + bytes(self.blob))

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.to_bytes())
        return path


def read_glb_json(path: Path) -> dict:
    """Returns the JSON chunk of a GLB file."""
    data = path.read_bytes()
    magic, version, _ = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise ValueError(f"{path.name} is not a glTF 2.0 binary")
    length, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON":
        raise ValueError(f"{path.name} has no JSON chunk")
    return json.loads(data[20:20 + length])
