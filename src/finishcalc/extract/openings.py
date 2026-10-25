"""Проёмы из чертежа: двери и окна, их марки, размеры и помещения."""

from typing import Literal, NamedTuple

from ezdxf import bbox
from ezdxf.document import Drawing
from shapely.geometry import Point, Polygon, box

from finishcalc.model import Issue, Opening, Profile, Room
from finishcalc.numbers import normalize_room_number

# Марка проёма ищется не дальше этого расстояния от центра габарита блока, м.
_MARK_SEARCH_RADIUS_M = 1.5
# Проём относится к помещению, если габарит блока не дальше этого от контура, м.
_ROOM_LINK_DISTANCE_M = 0.3
# Без марки блок с короткой стороной габарита больше этой — дверь, иначе окно, м.
_DOOR_MIN_SHORT_SIDE_M = 0.3
# Окончание марки левого варианта двери (в нормализованном виде — верхний регистр).
_LEFT_VARIANT_SUFFIX = "Л"
# Знаки варианта позиции в конце марки («3*», «5'», «3**») — размер тот же, что у позиции.
_VARIANT_MARKS = "*'’"
# Проём не ниже этой высоты начинается от пола (проём без полотна, витраж), м.
_FLOOR_LEVEL_HEIGHT_M = 2.0

Kind = Literal["door", "window"]
# Размеры по умолчанию: (ширина, высота, низ от пола), м.
_DEFAULT_SIZES: dict[Kind, tuple[float, float, float]] = {
    "door": (0.9, 2.1, 0.0),
    "window": (1.5, 1.5, 0.9),
}
_KIND_RU: dict[Kind, str] = {"door": "дверь", "window": "окно"}


class _Mark(NamedTuple):
    text: str
    kind: Kind
    point: Point


def extract_openings(
    doc: Drawing,
    profile: Profile,
    rooms: list[Room],
    spec: dict[str, tuple[float, float]],
) -> tuple[list[Opening], list[Issue]]:
    """Проёмы чертежа и предупреждения о проёмах без марки и без записи в спецификации.

    Проём — вставка блока в слое проёмов; вид и марку задаёт ближайший текст
    марки в радиусе 1.5 м, без марки вид определяется по габариту. Помещения
    проёма — те, чей контур не дальше 0.3 м от габарита блока. Вставка без
    геометрии (пустой блок) пропускается. Геометрия в метрах.
    """
    scale = profile.mm_per_unit / 1000
    marks = _read_marks(doc, profile, scale)
    openings: list[Opening] = []
    issues: list[Issue] = []

    for insert in doc.modelspace().query("INSERT"):
        if insert.dxf.layer != profile.openings_layer:
            continue
        extents = bbox.extents([insert], fast=True)
        if not extents.has_data:
            continue
        footprint = box(extents.extmin.x * scale, extents.extmin.y * scale,
                        extents.extmax.x * scale, extents.extmax.y * scale)
        center = footprint.centroid
        mark = _nearest_mark(marks, center)

        if mark is not None:
            kind, mark_text = mark.kind, mark.text
        else:
            kind, mark_text = _kind_by_footprint(footprint), None

        default_width, default_height, sill = _DEFAULT_SIZES[kind]
        width, height = default_width, default_height
        size_source: Literal["spec", "default"] = "default"
        where = f"{_KIND_RU[kind]} около ({center.x:.2f}; {center.y:.2f}) м"
        if mark_text is None:
            issues.append(Issue("opening_without_mark", None, f"Проём без марки: {where}"))
        else:
            size = lookup_size(mark_text, spec)
            if size is None:
                issues.append(Issue(
                    "opening_mark_not_in_spec", None,
                    f"Марки {mark_text} нет в спецификации проёмов ({where}); "
                    f"взяты размеры по умолчанию {width} × {height} м",
                ))
            else:
                width, height = size
                size_source = "spec"
                if height >= _FLOOR_LEVEL_HEIGHT_M:
                    sill = 0.0

        openings.append(Opening(
            kind=kind,
            mark=mark_text,
            width_m=width,
            height_m=height,
            sill_m=sill,
            rooms=[room.number for room in rooms
                   if room.polygon.distance(footprint) <= _ROOM_LINK_DISTANCE_M],
            size_source=size_source,
            center=(center.x, center.y),
        ))
    return openings, issues


def lookup_size(mark: str, spec: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    """Размер (ширина, высота) в метрах по марке из спецификации.

    Марки сравниваются без учёта регистра и латиницы-двойников. Сначала ищется
    точная марка, затем без конечных знаков варианта («3**» → «3»), затем без
    конечной «л» (левый вариант двери).
    """
    key = normalize_room_number(mark)
    sizes = {normalize_room_number(spec_mark): size for spec_mark, size in spec.items()}
    for candidate in dict.fromkeys([key, key.rstrip(_VARIANT_MARKS)]):
        if candidate in sizes:
            return sizes[candidate]
        if candidate.endswith(_LEFT_VARIANT_SUFFIX) and candidate[:-len(_LEFT_VARIANT_SUFFIX)] in sizes:
            return sizes[candidate[:-len(_LEFT_VARIANT_SUFFIX)]]
    return None


def _read_marks(doc: Drawing, profile: Profile, scale: float) -> list[_Mark]:
    """Тексты марок в слоях марок дверей и окон; вид марки задаёт её слой."""
    kind_by_layer: dict[str, Kind] = {}
    if profile.window_mark_layer:
        kind_by_layer[profile.window_mark_layer] = "window"
    if profile.door_mark_layer:
        kind_by_layer[profile.door_mark_layer] = "door"

    marks = []
    for mtext in doc.modelspace().query("MTEXT"):
        kind = kind_by_layer.get(mtext.dxf.layer)
        text = mtext.plain_text().strip()
        if kind is None or not text:
            continue
        at = mtext.dxf.insert
        marks.append(_Mark(text, kind, Point(at.x * scale, at.y * scale)))
    return marks


def _nearest_mark(marks: list[_Mark], center: Point) -> _Mark | None:
    """Ближайшая к центру проёма марка в радиусе поиска или None."""
    nearest = min(marks, key=lambda m: m.point.distance(center), default=None)
    if nearest is not None and nearest.point.distance(center) <= _MARK_SEARCH_RADIUS_M:
        return nearest
    return None


def _kind_by_footprint(footprint: Polygon) -> Kind:
    """Вид проёма без марки: дверь, если короткая сторона габарита > 0.3 м."""
    x_min, y_min, x_max, y_max = footprint.bounds
    return "door" if min(x_max - x_min, y_max - y_min) > _DOOR_MIN_SHORT_SIDE_M else "window"
