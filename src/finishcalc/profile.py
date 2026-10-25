"""Профиль проекта: какие слои, блоки и атрибуты чертежа играют какую роль.

Сначала роли ищутся по правилам (`detect_profile`), нерешённые уточняет ИИ
(`refine_profile`); он только выбирает имена из найденных в чертеже.
"""

import json
import re
from dataclasses import fields
from pathlib import Path

from ezdxf.document import Drawing

from finishcalc.llm import LLM
from finishcalc.model import Profile

_ROLE_FIELDS = [f.name for f in fields(Profile) if f.name != "mm_per_unit"]
_OPTIONAL_FIELDS = ("door_mark_layer", "window_mark_layer")
# Блок-номерок / блок типа пола -> тег его атрибута.
_TAG_ATTR_OF = {"room_tag_block": "room_tag_attr", "floor_tag_block": "floor_tag_attr"}

# Правила по имени (после нижнего регистра и замены «ё» на «е»).
_CONTOUR_LAYER = re.compile(r"area|площ|помещ")
_ROOM_TAG_BLOCK = re.compile(r"ном|room|пом")
_FLOOR_TAG_BLOCK = re.compile(r"пол|floor")
_OPENINGS_LAYER = re.compile(r"win|door|двер|окн|проем")
_DOOR_MARK_LAYER = re.compile(r"двер")
_WINDOW_MARK_LAYER = re.compile(r"окон|проем")

_MM_PER_UNIT = {4: 1.0, 5: 10.0, 6: 1000.0}  # $INSUNITS: мм, см, м
_NONE = "none"
_MAX_LAYER_SAMPLES = 3
_MAX_BLOCK_SAMPLES = 5
_MAX_SAMPLE_LENGTH = 80

_FIELD_HINTS = {
    "room_contour_layer": "слой, где замкнутыми полилиниями нарисованы контуры помещений",
    "room_tag_block": "блок-номерок помещения; номер лежит в его единственном атрибуте",
    "room_tag_attr": "тег атрибута блока-номерка, в котором записан номер помещения",
    "floor_tag_block": "блок с типом пола; тип лежит в его единственном атрибуте",
    "floor_tag_attr": "тег атрибута блока типа пола, в котором записан тип пола",
    "openings_layer": "слой со вставками блоков дверей и окон",
    "door_mark_layer": "слой текстов (MTEXT) с марками дверей; none, если марок дверей нет",
    "window_mark_layer": "слой текстов (MTEXT) с марками окон и проёмов; none, если марок нет",
}
_SYSTEM_PROMPT = (
    "Ты помогаешь разобраться в чертеже AutoCAD с поэтажным планом здания. "
    "По сводке слоёв и блоков определи, какой слой, блок или тег атрибута играет "
    "каждую из запрошенных ролей. Выбирай только значения из списка допустимых для "
    "роли; если подходящего нет, ответь none. Ничего не считай и не придумывай."
)


class ProfileError(Exception):
    """Профиль определить не удалось; `fields` — имена полей `Profile` без ответа."""

    def __init__(self, field_names: list[str]):
        self.fields = list(field_names)
        super().__init__("Не удалось определить поля профиля: " + ", ".join(self.fields))


def summarize_drawing(doc: Drawing) -> dict:
    """Сводка чертежа для определения профиля и для запроса к ИИ.

    `layers` — слои с объектами: число объектов каждого типа и до 3 образцов текста.
    `blocks` — именованные блоки со вставками (безымянные `*…` пропущены): число
    вставок, теги атрибутов и до 5 образцов их значений. `insunits` — `$INSUNITS`.
    """
    return _scan(doc)[0]


def detect_profile(doc: Drawing) -> tuple[dict, list[str]]:
    """Роли, найденные по правилам, и список нерешённых полей `Profile`.

    Нерешённым поле остаётся, если кандидатов нет или несколько равных. В
    найденных всегда есть `mm_per_unit`.
    """
    summary, closed_polylines = _scan(doc)
    layers = {layer["name"]: layer["types"] for layer in summary["layers"]}
    found: dict = {}

    contour = _only(
        name for name, count in closed_polylines.items()
        if count and _CONTOUR_LAYER.search(_fold(name))
    )
    if contour:
        found["room_contour_layer"] = contour
    for block_field, pattern in (("room_tag_block", _ROOM_TAG_BLOCK),
                                 ("floor_tag_block", _FLOOR_TAG_BLOCK)):
        block = _only(
            b for b in summary["blocks"]
            if len(b["attribs"]) == 1 and pattern.search(_fold(b["name"]))
        )
        if block:
            found[block_field] = block["name"]
            found[_TAG_ATTR_OF[block_field]] = block["attribs"][0]

    openings = _top({
        name: types.get("INSERT", 0) for name, types in layers.items()
        if _OPENINGS_LAYER.search(_fold(name))
    })
    if openings:
        found["openings_layer"] = openings

    def mtext_counts(pattern: re.Pattern, exclude: str | None = None) -> dict[str, int]:
        return {name: types.get("MTEXT", 0) for name, types in layers.items()
                if name != exclude and pattern.search(_fold(name))}

    door_marks = _top(mtext_counts(_DOOR_MARK_LAYER))
    if door_marks:
        found["door_mark_layer"] = door_marks
    window_marks = _top(mtext_counts(_WINDOW_MARK_LAYER, exclude=door_marks))
    if window_marks:
        found["window_mark_layer"] = window_marks

    found["mm_per_unit"] = _MM_PER_UNIT.get(summary["insunits"], 1.0)
    ordered = {name: found[name] for name in (*_ROLE_FIELDS, "mm_per_unit") if name in found}
    unresolved = [name for name in _ROLE_FIELDS if name not in found]
    return ordered, unresolved


