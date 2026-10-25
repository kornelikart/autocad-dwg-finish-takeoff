"""Разбор описаний отделки из ведомости и составов полов из экспликации.

Правила (регулярные выражения) режут текст на зоны стен, находят высоту потолка
и заведение пола на стену; ИИ только раскладывает короткие описания на позиции
справочника норм. Площади и количества здесь не считаются.
"""

import re

from finishcalc.extract.tables import FinishRow, FloorTypeRow
from finishcalc.llm import LLM
from finishcalc.model import FinishSpec, FloorLayer, FloorType, Zone
from finishcalc.norms import Norm

UNSPECIFIED = "не указано в ведомости"
AI_SUGGESTED = "предложено ИИ"
OTHER = "other"

_MM_PER_M = 1000
# Что обрамляет текст зоны после маркера: точки, тире, пробелы.
_ZONE_TEXT_EDGES = " \t\r\n\xa0.-–—"

_ZONE_MARKER = re.compile(r"(?<!\w)(выше|ниже)\s*(\d+)\s*мм", re.IGNORECASE)
_CEILING_HEIGHT = re.compile(
    r"h\s*=\s*(\d+(?:[.,]\d+)?)(?:\s*[-–—]\s*(\d+(?:[.,]\d+)?))?\s*(мм|м)", re.IGNORECASE)
_FLOOR_UPTURN = re.compile(r"завести\s+на\s+стену\s+на\s+(\d+)\s*мм", re.IGNORECASE)


def _fold(text: str) -> str:
    """Копия текста для поиска: «ё» как «е». Длина не меняется, поэтому позиции
    совпадений годятся и для исходной строки."""
    return text.replace("ё", "е").replace("Ё", "Е")


def _zone_text(raw: str) -> str:
    return raw.strip(_ZONE_TEXT_EDGES) or UNSPECIFIED


def split_zones(text: str) -> list[tuple[float, float | None, str]]:
    """Зоны стены снизу вверх: (от, до, текст); высоты в метрах, `None` — до потолка.

    Маркеры «ниже N мм» и «выше N мм» делят текст на зоны; зазор между ними и
    сторона без маркера получают текст `UNSPECIFIED`. Без маркеров — одна зона."""
    markers = list(_ZONE_MARKER.finditer(_fold(text)))
    if not markers:
        return [(0.0, None, _zone_text(text))]

    below: tuple[float, str] | None = None
    above: tuple[float, str] | None = None
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        band = (int(marker.group(2)) / _MM_PER_M, _zone_text(text[marker.end():end]))
        if marker.group(1).lower() == "ниже":
            below = below or band
        else:
            above = above or band

    zones: list[tuple[float, float | None, str]] = []
    if below:
        zones.append((0.0, below[0], below[1]))
    if above:
        if not below:
            zones.append((0.0, above[0], UNSPECIFIED))
        elif below[0] < above[0]:
            zones.append((below[0], above[0], UNSPECIFIED))
        zones.append((above[0], None, above[1]))
    else:
        zones.append((below[0], None, UNSPECIFIED))
    return zones


def ceiling_height(text: str) -> float | None:
    """Высота подвесного потолка из «h=3,0м» или «h=2,5-3,0м» (берётся наибольшая), м."""
    heights = []
    for match in _CEILING_HEIGHT.finditer(_fold(text)):
        divisor = _MM_PER_M if match.group(3).lower() == "мм" else 1
        heights.extend(float(g.replace(",", ".")) / divisor for g in match.group(1, 2) if g)
    return max(heights) if heights else None


def floor_upturn(text: str) -> float:
    """Высота заведения пола на стену из «завести на стену на 300мм», м; иначе 0.0."""
    match = _FLOOR_UPTURN.search(_fold(text))
    return int(match.group(1)) / _MM_PER_M if match else 0.0


def _catalogue(norms: dict[str, Norm]) -> str:
    return "\n".join(f"{key} — {norm.title}" for key, norm in norms.items())


def _keys_schema(norms: dict[str, Norm]) -> dict:
    return {"type": "array", "items": {"type": "string", "enum": [*norms, OTHER]}}


def _known_keys(keys: list[str], norms: dict[str, Norm]) -> list[str]:
    """Ключи вне справочника заменяются на `other`."""
    return [key if key in norms else OTHER for key in keys]


_MAPPING_RULES = """\
Правила:
- «штукатурка» → plaster_gypsum;
- «улучшенная штукатурка» или «по шпатлевке» → добавь putty после штукатурки (или первой позицией, если штукатурки нет);
- «окраска» → primer, paint_wd;
- «керамогранит» или «плитка» → porcelain_tile, tile_adhesive;
- «подвесной потолок», «Armstrong» → suspended_ceiling;
- «ламинат» → laminate;
- «гидроизоляция», «изопласт» → roll_waterproofing;
- «стяжка» → screed;
- не подходит ничего → other.
Цвет, марку и производителя не разбирай: это не позиции. Пример: «улучшенная штукатурка, окраска» → \
plaster_gypsum, putty, primer, paint_wd."""


