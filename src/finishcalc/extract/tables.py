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
    ("ведомость проем", "openings_spec"),
)
# Продолжение таблицы: та же координата (Y — правее, X — ниже) с точностью до этой величины (ед. чертежа).
_CONTINUATION_TOLERANCE = 1.0
# «Высота - ширина»: в дециметрах («21,5 - 10», ГОСТ 475) или в миллиметрах («2150 - 900»).
_OPENING_SIZE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)(?![\d.,])")
_SIZE_CELLS = range(1, 12)
_MM_FROM = 100  # число от 100 — миллиметры, меньше — дециметры
_HEIGHT_RANGE_M = (1.0, 4.5)  # ниже метра — скорее масса или количество, чем проём
_WIDTH_RANGE_M = (0.3, 8.0)


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
    """Вид и заголовок каждой таблицы. Безымянная таблица — продолжение известной и
    наследует её вид и заголовок, если стоит правее на той же высоте с теми же колонками
    или ниже в той же колонке листа с почти теми же колонками (±1)."""
    first_pass = [_classify(table) for table in raw]
    result: list[ClassifiedTable] = []
    for table, own in zip(raw, first_pass):
        if own.kind == "other":
            parent = _left_neighbour(table, first_pass) or _upper_neighbour(table, first_pass)
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
    """Марка проёма → (ширина, высота) в метрах. Строка без правдоподобного размера
    (номер ГОСТ, «см. эскиз», шапка) пропускается."""
    spec: dict[str, tuple[float, float]] = {}
    for row in t.rows:
        mark = normalize_room_number(_cell(row, 0))
        size = _opening_size(" ".join(_cell(row, i) for i in _SIZE_CELLS))
        if mark and size is not None:
            spec[mark] = size
    return spec


def _opening_size(text: str) -> tuple[float, float] | None:
    """Первая пара «высота - ширина» с правдоподобными размерами, как (ширина, высота) в метрах."""
    for match in _OPENING_SIZE.finditer(text):
        height, width = _to_metres(match.group(1)), _to_metres(match.group(2))
        if (height is not None and width is not None
                and _HEIGHT_RANGE_M[0] <= height <= _HEIGHT_RANGE_M[1]
                and _WIDTH_RANGE_M[0] <= width <= _WIDTH_RANGE_M[1]):
            return width, height
    return None


def _to_metres(value: str) -> float | None:
    number = parse_ru_float(value)
    if number is None:
        return None
    return round(number / 1000 if number >= _MM_FROM else number / 10, 3)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _classify(table: RawTable) -> ClassifiedTable:
    head = " ".join(cell for row in table.rows[:2] for cell in row).lower().replace("ё", "е")
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
                and abs(other.insert[1] - table.insert[1]) <= _CONTINUATION_TOLERANCE
                and _column_count(other.rows) == columns
                and (best is None or other.insert[0] > best.insert[0])):
            best = other
    return best


def _upper_neighbour(table: RawTable,
                     classified: list[ClassifiedTable]) -> ClassifiedTable | None:
    """Ближайшая сверху опознанная таблица в той же колонке листа (та же координата X),
    число колонок отличается не больше чем на одну."""
    columns = _column_count(table.rows)
    best: ClassifiedTable | None = None
    for other in classified:
        if (other.kind != "other"
                and other.insert[1] > table.insert[1]
                and abs(other.insert[0] - table.insert[0]) <= _CONTINUATION_TOLERANCE
                and abs(_column_count(other.rows) - columns) <= 1
                and (best is None or other.insert[1] < best.insert[1])):
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
