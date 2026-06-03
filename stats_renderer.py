"""Рендер таблиц статистики кланов и игроков на фоновое изображение."""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Literal, Sequence

from PIL import Image, ImageDraw, ImageFont

from stats_store import RankingEntry


_REPO_ROOT = Path(__file__).resolve().parent
_BUNDLED_FONTS_DIR = _REPO_ROOT / "assets" / "fonts"
_DEFAULT_BACKGROUND = _REPO_ROOT / "images" / "statfon.jpg"
STAT_BACKGROUND_PATH = Path(os.getenv("STAT_BACKGROUND_PATH", str(_DEFAULT_BACKGROUND)))

TOP_CLAN_LIMIT = 7
TOP_PLAYER_LIMIT = 7

_METRIC_HEADERS = ("kills", "deaths", "kd", "maxdist")
# Первая колонка шире для длинных ников игроков
_COLUMN_FRACTIONS = (0.28, 0.14, 0.18, 0.11, 0.29)
_PLAYER_COLUMN_FRACTIONS = (0.34, 0.13, 0.17, 0.11, 0.25)
_COLUMN_ALIGNS: tuple[Literal["left", "right", "center"], ...] = (
    "center",
    "center",
    "center",
    "center",
    "center",
)

_TABLE_FRACTION = float(os.getenv("STAT_TABLE_FRACTION", "0.744"))

_COLOR_HEADER = (0, 0, 0, 255)
_COLOR_BODY = (0, 0, 0, 255)
_COLOR_MUTED = (40, 40, 40, 255)
_COLOR_GRID = (170, 170, 170, 160)


def _resolve_font_path(*, bold: bool = False) -> Path:
    env_path = os.getenv("STAT_FONT_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate

    bundled = (
        _BUNDLED_FONTS_DIR / ("OpenSans-Bold.ttf" if bold else "OpenSans-Regular.ttf"),
        _BUNDLED_FONTS_DIR / "OpenSans-VF.ttf",
        _REPO_ROOT.parent / "dsbot" / "assets" / "fonts" / "OpenSans-VF.ttf",
    )
    for candidate in bundled:
        if candidate.is_file():
            return candidate

    linux = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if bold
        else Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")
        if bold
        else Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    )
    for candidate in linux:
        if candidate.is_file():
            return candidate

    windir = os.environ.get("WINDIR", r"C:\Windows")
    segoe = Path(windir) / "Fonts" / ("segoeuib.ttf" if bold else "segoeui.ttf")
    if segoe.is_file():
        return segoe

    raise FileNotFoundError(
        "Font not found. Set STAT_FONT_PATH or add OpenSans-Regular.ttf "
        "and OpenSans-Bold.ttf to assets/fonts."
    )


def _load_font(size: int, *, weight: int = 400) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    bold = weight >= 600
    path = _resolve_font_path(bold=bold)
    font = ImageFont.truetype(str(path), size=size)
    if path.name == "OpenSans-VF.ttf":
        setter = getattr(font, "set_variation_by_axes", None)
        if callable(setter):
            try:
                setter([float(weight)])
            except (OSError, TypeError, ValueError):
                pass
    return font


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _truncate_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    if _text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed and _text_width(draw, trimmed + ellipsis, font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ellipsis) if trimmed else ellipsis


def _layout_table(width: int, height: int, row_limit: int) -> tuple[int, int, int, int, int, int]:
    fraction = max(0.5, min(1.0, _TABLE_FRACTION))
    table_width = int(width * fraction)
    table_height = int(height * fraction)
    left = (width - table_width) // 2
    top = (height - table_height) // 2
    right = left + table_width
    row_slots = 1 + row_limit
    unit = table_height // row_slots
    header_height = unit
    row_height = unit
    bottom = top + unit * row_slots
    return left, top, right, bottom, header_height, row_height


def _layout_columns(
    table_left: int,
    table_width: int,
    fractions: tuple[float, ...],
) -> list[tuple[int, int, Literal["left", "right", "center"]]]:
    columns: list[tuple[int, int, Literal["left", "right", "center"]]] = []
    x = table_left
    for fraction, align in zip(fractions, _COLUMN_ALIGNS):
        col_w = int(table_width * fraction)
        columns.append((x, col_w, align))
        x += col_w
    last_x, _, last_align = columns[-1]
    columns[-1] = (last_x, table_left + table_width - last_x, last_align)
    return columns


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    *,
    anchor: str = "lt",
) -> None:
    x, y = xy
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


def _fit_font_to_columns(
    draw: ImageDraw.ImageDraw,
    labels: tuple[str, ...],
    columns: list[tuple[int, int, Literal["left", "right", "center"]]],
    max_size: int,
    min_size: int,
    *,
    weight: int,
) -> ImageFont.ImageFont:
    for size in range(max_size, min_size - 1, -1):
        font = _load_font(size, weight=weight)
        if all(
            _text_width(draw, label, font) <= col_w - 14
            for label, (_, col_w, _) in zip(labels, columns)
        ):
            return font
    return _load_font(min_size, weight=weight)


