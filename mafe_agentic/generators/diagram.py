"""Generador de diagramas de flujo."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import graphviz


SHAPE_MAP = {"box": "box", "ellipse": "ellipse", "diamond": "diamond"}


def build(spec: dict, output_path: str) -> str:
    title = spec.get("title", "")
    nodes = spec.get("nodes", [])
    edges = spec.get("edges", [])

    dot = graphviz.Digraph(
        format="png",
        graph_attr={
            "rankdir": "TB", "bgcolor": "white", "pad": "0.6",
            "nodesep": "0.5", "ranksep": "0.6",
            "label": title, "labelloc": "t",
            "fontname": "Helvetica", "fontsize": "16", "fontcolor": "#1F3A68",
        },
        node_attr={
            "fontname": "Helvetica", "fontsize": "12",
            "style": "filled,rounded", "fillcolor": "#F0EEE7",
            "color": "#1F3A68", "fontcolor": "#2A2A2A", "penwidth": "1.6",
        },
        edge_attr={
            "fontname": "Helvetica", "fontsize": "10",
            "color": "#1F3A68", "arrowsize": "0.8",
        },
    )

    for n in nodes:
        shape = SHAPE_MAP.get(n.get("shape", "box"), "box")
        attrs = {"shape": shape}
        if shape == "diamond":
            attrs["fillcolor"] = "#FCE7D8"
        elif shape == "ellipse":
            attrs["fillcolor"] = "#E5EDF5"
        dot.node(str(n["id"]), n["label"], **attrs)

    for e in edges:
        dot.edge(str(e["from"]), str(e["to"]), label=e.get("label", ""))

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        rendered = dot.render(filename="diagram", directory=tmp, cleanup=False)
        shutil.copyfile(rendered, out_path)
    return str(out_path)
