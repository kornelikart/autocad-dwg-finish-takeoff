"""Расчёт площадей помещений и итоговых количеств материалов.

Чистые функции без ИИ и без чтения файлов: геометрия и проёмы берутся из `Room`,
отделка — из `FinishSpec`, расход — из справочника норм.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from finishcalc.finishes import OTHER, UNSPECIFIED
from finishcalc.model import FloorLayer, FloorType, Issue, MaterialLine, Room, Zone
from finishcalc.norms import Norm

_MM_PER_M = 1000
_NAME_LIMIT = 60
_NO_NORM_NAME = "Без нормы"
# Единицы, в которых количество берётся прямо из площади или объёма; у остальных — по расходу на м².
_DIRECT_UNITS = ("м²", "м³")
# Знаков после запятой при округлении итога: м² — 0.1, м³ — 0.01, остальные — 0.1.
_DIGITS = {"м³": 2}
_DEFAULT_DIGITS = 1


@dataclass
class ZoneArea:
    """Площадь одной полосы стены за вычетом проёмов (и захода пола у нижней полосы)."""

    from_m: float
    to_m: float
    area_m2: float
    items: list[str]
    unspecified: bool
    source_text: str


@dataclass
class RoomAreas:
    """Площади помещения: пол, потолок, стены по зонам и полоса захода пола на стену."""

    height_m: float
    floor_m2: float
    ceiling_m2: float
    walls_m2: float
    upturn_m2: float
    zones: list[ZoneArea]


def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Длина пересечения отрезков [a0; a1] и [b0; b1]; 0, если они не пересекаются."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def _is_unspecified(zone: Zone) -> bool:
    return not zone.items and zone.source_text == UNSPECIFIED


def room_areas(room: Room, default_height_m: float) -> RoomAreas:
    """Площади помещения. Высота — из описания потолка, иначе `default_height_m`."""
    finish = room.finish
    height = default_height_m
    if finish is not None and finish.ceiling_height_m is not None:
        height = finish.ceiling_height_m

    upturn = 0.0
    if finish is not None:
        door_widths = sum(o.width_m for o in room.openings if o.kind == "door")
        upturn = max(0.0, room.perimeter_m - door_widths) * finish.floor_upturn_m

    walls = finish.walls if finish is not None and finish.walls else [Zone(0.0, None, [], "")]
    zones: list[ZoneArea] = []
    upturn_deducted = upturn <= 0.0
    for zone in walls:
        low = min(max(zone.from_m, 0.0), height)
        high = height if zone.to_m is None else min(max(zone.to_m, low), height)
        area = room.perimeter_m * (high - low)
        for opening in room.openings:
            top = opening.sill_m + opening.height_m
            area -= opening.width_m * overlap(opening.sill_m, top, low, high)
        if not upturn_deducted and zone.from_m <= 0.0:
            area -= upturn
            upturn_deducted = True
        zones.append(ZoneArea(low, high, max(area, 0.0), list(zone.items), _is_unspecified(zone), zone.source_text))

    return RoomAreas(
        height_m=height,
        floor_m2=room.area_m2,
        ceiling_m2=room.area_m2,
        walls_m2=sum(z.area_m2 for z in zones),
        upturn_m2=upturn,
        zones=zones,
    )


def _unique(keys: list[str]) -> list[str]:
    """Ключи без повторов, в прежнем порядке (ИИ может повторить позицию)."""
    return list(dict.fromkeys(keys))


def _fallback_name(source_text: str) -> str:
    return source_text.strip()[:_NAME_LIMIT].strip() or _NO_NORM_NAME


def _norm_applies(norm: Norm | None, volume: bool) -> bool:
    """Норма годится, только если её единица согласована с переданным количеством:
    объём (слой `volume`) — только м³-норма; площадь — любая, кроме м³, и для кг, л нужен расход."""
    if norm is None:
        return False
    if volume:
        return norm.unit == "м³"
    return norm.unit == "м²" or (norm.unit != "м³" and norm.per_m2 is not None)


def _norm_quantity(norm: Norm, amount: float) -> float:
    """Количество по норме: м², м³ — `amount` с запасом; кг, л — расход на `amount` м²."""
    reserve = 1 + norm.reserve_pct / 100
    if norm.unit in _DIRECT_UNITS:
        return amount * reserve
    return amount * norm.per_m2 * norm.coats * reserve


def _add(totals: dict[tuple[str, str], MaterialLine], room_number: str, material: str, unit: str,
         quantity: float, reserve_pct: float, basis: str) -> None:
    if quantity <= 0:
        return
    line = totals.get((material, unit))
    if line is None:
        totals[(material, unit)] = MaterialLine(material, unit, quantity, reserve_pct, basis, [room_number])
        return
    line.quantity += quantity
    if room_number not in line.rooms:
        line.rooms.append(room_number)


def _add_item(totals: dict[tuple[str, str], MaterialLine], room_number: str, key: str,
              norms: dict[str, Norm], amount: float, source_text: str, volume: bool = False) -> None:
    """Позиция `key` на поверхности площадью `amount` м² (или слой объёмом `amount` м³, если `volume`).
    Нет подходящей нормы — количество с чертежа в м² (м³ для слоя), название — исходный текст."""
    norm = norms.get(key) if key != OTHER else None
    if not _norm_applies(norm, volume):
        _add(totals, room_number, _fallback_name(source_text), "м³" if volume else "м²", amount, 0.0, "чертёж")
        return
    _add(totals, room_number, norm.title, norm.unit, _norm_quantity(norm, amount), norm.reserve_pct, "норма")


def _thickness_mm(layer: FloorLayer) -> float | None:
    """Толщина слоя: среднее min и max; известна одна граница — она же и вторая."""
    known = [t for t in (layer.thickness_mm_min, layer.thickness_mm_max) if t is not None]
    return sum(known) / len(known) if known else None


def round_half_up(value: float, digits: int) -> float:
    """Округление «от половины вверх»; `round(…, 9)` убирает шум плавающей запятой
    (247.15 не превращается в 247.1)."""
    return float(Decimal(repr(round(value, 9))).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def unit_digits(unit: str) -> int:
    """Знаков после запятой при округлении величины в единице `unit`: м³ — 2, остальные — 1."""
    return _DIGITS.get(unit, _DEFAULT_DIGITS)


def format_ru(value: float, digits: int = 1) -> str:
    """Число с десятичной запятой и `digits` знаками после неё: 4.68 -> «4,7»."""
    return f"{value:.{digits}f}".replace(".", ",")


def _unspecified_issue(room_number: str, bands: list[ZoneArea]) -> Issue:
    ranges = "; ".join(f"{format_ru(b.from_m)}–{format_ru(b.to_m)} м" for b in bands)
    total = sum(b.area_m2 for b in bands)
    return Issue("unspecified_wall_band", room_number,
                 f"Полоса стены {ranges} не описана в ведомости отделки, площадь {format_ru(total)} м²")


def aggregate_materials(rooms: list[Room], areas: dict[str, RoomAreas], floor_types: dict[str, FloorType],
                        norms: dict[str, Norm]) -> tuple[list[MaterialLine], list[Issue]]:
    """Итоговая ведомость материалов по всем помещениям и предупреждения расчёта.
    Строки сводятся по паре (материал, единица); округление — один раз, после суммирования."""
    totals: dict[tuple[str, str], MaterialLine] = {}
    issues: list[Issue] = []

    for room in rooms:
        number = room.number
        area = areas[number]
        finish = room.finish

        if finish is not None:
            for key in _unique(finish.ceiling_items):
                _add_item(totals, number, key, norms, area.ceiling_m2, finish.source.get("ceiling", ""))
            for zone in area.zones:
                for key in _unique(zone.items):
                    _add_item(totals, number, key, norms, zone.area_m2, zone.source_text)
            bands = [z for z in area.zones if z.unspecified and z.area_m2 > 0]
            if bands:
                issues.append(_unspecified_issue(number, bands))

        floor_type = floor_types.get(room.floor_type) if room.floor_type else None
        if floor_type is not None and floor_type.layers:
            for index, layer in enumerate(floor_type.layers):
                if layer.count_as == "piece":
                    continue
                if layer.count_as == "area":
                    layer_area = area.floor_m2 + (area.upturn_m2 if index == 0 else 0.0)
                    _add_item(totals, number, layer.norm_key, norms, layer_area, layer.material)
                    continue
                thickness = _thickness_mm(layer)
                if thickness is None:
                    issues.append(Issue(
                        "floor_layer_without_thickness", number,
                        f"Слой «{layer.material}» типа пола {floor_type.code}: толщина не указана, слой не учтён"))
                    continue
                volume = area.floor_m2 * thickness / _MM_PER_M
                _add_item(totals, number, layer.norm_key, norms, volume, layer.material, volume=True)
        elif finish is not None:
            floor_area = area.floor_m2 + area.upturn_m2
            for key in _unique(finish.floor_items):
                _add_item(totals, number, key, norms, floor_area, finish.source.get("floor", ""))

    lines = list(totals.values())
    for line in lines:
        line.quantity = round_half_up(line.quantity, unit_digits(line.unit))
    return lines, issues


def floor_type_areas(rooms: list[Room], areas: dict[str, RoomAreas]) -> dict[str, float]:
    """Площадь пола по типам (код типа → м², до 0.1); помещения без типа пропускаются."""
    totals: dict[str, float] = {}
    for room in rooms:
        if room.floor_type:
            totals[room.floor_type] = totals.get(room.floor_type, 0.0) + areas[room.number].floor_m2
    return {code: round_half_up(total, unit_digits("м²")) for code, total in totals.items()}
