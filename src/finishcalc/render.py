"""Картинка плана: контуры помещений с номерами. Помещение без предупреждений —
зелёное, с любым предупреждением — оранжевое."""

from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from finishcalc.model import Issue, Room

COLOR_OK = "#8fd18f"
COLOR_WARN = "#f5a742"
_EDGE_COLOR = "#333333"
_LABEL_COLOR = "#111111"

_DPI = 150
_LONG_SIDE_IN = 14.0  # длинная сторона картинки
_MIN_SHORT_SIDE_IN = 3.0
_BLANK_SIZE_IN = (6.0, 4.0)
_PADDING_SHARE = 0.03  # поля вокруг плана, доля длинной стороны
_LABEL_FONT_PT = 7


def render_plan(rooms: list[Room], issues: list[Issue], out_png: Path) -> Path:
    """Рисует план в `out_png` (PNG, 150 dpi) и возвращает этот путь."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    flagged = {issue.room for issue in issues if issue.room is not None}
    drawable = [(room, parts) for room in rooms if (parts := _polygon_parts(room.polygon))]

    if drawable:
        min_x, min_y, max_x, max_y = _bounds([part for _, parts in drawable for part in parts])
        pad = _PADDING_SHARE * max(max_x - min_x, max_y - min_y, 1e-9)
        min_x, min_y, max_x, max_y = min_x - pad, min_y - pad, max_x + pad, max_y + pad
        figure = _figure_for_extent(max_x - min_x, max_y - min_y)
        axes = figure.add_axes((0, 0, 1, 1))
        axes.set_axis_off()
        axes.set_aspect("equal")
        axes.set_xlim(min_x, max_x)
        axes.set_ylim(min_y, max_y)
        for room, parts in drawable:
            color = COLOR_WARN if room.number in flagged else COLOR_OK
            for part in parts:
                axes.add_patch(PathPatch(_polygon_path(part), facecolor=color,
                                         edgecolor=_EDGE_COLOR, linewidth=0.6))
            label_at = _label_point(parts)
            axes.text(label_at.x, label_at.y, room.number, ha="center", va="center",
                      fontsize=_LABEL_FONT_PT, color=_LABEL_COLOR)
    else:
        figure = Figure(figsize=_BLANK_SIZE_IN)

    figure.set_facecolor("white")
    FigureCanvasAgg(figure)
    figure.savefig(out_png, dpi=_DPI, format="png")
    return out_png


def _polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    """Непустые полигоны геометрии: у MultiPolygon после ремонта — все части."""
    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]
    if isinstance(geometry, MultiPolygon):
        return [part for part in geometry.geoms if not part.is_empty]
    return []


def _bounds(polygons: list[Polygon]) -> tuple[float, float, float, float]:
    boxes = [polygon.bounds for polygon in polygons]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _figure_for_extent(width_m: float, height_m: float) -> Figure:
    """Фигура с теми же пропорциями, что у плана, с длинной стороной `_LONG_SIDE_IN`."""
    long_side = max(width_m, height_m, 1e-9)
    width_in = _LONG_SIDE_IN * width_m / long_side
    height_in = _LONG_SIDE_IN * height_m / long_side
    return Figure(figsize=(max(width_in, _MIN_SHORT_SIDE_IN), max(height_in, _MIN_SHORT_SIDE_IN)))


def _polygon_path(polygon: Polygon) -> MplPath:
    """Контур с дырками: внешнее кольцо против часовой стрелки, внутренние — по часовой."""
    polygon = orient(polygon)
    vertices: list[tuple[float, float]] = []
    codes: list[int] = []
    for ring in [polygon.exterior, *polygon.interiors]:
        coords = list(ring.coords)
        vertices.extend(coords)
        codes.extend([MplPath.MOVETO, *[MplPath.LINETO] * (len(coords) - 2), MplPath.CLOSEPOLY])
    return MplPath(vertices, codes)


def _label_point(parts: list[Polygon]) -> Point:
    """Точка для номера: в центре самой большой части; если центр вне фигуры
    (Г-образная комната, дуга), то ближайшая внутренняя точка."""
    biggest = max(parts, key=lambda part: part.area)
    centre = biggest.centroid
    return centre if biggest.contains(centre) else biggest.representative_point()
