"""Таблицы AutoCAD: ведомость отделки, экспликации помещений и полов,
спецификация заполнения проёмов."""

import re
from dataclasses import dataclass
from typing import Literal

from ezdxf.entities.acad_table import read_acad_table_content
from ezdxf.tools.text import plain_mtext

from finishcalc.numbers import floor_key, normalize_room_number, parse_ru_float

TableKind = Literal["finish_schedule", "room_explication", "floor_explication",
                    "openings_spec", "other"]

# Признак вида таблицы в тексте первых двух строк (в нижнем регистре).
_KIND_MARKERS: tuple[tuple[str, TableKind], ...] = (
    ("ведомость отделки", "finish_schedule"),
    ("экспликация помещений", "room_explication"),
    ("экспликация полов", "floor_explication"),
    ("спецификация заполнения проем", "openings_spec"),
)
# Продолжение таблицы: та же координата Y с точностью до этой величины (ед. чертежа).
_CONTINUATION_Y_TOLERANCE = 1.0
# «Высота - ширина» в дециметрах, например «21 - 10» или «21,5 - 8».
_OPENING_SIZE = re.compile(r"(\d{2}(?:[.,]\d)?)\s*-\s*(\d{1,2}(?:[.,]\d)?)")
_DECIMETERS_PER_METER = 10


@dataclass
class RawTable:
    rows: list[list[str]]
    insert: tuple[float, float]


@dataclass
class ClassifiedTable:
    kind: TableKind
    title: str
    rows: list[list[str]]
    insert: tuple[float, float]


@dataclass
class ExplRow:
    number: str
    name: str
    area_m2: float | None
    floor: str | None


@dataclass
class FinishRow:
    number: str
    name: str
    ceiling: str
    walls: str
    floor: str
    floor_area_m2: float | None
    note: str


@dataclass
class FloorTypeRow:
    code: str
    rooms_label: str
    layers_text: str


def read_tables(doc) -> list[RawTable]:
    """Все ACAD_TABLE модели: текст ячеек без кодов форматирования, точка вставки."""
    tables: list[RawTable] = []
    for entity in doc.modelspace().query("ACAD_TABLE"):
        rows = [[_clean(plain_mtext(str(cell), split=False)) for cell in row]
                for row in read_acad_table_content(entity)]
        insert = entity.dxf.insert
        tables.append(RawTable(rows, (float(insert[0]), float(insert[1]))))
    return tables


def classify_tables(raw: list[RawTable]) -> list[ClassifiedTable]:
    """Вид и заголовок каждой таблицы; безымянная таблица с теми же колонками на той же
    высоте правее известной — её продолжение и наследует вид и заголовок."""
    first_pass = [_classify(table) for table in raw]
    result: list[ClassifiedTable] = []
    for table, own in zip(raw, first_pass):
        if own.kind == "other":
            parent = _left_neighbour(table, first_pass)
            if parent is not None:
                own = ClassifiedTable(parent.kind, parent.title, table.rows, table.insert)
        result.append(own)
    return result


def parse_room_explication(t: ClassifiedTable) -> list[ExplRow]:
    """Помещения экспликации; этаж берётся из заголовка таблицы."""
    floor = floor_key(t.title)
    rows: list[ExplRow] = []
    for row in t.rows:
        number, name, area = _cell(row, 0), _cell(row, 1), _cell(row, 2)
        if not name or _is_explication_service_row(number, name):
            continue
        rows.append(ExplRow(normalize_room_number(number), name, parse_ru_float(area), floor))
    return rows


def parse_finish_schedule(t: ClassifiedTable) -> list[FinishRow]:
    """Строки ведомости отделки: номер, название, отделка потолка, стен, пола, площадь пола."""
    rows: list[FinishRow] = []
    for row in t.rows:
        cells = [_cell(row, i) for i in range(9)]
        number = normalize_room_number(cells[0])
        if not number or not any(cells[1:]) or _is_finish_header(cells):
            continue
        rows.append(FinishRow(number, cells[1], cells[2], cells[4], cells[6],
                              parse_ru_float(cells[7]), cells[8]))
    return rows


def parse_floor_explication(t: ClassifiedTable) -> list[FloorTypeRow]:
    """Типы полов экспликации: код, подпись помещений, текст слоёв."""
    rows: list[FloorTypeRow] = []
    for row in t.rows:
        code = _cell(row, 1)
        if not code or "тип пола" in code.lower():
            continue
        rows.append(FloorTypeRow(code, _cell(row, 0), _cell(row, 3)))
    return rows


def parse_openings_spec(t: ClassifiedTable) -> dict[str, tuple[float, float]]:
    """Марка проёма → (ширина, высота) в метрах."""
    spec: dict[str, tuple[float, float]] = {}
    for row in t.rows:
        mark = normalize_room_number(_cell(row, 0))
        size = _OPENING_SIZE.search(" ".join(_cell(row, i) for i in range(2, 12)))
        if not mark or size is None:
            continue
        height_dm, width_dm = parse_ru_float(size.group(1)), parse_ru_float(size.group(2))
        if height_dm is None or width_dm is None:
            continue
        spec[mark] = (width_dm / _DECIMETERS_PER_METER, height_dm / _DECIMETERS_PER_METER)
    return spec


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _classify(table: RawTable) -> ClassifiedTable:
    head = " ".join(cell for row in table.rows[:2] for cell in row).lower()
    kind: TableKind = "other"
    for marker, marker_kind in _KIND_MARKERS:
        if marker in head:
            kind = marker_kind
            break
    title = next((cell for cell in table.rows[0] if cell), "") if table.rows else ""
    return ClassifiedTable(kind, title, table.rows, table.insert)


def _column_count(rows: list[list[str]]) -> int:
    return max((len(row) for row in rows), default=0)


def _left_neighbour(table: RawTable,
                    classified: list[ClassifiedTable]) -> ClassifiedTable | None:
    """Ближайшая слева опознанная таблица с тем же числом колонок и той же координатой Y."""
    columns = _column_count(table.rows)
    best: ClassifiedTable | None = None
    for other in classified:
        if (other.kind != "other"
                and other.insert[0] < table.insert[0]
                and abs(other.insert[1] - table.insert[1]) <= _CONTINUATION_Y_TOLERANCE
                and _column_count(other.rows) == columns
                and (best is None or other.insert[0] > best.insert[0])):
            best = other
    return best


def _is_explication_service_row(number: str, name: str) -> bool:
    """Шапка («Наименование») либо итог («Всего», «в т.ч.»)."""
    if "наименование" in name.lower():
        return True
    return any(cell.lower().startswith(("всего", "в т.ч.")) for cell in (number, name))


def _is_finish_header(cells: list[str]) -> bool:
    return any(cell.lower() == "потолок" or "наименование помещения" in cell.lower()
               for cell in cells)
