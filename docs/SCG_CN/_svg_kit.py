#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SVG primitives + layout constants for thesis figures."""

from __future__ import annotations

import html
import math
from pathlib import Path

# Canvas & margins (16:9)
W, H = 1920, 1080
MX = 64          # left/right margin
MY = 56          # top/bottom margin
TITLE_Y = MY + 8
SUB_Y = TITLE_Y + 34
FOOTER1_Y = H - MY - 8
FOOTER2_Y = H - MY - 36
CONTENT_TOP = SUB_Y + 28
CONTENT_BOT = FOOTER2_Y - 28
CX = W / 2


def esc(s: str) -> str:
    return html.escape(s, quote=True)


class Svg:
    def __init__(
        self,
        title: str = "",
        *,
        family: str = "Segoe UI, Helvetica Neue, Arial, sans-serif",
    ):
        self.w = W
        self.h = H
        self.title = title
        self.family = family
        self.parts: list[str] = []

    def bg(self, color: str = "#ffffff") -> None:
        self.parts.append(f'<rect width="{self.w}" height="{self.h}" fill="{color}"/>')

    def heading(self, main: str, sub: str = "") -> None:
        self.text(CX, TITLE_Y, main, size=30, weight="700", anchor="middle")
        if sub:
            self.text(CX, SUB_Y, sub, size=17, fill="#64748b", anchor="middle")

    def footer(self, line1: str, line2: str = "") -> None:
        if line2:
            self.text(CX, FOOTER2_Y, line1, size=17, fill="#475569", anchor="middle")
            self.text(CX, FOOTER1_Y, line2, size=16, fill="#64748b", anchor="middle")
        else:
            self.text(CX, FOOTER1_Y, line1, size=17, fill="#475569", anchor="middle")

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str = "#ffffff",
        stroke: str = "#334155",
        sw: float = 1.8,
        rx: float = 10,
    ) -> None:
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{rx}" ry="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )

    def text(
        self,
        x: float,
        y: float,
        s: str,
        *,
        size: float = 17,
        weight: str = "400",
        fill: str = "#0f172a",
        anchor: str = "start",
        family: str | None = None,
    ) -> None:
        fam = family or self.family
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" font-family="{fam}">{esc(s)}</text>'
        )

    def _arrowhead(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        fill: str = "#64748b",
        size: float = 11.0,
    ) -> None:
        """Draw a filled triangle at (x2,y2) pointing along the segment — no SVG markers."""
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        # base sits `size` back from tip; half-width ~0.55*size
        bx, by = x2 - ux * size, y2 - uy * size
        px, py = -uy * size * 0.55, ux * size * 0.55
        self.parts.append(
            f'<polygon points="{x2:.2f},{y2:.2f} {bx + px:.2f},{by + py:.2f} '
            f'{bx - px:.2f},{by - py:.2f}" fill="{fill}"/>'
        )

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke: str = "#64748b",
        sw: float = 2.0,
        dash: str | None = None,
        marker_end: str | None = "url(#arrow)",
        arrow_size: float = 11.0,
    ) -> None:
        """Draw a straight segment. Arrowheads are polygons (resvg-safe), not markers."""
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        draw_arrow = marker_end is not None and marker_end != ""
        ex, ey = x2, y2
        if draw_arrow:
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length > arrow_size + 1:
                # stop the shaft just before the tip so it meets the triangle cleanly
                ux, uy = dx / length, dy / length
                ex = x2 - ux * (arrow_size * 0.85)
                ey = y2 - uy * (arrow_size * 0.85)
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
            f'stroke="{stroke}" stroke-width="{sw}"{dash_attr}/>'
        )
        if draw_arrow:
            self._arrowhead(x1, y1, x2, y2, fill=stroke, size=arrow_size)

    def polyline(
        self,
        points: list[tuple[float, float]],
        *,
        stroke: str = "#64748b",
        sw: float = 2.0,
        fill: str = "none",
        marker_end: str | None = None,
        arrow_size: float = 11.0,
    ) -> None:
        if len(points) < 2:
            return
        draw_arrow = marker_end is not None and marker_end != ""
        pts_draw = list(points)
        if draw_arrow:
            (x1, y1), (x2, y2) = points[-2], points[-1]
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length > arrow_size + 1:
                ux, uy = dx / length, dy / length
                pts_draw[-1] = (x2 - ux * (arrow_size * 0.85), y2 - uy * (arrow_size * 0.85))
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts_draw)
        self.parts.append(
            f'<polyline points="{pts}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}"/>'
        )
        if draw_arrow:
            self._arrowhead(*points[-2], *points[-1], fill=stroke, size=arrow_size)

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        *,
        fill: str = "#2563eb",
        stroke: str = "#1e40af",
        sw: float = 1.5,
    ) -> None:
        self.parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
        )

    def box(
        self,
        cx: float,
        cy: float,
        w: float,
        h: float,
        lines: list[str],
        *,
        fill: str = "#f8fafc",
        stroke: str = "#475569",
        title_size: float = 18,
        body_size: float = 15,
        accent: bool = False,
    ) -> dict[str, float]:
        """Draw a centered box; return edge anchors for arrows."""
        if accent:
            fill, stroke = "#eff6ff", "#2563eb"
        x, y = cx - w / 2, cy - h / 2
        self.rect(x, y, w, h, fill=fill, stroke=stroke, sw=2.2 if accent else 1.8)
        if lines:
            n = len(lines)
            gap = max(title_size, body_size) + 6
            mid_i = (n - 1) / 2
            for i, line in enumerate(lines):
                sz = title_size if i == 0 else body_size
                # Baseline sits ~0.35em below glyph visual center
                y_line = cy + (i - mid_i) * gap + sz * 0.35
                self.text(
                    cx,
                    y_line,
                    line,
                    size=sz,
                    weight="700" if i == 0 else "400",
                    fill="#0f172a" if i == 0 else "#334155",
                    anchor="middle",
                )
        return {
            "cx": cx,
            "cy": cy,
            "w": w,
            "h": h,
            "l": x,
            "r": x + w,
            "t": y,
            "b": y + h,
        }

    def arrow_h(self, a: dict, b: dict, *, y: float | None = None, label: str = "", pad: float = 6) -> None:
        yy = a["cy"] if y is None else y
        if a["cx"] <= b["cx"]:
            self.line(a["r"] + pad, yy, b["l"] - pad, yy)
        else:
            # Right-to-left: leave from left edge of a into right edge of b
            self.line(a["l"] - pad, yy, b["r"] + pad, yy)
        if label:
            self.text((a["cx"] + b["cx"]) / 2, yy - 14, label, size=15, fill="#64748b", anchor="middle")

    def arrow_v(self, a: dict, b: dict, *, x: float | None = None, label: str = "", pad: float = 6) -> None:
        xx = a["cx"] if x is None else x
        if a["cy"] <= b["cy"]:
            self.line(xx, a["b"] + pad, xx, b["t"] - pad)
        else:
            self.line(xx, a["t"] - pad, xx, b["b"] + pad)
        if label:
            self.text(xx + 12, (a["cy"] + b["cy"]) / 2 + 5, label, size=15, fill="#64748b", anchor="start")

    def elbow_down(
        self,
        a: dict,
        b: dict,
        *,
        mid_y: float | None = None,
        pad: float = 6,
        stroke: str = "#64748b",
        sw: float = 2.0,
    ) -> None:
        """Orthogonal: down from a, across, down into b (marker only on last segment)."""
        my = mid_y if mid_y is not None else (a["b"] + b["t"]) / 2
        self.line(a["cx"], a["b"] + pad, a["cx"], my, marker_end=None, stroke=stroke, sw=sw)
        self.line(a["cx"], my, b["cx"], my, marker_end=None, stroke=stroke, sw=sw)
        self.line(b["cx"], my, b["cx"], b["t"] - pad, stroke=stroke, sw=sw)

    def elbow_across(
        self,
        a: dict,
        b: dict,
        *,
        mid_x: float | None = None,
        pad: float = 6,
        stroke: str = "#64748b",
        sw: float = 2.0,
    ) -> None:
        """Orthogonal: across from a, then vertical into b."""
        mx = mid_x if mid_x is not None else (a["r"] + b["l"]) / 2 if a["r"] < b["l"] else (b["r"] + a["l"]) / 2
        # leave a on the side facing b
        if a["cx"] <= b["cx"]:
            self.line(a["r"] + pad, a["cy"], mx, a["cy"], marker_end=None, stroke=stroke, sw=sw)
        else:
            self.line(a["l"] - pad, a["cy"], mx, a["cy"], marker_end=None, stroke=stroke, sw=sw)
        self.line(mx, a["cy"], mx, b["cy"], marker_end=None, stroke=stroke, sw=sw)
        if a["cx"] <= b["cx"]:
            self.line(mx, b["cy"], b["l"] - pad, b["cy"], stroke=stroke, sw=sw)
        else:
            self.line(mx, b["cy"], b["r"] + pad, b["cy"], stroke=stroke, sw=sw)

    def render(self) -> str:
        # No SVG markers — arrowheads are drawn as polygons in line()/polyline()
        # so vertical arrows stay straight under resvg PNG conversion.
        head = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{esc(self.title)}">',
        ]
        return "\n".join(head + self.parts + ["</svg>"])

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")


# Back-compat aliases used by older call sites
def box_centered(svg: Svg, *args, **kwargs):
    return svg.box(*args, **kwargs)