def map_items(text: str, norms: dict[str, Norm], llm: LLM) -> list[str]:
    """Позиции справочника (ключи или `other`) по описанию отделки, в порядке слоёв."""
    if not text.strip():
        return []
    system = (
        "Ты сметчик. Разложи описание отделки на позиции справочника в порядке слоёв.\n\n"
        f"Справочник (ключ — название):\n{_catalogue(norms)}\n\n{_MAPPING_RULES}")
    schema = {"type": "object", "properties": {"items": _keys_schema(norms)},
              "required": ["items"], "additionalProperties": False}
    answer = llm.complete_json(system, text, schema, "finish_items")
    return _known_keys(answer["items"], norms)


def parse_finish(row: FinishRow, norms: dict[str, Norm], llm: LLM) -> FinishSpec:
    """Отделка помещения из строки ведомости; вызовы ИИ идут по очереди."""
    ceiling_items = map_items(row.ceiling, norms, llm)
    walls = [
        Zone(from_m, to_m, [] if text == UNSPECIFIED else map_items(text, norms, llm), text)
        for from_m, to_m, text in split_zones(row.walls)
    ]
    floor_items = map_items(row.floor, norms, llm)
    return FinishSpec(
        ceiling_items=ceiling_items,
        ceiling_height_m=ceiling_height(row.ceiling),
        walls=walls,
        floor_items=floor_items,
        floor_upturn_m=floor_upturn(row.floor),
        source={"ceiling": row.ceiling, "walls": row.walls, "floor": row.floor},
    )


def _thickness(value: float | None) -> float | None:
    """Толщина слоя, мм; 0 («не указана») → None."""
    return float(value) if value and value > 0 else None


def parse_floor_type(row: FloorTypeRow, norms: dict[str, Norm], llm: LLM) -> FloorType:
    """Слои типа пола из текста экспликации."""
    system = (
        "Ты сметчик. Разбери состав пола на слои сверху вниз.\n"
        "Слои пронумерованы «1.», «2.», … и могут быть разделены символом «|» или не разделены "
        "вообще: тогда граница слоя — следующий номер. Число перед словом «слой» "
        "(«1 слой Изопласта») — количество слоёв материала, а не номер. "
        "«В25» — класс бетона, а не толщина.\n\n"
        "Для каждого слоя верни:\n"
        "- material — название материала как в тексте;\n"
        "- thickness_mm_min, thickness_mm_max — толщина в мм: одно значение — в оба поля, "
        "диапазон «50-95» — наименьшее и наибольшее; толщины нет — 0;\n"
        "- count_as — как считать слой: volume (растворы, бетон, засыпки — по объёму), "
        "area (плитка, покрытия, плёнки, рулонные и листовые материалы — по площади), "
        "piece (всё остальное: плита перекрытия, грунт основания);\n"
        "- norm_key — ключ справочника или other.\n\n"
        f"Справочник (ключ — название):\n{_catalogue(norms)}")
    layer_schema = {
        "type": "object",
        "properties": {
            "material": {"type": "string"},
            "thickness_mm_min": {"type": "number"},
            "thickness_mm_max": {"type": "number"},
            "count_as": {"type": "string", "enum": ["volume", "area", "piece"]},
            "norm_key": {"type": "string", "enum": [*norms, OTHER]},
        },
        "required": ["material", "thickness_mm_min", "thickness_mm_max", "count_as", "norm_key"],
        "additionalProperties": False,
    }
    schema = {"type": "object",
              "properties": {"layers": {"type": "array", "items": layer_schema}},
              "required": ["layers"], "additionalProperties": False}
    answer = llm.complete_json(system, row.layers_text, schema, "floor_layers")
    layers = [
        FloorLayer(
            material=raw["material"],
            thickness_mm_min=_thickness(raw["thickness_mm_min"]),
            thickness_mm_max=_thickness(raw["thickness_mm_max"]),
            count_as=raw["count_as"],
            norm_key=_known_keys([raw["norm_key"]], norms)[0],
        )
        for raw in answer["layers"]
    ]
    return FloorType(code=row.code, rooms_label=row.rooms_label, layers=layers,
                     source_text=row.layers_text)


def suggest_finish(room_name: str, norms: dict[str, Norm], llm: LLM) -> FinishSpec:
    """Типовая отделка помещения без строки в ведомости, предложенная ИИ."""
    system = (
        "Ты сметчик. По названию помещения предложи типовую отделку потолка (ceiling), "
        "стен (walls) и пола (floor): для каждой поверхности — позиции справочника в порядке слоёв. "
        "Если для поверхности нет подходящей позиции, верни пустой список.\n\n"
        f"Справочник (ключ — название):\n{_catalogue(norms)}\n\n{_MAPPING_RULES}")
    surfaces = ("ceiling", "walls", "floor")
    schema = {"type": "object", "properties": {s: _keys_schema(norms) for s in surfaces},
              "required": list(surfaces), "additionalProperties": False}
    answer = llm.complete_json(system, room_name, schema, "suggest_finish")
    return FinishSpec(
        ceiling_items=_known_keys(answer["ceiling"], norms),
        ceiling_height_m=None,
        walls=[Zone(0.0, None, _known_keys(answer["walls"], norms), AI_SUGGESTED)],
        floor_items=_known_keys(answer["floor"], norms),
        floor_upturn_m=0.0,
        source={s: AI_SUGGESTED for s in surfaces},
    )
