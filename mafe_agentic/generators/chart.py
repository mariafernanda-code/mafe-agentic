"""Generador de graficos PNG."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PALETTE = ["#1F3A68", "#E06C3D", "#5A8AB0", "#D4A24C", "#7CA982",
           "#9D5B7B", "#4F6D7A", "#C18F4A"]


def build(spec: dict, output_path: str) -> str:
    ctype = spec.get("type", "bar")
    labels = spec.get("labels", [])
    values = spec.get("values", [])
    title = spec.get("title", "")
    x_label = spec.get("x_label", "")
    y_label = spec.get("y_label", "")

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=180)

    if ctype == "bar":
        ax.bar(labels, values, color=PALETTE[0])
        for i, v in enumerate(values):
            ax.text(i, v, f"{v:,.0f}" if isinstance(v, (int, float)) else str(v),
                    ha="center", va="bottom", fontsize=9, color="#2A2A2A")
    elif ctype == "line":
        ax.plot(labels, values, marker="o", linewidth=2.4,
                color=PALETTE[1], markerfacecolor=PALETTE[0])
        ax.grid(True, axis="y", linestyle="--", alpha=0.4)
    elif ctype == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%",
               colors=PALETTE, startangle=90,
               wedgeprops={"edgecolor": "white", "linewidth": 2})
    else:
        ax.bar(labels, values, color=PALETTE[0])

    if title:
        ax.set_title(title, fontsize=14, color="#2A2A2A", pad=14)
    if x_label and ctype != "pie":
        ax.set_xlabel(x_label, color="#555")
    if y_label and ctype != "pie":
        ax.set_ylabel(y_label, color="#555")

    if ctype != "pie":
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(colors="#555")

    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return output_path