def _draw_cell(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    width: int,
    cell_top: int,
    cell_height: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    align: Literal["left", "right", "center"],
) -> None:
    display = _truncate_to_width(draw, text, font, width - 14)
    center_y = cell_top + cell_height // 2
    pad = 10
    if align == "left":
        tx = x + pad
        anchor = "lm"
    elif align == "right":
        tx = x + width - pad
        anchor = "rm"
    else:
        tx = x + width // 2
        anchor = "mm"
    _draw_text(draw, (tx, center_y), display, font, fill, anchor=anchor)


def _draw_table_grid(
    draw: ImageDraw.ImageDraw,
    table_left: int,
    table_top: int,
    table_right: int,
    header_height: int,
    row_height: int,
    row_limit: int,
    columns: list[tuple[int, int, Literal["left", "right", "center"]]],
) -> None:
    line_w = 1
    header_sep_y = table_top + header_height
    draw.line(
        (table_left, header_sep_y, table_right, header_sep_y),
        fill=_COLOR_GRID,
        width=line_w,
    )

    for row_index in range(1, row_limit):
        y = header_sep_y + row_index * row_height
        draw.line((table_left, y, table_right, y), fill=_COLOR_GRID, width=line_w)

    content_bottom = table_top + header_height + row_limit * row_height
    for col_x, _, _ in columns[1:]:
        draw.line(
            (col_x, table_top, col_x, content_bottom),
            fill=_COLOR_GRID,
            width=line_w,
        )


def _row_values(entry: RankingEntry) -> tuple[str, str, str, str, str]:
    kd_text = f"{entry.kd:.2f}"
    dist_text = f"{entry.max_distance_m:.0f}" if entry.max_distance_m else "—"
    return (
        entry.display_name,
        str(entry.kills),
        str(entry.deaths),
        kd_text,
        dist_text,
    )


def stats_image_caption(*, period_note: str | None = None) -> str | None:
    return None


def render_ranking_table_image(
    rows: Sequence[RankingEntry],
    *,
    name_header: str,
    row_limit: int,
    column_fractions: tuple[float, ...] = _COLUMN_FRACTIONS,
    background_path: Path | None = None,
) -> io.BytesIO:
    bg_path = background_path or STAT_BACKGROUND_PATH
    if not bg_path.is_file():
        raise FileNotFoundError(f"Background image not found: {bg_path}")

    base = Image.open(bg_path).convert("RGBA")
    canvas = base.copy()
    draw = ImageDraw.Draw(canvas)

    width, height = canvas.size
    table_left, table_top, table_right, table_bottom, header_height, row_height = _layout_table(
        width, height, row_limit
    )
    table_width = table_right - table_left
    columns = _layout_columns(table_left, table_width, column_fractions)
    header_labels = (name_header, *_METRIC_HEADERS)

    font_header = _fit_font_to_columns(
        draw,
        header_labels,
        columns,
        max(26, int(header_height * 0.36)),
        max(18, int(header_height * 0.22)),
        weight=700,
    )
    font_body = _load_font(max(20, int(row_height * 0.34)), weight=500)

    header_top = table_top
    body_top = header_top + header_height

    _draw_table_grid(
        draw,
        table_left,
        table_top,
        table_right,
        header_height,
        row_height,
        row_limit,
        columns,
    )

    for label, (col_x, col_w, align) in zip(header_labels, columns):
        _draw_cell(
            draw,
            label,
            col_x,
            col_w,
            header_top,
            header_height,
            font_header,
            _COLOR_HEADER,
            align,
        )

    row_top = body_top
    displayed = list(rows[:row_limit])

    if not displayed:
        _draw_text(
            draw,
            (width // 2, row_top + row_height // 2),
            "Нет данных",
            font_body,
            _COLOR_MUTED,
            anchor="mm",
        )
    else:
        for index in range(row_limit):
            cell_top = row_top + index * row_height
            if cell_top + row_height > table_bottom:
                break
            if index >= len(displayed):
                continue
            values = _row_values(displayed[index])
            for value, (col_x, col_w, align) in zip(values, columns):
                _draw_cell(
                    draw,
                    value,
                    col_x,
                    col_w,
                    cell_top,
                    row_height,
                    font_body,
                    _COLOR_BODY,
                    align,
                )

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=92)
    out.seek(0)
    return out


def render_stats_image(
    clans: Sequence[RankingEntry],
    *,
    background_path: Path | None = None,
) -> io.BytesIO:
    return render_ranking_table_image(
        clans,
        name_header="clan",
        row_limit=TOP_CLAN_LIMIT,
        column_fractions=_COLUMN_FRACTIONS,
        background_path=background_path,
    )


def render_player_stats_image(
    players: Sequence[RankingEntry],
    *,
    background_path: Path | None = None,
) -> io.BytesIO:
    return render_ranking_table_image(
        players,
        name_header="player",
        row_limit=TOP_PLAYER_LIMIT,
        column_fractions=_PLAYER_COLUMN_FRACTIONS,
        background_path=background_path,
    )
