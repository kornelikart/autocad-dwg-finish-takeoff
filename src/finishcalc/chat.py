"""Чат по результату: вопрос сметчика и таблицы расчёта -> ответ ИИ.

ИИ только находит ответ в готовых таблицах; итоги по этажам заранее считает `calc`,
поэтому вопрос «сколько керамогранита на 2-м этаже» не требует от модели арифметики.
"""

import csv
import io

from finishcalc import calc
from finishcalc.llm import LLM, LLMError
from finishcalc.model import MaterialLine, Room
from finishcalc.norms import Norm
from finishcalc.pipeline import ProjectResult

NO_DATA = "в данных этого нет"
_NO_FLOOR = "не определён"
_SYSTEM_PROMPT = (
    "Ты помощник сметчика. Отвечай по-русски, коротко, только по таблицам расчёта из сообщения "
    "пользователя. Ничего не считай сам: бери числа из таблиц как есть и указывай единицы. "
    "Если ответа в таблицах нет, ответь ровно «" + NO_DATA + "»."
)
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
           "required": ["answer"], "additionalProperties": False}


def answer(question: str, result: ProjectResult, llm: LLM, norms: dict[str, Norm] | None = None) -> str:
    """Ответ на вопрос по результату. С `norms` в контекст входят и материалы по этажам.

    Raises:
        LLMError: ИИ не ответил или вернул пустой ответ.
    """
    user = f"{context(result, norms)}\nВопрос: {question.strip()}"
    reply = llm.complete_json(_SYSTEM_PROMPT, user, _SCHEMA, "chat").get("answer")
    if not isinstance(reply, str) or not reply.strip():
        raise LLMError("ИИ вернул пустой ответ")
    return reply.strip()


def context(result: ProjectResult, norms: dict[str, Norm] | None = None) -> str:
    """Таблицы расчёта в виде CSV с разделителем «;» и десятичной запятой."""
    parts = [_section("Материалы, всё здание", ["Материал", "Ед.", "Количество", "Запас, %"],
                      [_material_row(line) for line in result.materials])]
    if norms is not None:
        rows = []
        for floor, rooms in _rooms_by_floor(result).items():
            lines, _ = calc.aggregate_materials(rooms, result.areas, result.floor_types, norms)
            rows.extend([floor, *_material_row(line)[:3]] for line in lines)
        parts.append(_section("Материалы по этажам", ["Этаж", "Материал", "Ед.", "Количество"], rows))
    parts.append(_section("Площадь пола по типам", ["Тип пола", "Площадь, м²"],
                          [[code, calc.format_ru(area)] for code, area in result.floor_type_areas.items()]))
    parts.append(_section(
        "Помещения", ["№", "Наименование", "Этаж", "Тип пола", "Пол, м²", "Стены, м²", "Потолок, м²"],
        [[room.number, room.name, room.floor or _NO_FLOOR, room.floor_type or "",
          *(calc.format_ru(calc.round_half_up(value, 1)) for value in _room_areas(result, room.number))]
         for room in result.rooms]))
    return "\n".join(parts)


def _rooms_by_floor(result: ProjectResult) -> dict[str, list[Room]]:
    groups: dict[str, list[Room]] = {}
    for room in result.rooms:
        groups.setdefault(room.floor or _NO_FLOOR, []).append(room)
    return groups


def _room_areas(result: ProjectResult, number: str) -> tuple[float, float, float]:
    areas = result.areas[number]
    return areas.floor_m2, areas.walls_m2, areas.ceiling_m2


def _material_row(line: MaterialLine) -> list[str]:
    return [line.material, line.unit, calc.format_ru(line.quantity, calc.unit_digits(line.unit)),
            calc.format_ru(line.reserve_pct, 0)]


def _section(title: str, header: list[str], rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return f"{title}:\n{buffer.getvalue()}"
