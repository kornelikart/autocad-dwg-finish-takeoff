"""Помещения из чертежа: контуры в слое помещений, номерки и типы пола."""

from ezdxf.document import Drawing
from ezdxf.path import make_path
from shapely.geometry import MultiPolygon, Point, Polygon

from finishcalc.model import Issue, Profile, Room
from finishcalc.numbers import normalize_room_number

# Контур — дубль принятого, если они различаются меньше чем на эту долю площади.
_DUPLICATE_AREA_SHARE = 0.01
# Допуск спрямления дуг, мм.
_FLATTEN_TOLERANCE_MM = 1.0


def extract_rooms(doc: Drawing, profile: Profile) -> tuple[list[Room], list[Issue]]:
    """Помещения чертежа и предупреждения о контурах и номерах без пары.

    Контур — полилиния в слое помещений. Номер относится к наибольшему из содержащих
    его контуров, в котором нет других номеров: так безымянный контур внутри помещения
    (колонна, шахта, зона) не подменяет само помещение. Если такого нет — к наименьшему
    содержащему. Тип пола — к наименьшему содержащему его контуру с номером, иначе к
    наименьшему содержащему. Геометрия в метрах.
    """
    scale = profile.mm_per_unit / 1000
    contours = _read_contours(doc, profile, scale)
    smallest_first = sorted(range(len(contours)), key=lambda i: contours[i].area)

    numbers_in: list[list[str]] = [[] for _ in contours]
    floor_types_in: list[list[str]] = [[] for _ in contours]
    issues: list[Issue] = []

    room_tags = []
    for raw, point in _read_tags(doc, profile.room_tag_block, profile.room_tag_attr, scale):
        number = normalize_room_number(raw)
        if number:
            room_tags.append((number, _covering(contours, smallest_first, point)))
    numbers_covered: list[set[str]] = [set() for _ in contours]
    for number, covering in room_tags:
        for index in covering:
            numbers_covered[index].add(number)

    for number, covering in room_tags:
        if not covering:
            issues.append(Issue(
                "number_without_contour", number,
                f"Номер {number} не попадает ни в один контур помещения",
            ))
            continue
        only_this = [i for i in covering if numbers_covered[i] == {number}]
        index = only_this[-1] if only_this else covering[0]
        if number not in numbers_in[index]:
            numbers_in[index].append(number)

    for raw, point in _read_tags(doc, profile.floor_tag_block, profile.floor_tag_attr, scale):
        floor_type = raw.strip()
        covering = _covering(contours, smallest_first, point)
        if floor_type and covering:
            numbered = [i for i in covering if numbers_in[i]]
            floor_types_in[(numbered or covering)[0]].append(floor_type)

    rooms: list[Room] = []
    for index, contour in enumerate(contours):
        numbers = numbers_in[index]
        where = f"около ({contour.centroid.x:.2f}; {contour.centroid.y:.2f}) м"
        if not numbers:
            issues.append(Issue(
                "contour_without_number", None,
                f"Контур площадью {contour.area:.2f} м² {where} без номера помещения",
            ))
            continue
        if len(numbers) > 1:
            issues.append(Issue(
                "contour_with_several_numbers", None,
                f"В контуре {where} несколько номеров: {', '.join(numbers)}",
            ))
            continue

        number = numbers[0]
        distinct_types = list(dict.fromkeys(floor_types_in[index]))
        floor_type = distinct_types[0] if len(distinct_types) == 1 else None
        if len(distinct_types) > 1:
            issues.append(Issue(
                "several_floor_types", number,
                f"В помещении {number} несколько типов пола: {', '.join(distinct_types)}",
            ))
        rooms.append(Room(
            number=number,
            name="",
            floor=None,
            polygon=contour,
            area_m2=contour.area,
            perimeter_m=contour.length,
            floor_type=floor_type,
        ))
    return rooms, issues


def _read_contours(doc: Drawing, profile: Profile, scale: float) -> list[Polygon]:
    """Контуры помещений в метрах, без повторяющихся."""
    flatten_distance = _FLATTEN_TOLERANCE_MM / profile.mm_per_unit
    accepted: list[Polygon] = []
    for entity in doc.modelspace().query("LWPOLYLINE POLYLINE"):
        if entity.dxf.layer != profile.room_contour_layer:
            continue
        points = [(v.x * scale, v.y * scale)
                  for v in make_path(entity).flattening(distance=flatten_distance)]
        polygon = _to_polygon(points)
        if polygon is not None and not _is_duplicate(polygon, accepted):
            accepted.append(polygon)
    return accepted


def _to_polygon(points: list[tuple[float, float]]) -> Polygon | None:
    """Полигон по вершинам; самопересечения чинятся через buffer(0).
    Вырожденный контур (меньше трёх вершин, нулевая площадь) — None."""
    if len(points) < 3:
        return None
    polygon = Polygon(points)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
        if isinstance(polygon, MultiPolygon):
            polygon = max(polygon.geoms, key=lambda part: part.area)
    if not isinstance(polygon, Polygon) or polygon.is_empty or polygon.area <= 0:
        return None
    return polygon


def _is_duplicate(polygon: Polygon, accepted: list[Polygon]) -> bool:
    tolerance = _DUPLICATE_AREA_SHARE * polygon.area
    return any(
        abs(other.area - polygon.area) < tolerance
        and other.symmetric_difference(polygon).area < tolerance
        for other in accepted
    )


def _read_tags(doc: Drawing, block_name: str, attr_tag: str,
               scale: float) -> list[tuple[str, Point]]:
    """Вставки блока: значение атрибута attr_tag и точка вставки в метрах."""
    tags = []
    for insert in doc.modelspace().query("INSERT"):
        if insert.dxf.name != block_name:
            continue
        for attrib in insert.attribs:
            if attrib.dxf.tag == attr_tag:
                x, y = insert.dxf.insert.x * scale, insert.dxf.insert.y * scale
                tags.append((attrib.dxf.text, Point(x, y)))
                break
    return tags


def _covering(contours: list[Polygon], smallest_first: list[int], point: Point) -> list[int]:
    """Индексы контуров, содержащих точку, по возрастанию площади.
    smallest_first — индексы всех контуров по возрастанию площади."""
    return [i for i in smallest_first if contours[i].covers(point)]
