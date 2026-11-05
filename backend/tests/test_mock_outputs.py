"""The mock placeholders are valid files."""

from __future__ import annotations

import struct

from assetgen.adapters.mock.glb import GlbBuilder, Part, build_mesh, read_glb_json
from assetgen.exporters import safe_name


def test_glb_structure(tmp_path):
    builder = GlbBuilder(extras={"x": 1})
    material = builder.add_material("m", (1, 0, 0))
    mesh = build_mesh([Part((0, 1, 0), (0.2, 0.5, 0.2), 1)], 2000)
    node = builder.add_node("root")
    builder.add_node("body", builder.add_mesh("body", mesh, material), node)
    path = builder.write(tmp_path / "a.glb")
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data)
    assert magic == b"glTF" and version == 2 and length == len(data)
    gltf = read_glb_json(path)
    assert gltf["extras"] == {"x": 1} and gltf["nodes"][0]["children"] == [1]
    assert 1500 <= mesh.triangles <= 2000
    assert max(mesh.indices) < len(mesh.positions) // 3


def test_safe_name_is_windows_safe():
    assert safe_name("Ember Knight!") == "Ember_Knight"
    assert safe_name("con") == "con_"
    assert safe_name("  ../..  ") == "asset"
