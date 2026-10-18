# -*- coding: utf-8 -*-
"""manga_grid.py - page grid templates for B Manga / Fill Manga.

The image model (Krea 2) draws a page with the right number of panels only when the page is a GRID described with left/right as well as top/bottom and "separated by thick black borders
into exactly N separate panels"; full-width bands stacked on a landscape canvas collapse to about two panels (tests in audit_reports/panel_render). The local LLM cannot be talked into writing
a grid itself, so for 2..5 panels the geometry comes from this table: B Manga writes these regions (structure only, panel content untouched) and Fill Manga is handed the matching layout
sentence to use as the body of PAGE LAYOUT. Anything outside 2..5 panels keeps the geometry the model wrote and Fill describes it as before.
"""
from __future__ import annotations

_BORDER = "The page is divided by thick black borders into exactly {n} separate panels. "

# panel count -> (regions as x,y,width,height in page percent, layout sentence)
GRID_TEMPLATES: dict[int, tuple[list[list[float]], str]] = {
    2: ([[0, 0, 100, 50], [0, 50, 100, 50]],
        _BORDER.format(n="two") + "Panel 1 fills the top half of the page across the full width. Panel 2 fills the bottom half of the page across the full width."),
    3: ([[0, 0, 50, 55], [50, 0, 50, 55], [0, 55, 100, 45]],
        _BORDER.format(n="three") + "Panel 1 is at the top left, Panel 2 is at the top right, and Panel 3 is a wide panel along the whole bottom of the page."),
    4: ([[0, 0, 50, 50], [50, 0, 50, 50], [0, 50, 50, 50], [50, 50, 50, 50]],
        _BORDER.format(n="four") + "The page is a two-by-two grid: Panel 1 top-left, Panel 2 top-right, Panel 3 bottom-left, Panel 4 bottom-right."),
    5: ([[0, 0, 50, 50], [50, 0, 50, 50], [0, 50, 33, 50], [33, 50, 34, 50], [67, 50, 33, 50]],
        _BORDER.format(n="five") + "The top row has two equal panels, Panel 1 on the left and Panel 2 on the right. The bottom row has three equal panels, Panel 3 on the left, Panel 4 in the middle and Panel 5 on the right."),
}


def template_regions(panel_count: int) -> list[list[float]] | None:
    t = GRID_TEMPLATES.get(panel_count)
    return [list(r) for r in t[0]] if t else None


def layout_text_for(regions: list) -> str:
    """The layout sentence when `regions` are exactly a template's regions for that panel count; '' otherwise (older projects or pages outside 2..5 panels)."""
    try:
        got = [[float(v) for v in r] for r in regions]
    except (TypeError, ValueError):
        return ""
    t = GRID_TEMPLATES.get(len(got))
    return t[1] if t and got == [[float(v) for v in r] for r in t[0]] else ""
