"""Сборка всего конвейера: чертежи и таблицы -> `ProjectResult`.

Порядок `analyze`: помещения планов, имена и этажи из экспликации, проёмы по спецификации,
отделка помещений, типы пола, площади, материалы. Числа считает `calc`; ИИ вызывается по очереди
(без потоков), `LLMError` и `ConvertError` уходят вызывающему.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar

import ezdxf
from ezdxf.document import Drawing

from finishcalc import calc
from finishcalc.convert import ConvertError, dwg_to_dxf
from finishcalc.extract.openings import extract_openings
from finishcalc.extract.rooms import extract_rooms
from finishcalc.extract.tables import (
    ClassifiedTable, FinishRow, FloorTypeRow, TableKind, classify_tables, parse_finish_schedule,
    parse_floor_explication, parse_openings_spec, parse_room_explication, read_tables)
from finishcalc.finishes import OTHER, parse_finish, parse_floor_type, suggest_finish
from finishcalc.llm import LLM
from finishcalc.model import FloorType, Issue, MaterialLine, Profile, Room
from finishcalc.norms import Norm

Mode = Literal["A", "B"]
_Row = TypeVar("_Row")


@dataclass
class ProjectResult:
    """Итог разбора проекта. Режим A — отделка из ведомости проектировщика,
    режим B — ведомости нет, отделку предложил ИИ."""

    rooms: list[Room]
    areas: dict[str, calc.RoomAreas]
    finish_rows: dict[str, FinishRow]
    floor_types: dict[str, FloorType]
    floor_type_areas: dict[str, float]
    materials: list[MaterialLine]
    issues: list[Issue]
    mode: Mode


def load_drawings(paths: list[Path], work_dir: Path) -> list[Drawing]:
    """Чертежи DWG или DXF; DWG сначала конвертируются в `work_dir`.

    Raises:
        ConvertError: файл не удалось конвертировать или прочитать как DXF.
    """
    docs = []
    for path in paths:
        path = Path(path)
        dxf = dwg_to_dxf(path, work_dir)
        try:
            docs.append(ezdxf.readfile(dxf))
        except (OSError, ezdxf.DXFError) as e:
            raise ConvertError(f"Не удалось прочитать {path.name} как DXF: {e}") from e
    return docs


def load_tables(docs: list[Drawing]) -> list[ClassifiedTable]:
    """Таблицы всех чертежей с видами. Продолжение таблицы ищется только в её чертеже."""
    return [table for doc in docs for table in classify_tables(read_tables(doc))]


def analyze(plan_docs: list[Drawing], tables: list[ClassifiedTable], profile: Profile, llm: LLM,
            norms: dict[str, Norm], floor_heights: dict[str, float],
            default_height_m: float = 3.0) -> ProjectResult:
    """Помещения планов + таблицы -> площади, материалы и предупреждения.

    `floor_heights` — высота помещения по этажу из экспликации ("1", "подвал"); этажа нет
    в словаре или он не определён — `default_height_m`. Высота «h=…» потолка из ведомости
    главнее. Есть таблица ведомости отделки — режим A, нет — режим B.
    """
    issues: list[Issue] = []
    rooms_by_doc = _collect_rooms(plan_docs, profile, issues)
    rooms = [room for group in rooms_by_doc for room in group]

    explication = _first_by_key(_rows(tables, "room_explication", parse_room_explication), lambda r: r.number)
    for room in rooms:
        row = explication.get(room.number)
        if row is not None:
            room.name, room.floor = row.name, row.floor

    _attach_openings(plan_docs, rooms_by_doc, profile, _openings_spec(tables), issues)

    mode: Mode = "A" if any(t.kind == "finish_schedule" for t in tables) else "B"
    finish_rows = _first_by_key(_rows(tables, "finish_schedule", parse_finish_schedule), lambda r: r.number)
    if mode == "A":
        issues.extend(_finishes_from_schedule(rooms, finish_rows, norms, llm))
    else:
        issues.extend(_finishes_from_ai(rooms, norms, llm))

    floor_types = _parse_floor_types(tables, norms, llm)
    issues.extend(_floor_type_issues(rooms, floor_types, compare_with_schedule=mode == "A"))

    areas = {room.number: calc.room_areas(room, _room_height(room, floor_heights, default_height_m))
             for room in rooms}
    materials, calc_issues = calc.aggregate_materials(rooms, areas, floor_types, norms)
    issues.extend(calc_issues)
    issues.append(Issue("reveals_not_counted", None, "Откосы проёмов не учтены"))

    return ProjectResult(
        rooms=rooms,
        areas=areas,
        finish_rows=finish_rows,
        floor_types=floor_types,
        floor_type_areas=calc.floor_type_areas(rooms, areas),
        materials=materials,
        issues=issues,
        mode=mode,
    )


def _rows(tables: list[ClassifiedTable], kind: TableKind,
          parse: Callable[[ClassifiedTable], list[_Row]]) -> list[_Row]:
    """Строки всех таблиц вида `kind` в порядке таблиц."""
    return [row for table in tables if table.kind == kind for row in parse(table)]


def _first_by_key(rows: list[_Row], key: Callable[[_Row], str]) -> dict[str, _Row]:
    """Строки по ключу; пустой ключ пропускается, при повторе остаётся первая строка."""
    result: dict[str, _Row] = {}
    for row in rows:
        k = key(row)
        if k and k not in result:
            result[k] = row
    return result


def _openings_spec(tables: list[ClassifiedTable]) -> dict[str, tuple[float, float]]:
    """Марка -> (ширина, высота) по всем таблицам спецификации; при повторе марки — первая."""
    spec: dict[str, tuple[float, float]] = {}
    for table in tables:
        if table.kind == "openings_spec":
            for mark, size in parse_openings_spec(table).items():
                spec.setdefault(mark, size)
    return spec


def _collect_rooms(plan_docs: list[Drawing], profile: Profile, issues: list[Issue]) -> list[list[Room]]:
    """Помещения каждого плана. Номер, уже встретившийся в другом контуре, пропускается:
    остаётся первый по порядку планов и контуров."""
    seen: set[str] = set()
    by_doc: list[list[Room]] = []
    for doc in plan_docs:
        doc_rooms, doc_issues = extract_rooms(doc, profile)
        issues.extend(doc_issues)
        kept: list[Room] = []
        for room in doc_rooms:
            if room.number in seen:
                center = room.polygon.centroid
                issues.append(Issue(
                    "duplicate_room_number", room.number,
                    f"Номер {room.number} стоит в нескольких контурах: учтён первый, контур "
                    f"{room.area_m2:.1f} м² около ({center.x:.2f}; {center.y:.2f}) м пропущен"))
                continue
            seen.add(room.number)
            kept.append(room)
        by_doc.append(kept)
    return by_doc


def _attach_openings(plan_docs: list[Drawing], rooms_by_doc: list[list[Room]], profile: Profile,
                     spec: dict[str, tuple[float, float]], issues: list[Issue]) -> None:
    """Проёмы каждого плана раскладываются по помещениям этого плана: проём между
    двумя помещениями попадает в оба."""
    by_number = {room.number: room for rooms in rooms_by_doc for room in rooms}
    for doc, rooms in zip(plan_docs, rooms_by_doc):
        openings, opening_issues = extract_openings(doc, profile, rooms, spec)
        issues.extend(opening_issues)
        for opening in openings:
            for number in opening.rooms:
                by_number[number].openings.append(opening)


def _finishes_from_schedule(rooms: list[Room], finish_rows: dict[str, FinishRow],
                            norms: dict[str, Norm], llm: LLM) -> list[Issue]:
    """Режим A: отделка по строке ведомости с номером помещения. ИИ разбирает только
    строки, у которых на плане есть помещение."""
    issues: list[Issue] = []
    for room in rooms:
        row = finish_rows.get(room.number)
        if row is None:
            issues.append(Issue("room_without_finish_row", room.number,
                                f"Помещения {room.number} нет в ведомости отделки, отделка не рассчитана"))
        else:
            room.finish = parse_finish(row, norms, llm)
    on_plan = {room.number for room in rooms}
    for number, row in finish_rows.items():
        if number not in on_plan:
            issues.append(Issue("finish_row_without_contour", number,
                                f"Помещение {number} «{row.name}» из ведомости отделки не найдено на плане"))
    return issues


def _finishes_from_ai(rooms: list[Room], norms: dict[str, Norm], llm: LLM) -> list[Issue]:
    """Режим B: ведомости нет, ИИ предлагает отделку по названию помещения (нет названия — по номеру)."""
    issues: list[Issue] = []
    for room in rooms:
        name = room.name or room.number
        room.finish = suggest_finish(name, norms, llm)
        issues.append(Issue("finish_suggested_by_ai", room.number,
                            f"Отделка помещения {room.number} предложена ИИ по названию «{name}», проверьте её"))
    return issues


def _parse_floor_types(tables: list[ClassifiedTable], norms: dict[str, Norm], llm: LLM) -> dict[str, FloorType]:
    """Типы пола по строкам экспликации полов (код -> тип; при повторе кода — первая строка)."""
    rows = _first_by_key(_rows(tables, "floor_explication", parse_floor_explication), lambda r: r.code)
    return {code: _floor_type(row, norms, llm) for code, row in rows.items()}


def _floor_type(row: FloorTypeRow, norms: dict[str, Norm], llm: LLM) -> FloorType:
    if not row.layers_text.strip():  # ИИ не должен выдумывать слои из пустого текста
        return FloorType(row.code, row.rooms_label, [], row.layers_text)
    return parse_floor_type(row, norms, llm)


def _floor_type_issues(rooms: list[Room], floor_types: dict[str, FloorType],
                       compare_with_schedule: bool) -> list[Issue]:
    """Тип пола с плана, которого нет в экспликации, и (режим A) расхождение с ведомостью."""
    issues: list[Issue] = []
    for room in rooms:
        code = room.floor_type
        if code is None:
            continue
        floor_type = floor_types.get(code)
        if floor_type is None:
            issues.append(Issue("floor_type_not_in_explication", room.number,
                                f"Тип пола {code} помещения {room.number} не описан в экспликации полов"))
        elif compare_with_schedule and _contradicts_schedule(room, floor_type):
            issues.append(Issue(
                "floor_type_mismatch", room.number,
                f"Тип пола {code} помещения {room.number} начинается со слоя «{floor_type.layers[0].material}», "
                f"а в ведомости пол: «{room.finish.source.get('floor', '')}»"))
    return issues


def _contradicts_schedule(room: Room, floor_type: FloorType) -> bool:
    """Верхний слой типа пола опознан, а среди материалов пола из ведомости его нет."""
    if not floor_type.layers or room.finish is None:
        return False
    top = floor_type.layers[0].norm_key
    return top != OTHER and bool(room.finish.floor_items) and top not in room.finish.floor_items


def _room_height(room: Room, floor_heights: dict[str, float], default_height_m: float) -> float:
    if room.floor is None:
        return default_height_m
    return floor_heights.get(room.floor, default_height_m)