def refine_profile(summary: dict, found: dict, unresolved: list[str], llm: LLM) -> Profile:
    """Профиль из найденных по правилам полей и ответа ИИ по нерешённым.

    ИИ выбирает только имена слоёв, блоков и тегов из сводки (или `none` для
    необязательных марок). Недопустимый ответ — `ProfileError` с такими полями.
    """
    if not unresolved:
        return Profile(**found)

    candidates = _candidates(summary)
    properties = {}
    for name in unresolved:
        options = candidates[_candidate_kind(name)]
        properties[name] = {
            "type": "string",
            "description": _FIELD_HINTS[name],
            "enum": [*options, _NONE],
        }
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(unresolved),
        "additionalProperties": False,
    }
    user = json.dumps(
        {"найдено": found, "определить": unresolved, "слои": summary["layers"],
         "блоки": summary["blocks"]},
        ensure_ascii=False,
    )
    answer = llm.complete_json(_SYSTEM_PROMPT, user, schema, "profile")

    values = dict(found)
    rejected = []
    for name in unresolved:
        value = answer.get(name)
        if value == _NONE and name in _OPTIONAL_FIELDS:
            values[name] = None
        elif isinstance(value, str) and value in candidates[_candidate_kind(name)]:
            values[name] = value
        else:
            rejected.append(name)

    block_tags = {b["name"]: b["attribs"] for b in summary["blocks"]}
    for block_field, attr_field in _TAG_ATTR_OF.items():
        if block_field in rejected or attr_field in rejected:
            continue
        if values[attr_field] not in block_tags.get(values[block_field], []):
            rejected.append(attr_field)

    if rejected:
        raise ProfileError([name for name in _ROLE_FIELDS if name in rejected])
    return Profile(**values)


def save_profile(p: Profile, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(p.to_json(), encoding="utf-8")


def load_profile(path: Path) -> Profile:
    return Profile.from_json(Path(path).read_text(encoding="utf-8"))


def _fold(name: str) -> str:
    """Имя для сравнения с правилами: без учёта регистра, «ё» как «е»."""
    return name.casefold().replace("ё", "е")


def _only(candidates):
    """Единственный кандидат или None (нет кандидатов либо их несколько)."""
    items = list(candidates)
    return items[0] if len(items) == 1 else None


def _top(counts: dict[str, int]) -> str | None:
    """Ключ с наибольшим положительным числом; при равенстве первых мест — None."""
    ranked = sorted((n for n in counts.values() if n > 0), reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0] == ranked[1]):
        return None
    return next(name for name, n in counts.items() if n == ranked[0])


def _candidate_kind(field: str) -> str:
    return field.rsplit("_", 1)[-1]  # "layer", "block" или "attr"


def _candidates(summary: dict) -> dict[str, list[str]]:
    """Допустимые значения полей: слои, блоки с атрибутами, теги атрибутов."""
    attr_blocks = [b for b in summary["blocks"] if b["attribs"]]
    return {
        "layer": [layer["name"] for layer in summary["layers"]],
        "block": [b["name"] for b in attr_blocks],
        "attr": list(dict.fromkeys(tag for b in attr_blocks for tag in b["attribs"])),
    }


def _scan(doc: Drawing) -> tuple[dict, dict[str, int]]:
    """Один проход по пространству модели: сводка и число замкнутых полилиний по слоям."""
    layers: dict[str, dict] = {}
    blocks: dict[str, dict] = {}
    closed_polylines: dict[str, int] = {}

    for entity in doc.modelspace():
        kind = entity.dxftype()
        layer = layers.setdefault(
            entity.dxf.layer, {"name": entity.dxf.layer, "types": {}, "samples": []})
        layer["types"][kind] = layer["types"].get(kind, 0) + 1
        text = _entity_text(entity)
        if text and text not in layer["samples"] and len(layer["samples"]) < _MAX_LAYER_SAMPLES:
            layer["samples"].append(text)
        if _is_closed_polyline(entity):
            closed_polylines[entity.dxf.layer] = closed_polylines.get(entity.dxf.layer, 0) + 1

        if kind == "INSERT" and not entity.dxf.name.startswith("*"):
            block = blocks.setdefault(
                entity.dxf.name,
                {"name": entity.dxf.name, "count": 0, "attribs": [], "samples": []})
            block["count"] += 1
            for attrib in entity.attribs:
                if attrib.dxf.tag not in block["attribs"]:
                    block["attribs"].append(attrib.dxf.tag)
                value = _clean(attrib.dxf.text)
                if (value and value not in block["samples"]
                        and len(block["samples"]) < _MAX_BLOCK_SAMPLES):
                    block["samples"].append(value)

    summary = {
        "layers": list(layers.values()),
        "blocks": list(blocks.values()),
        "insunits": int(doc.header.get("$INSUNITS", 0)),
    }
    return summary, closed_polylines


def _entity_text(entity) -> str:
    kind = entity.dxftype()
    if kind == "MTEXT":
        return _clean(entity.plain_text())
    if kind == "TEXT":
        return _clean(entity.dxf.text)
    return ""


def _clean(text: str) -> str:
    return " ".join(text.split())[:_MAX_SAMPLE_LENGTH]


def _is_closed_polyline(entity) -> bool:
    kind = entity.dxftype()
    if kind == "LWPOLYLINE":
        return bool(entity.closed)
    if kind == "POLYLINE":
        return bool(entity.is_closed)
    return False
