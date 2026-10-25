"""Синтетический чертёж для тестов: выдуманные помещения, номерки, типы пола,
двери и окна. Координаты в мм ($INSUNITS = 4)."""

from pathlib import Path

import ezdxf
from ezdxf.document import Drawing
from ezdxf.layouts import Modelspace

from finishcalc.model import Profile

SYNTH_PROFILE = Profile(
    room_contour_layer="_AREA",
    room_tag_block="номерок",
    room_tag_attr="ААА",
    floor_tag_block="тип пола",
    floor_tag_attr="ТИП",
    openings_layer="_WinDoo",
    door_mark_layer="_обозначения дверей",
    window_mark_layer="_обозначения проемов",
    mm_per_unit=1.0,
)

DOOR_BLOCK = "door_test"
WINDOW_BLOCK = "win_test"
_MARK_HEIGHT_MM = 200.0
_TAG_HEIGHT_MM = 250.0

_ROOM_TAGS = [
    ("А01", (2000, 2500)),
    ("А02", (6200, 2500)),
    ("А03", (2000, 7500)),
    ("А04", (10000, 7500)),
    ("А05", (11000, 7500)),
    ("А99", (20000, 20000)),
]
_FLOOR_TAGS = [
    ("7", (1000, 1000)),
    ("2", (5000, 1000)),
    ("7*", (1000, 7000)),
]


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _add_tag_block(doc: Drawing, block_name: str, attr_tag: str) -> None:
    block = doc.blocks.new(name=block_name)
    block.add_attdef(attr_tag, insert=(0, 0), text="", height=_TAG_HEIGHT_MM)


def _add_door_block(doc: Drawing, block_name: str) -> None:
    block = doc.blocks.new(name=block_name)
    block.add_line((0, 0), (900, 0))
    block.add_arc((0, 0), radius=900, start_angle=0, end_angle=90)


def _add_window_block(doc: Drawing, block_name: str) -> None:
    block = doc.blocks.new(name=block_name)
    block.add_lwpolyline(_rect(0, 0, 1500, 100), close=True)


def _add_mark(msp: Modelspace, text: str, layer: str, at: tuple[float, float]) -> None:
    msp.add_mtext(
        text,
        dxfattribs={"layer": layer, "insert": at, "char_height": _MARK_HEIGHT_MM},
    )


def _add_contours(msp: Modelspace, layer: str) -> None:
    attribs = {"layer": layer}
    for corners in (
        _rect(0, 0, 4000, 5000),  # А01
        _rect(0, 0, 4000, 5000),  # дубль А01
        _rect(4200, 0, 8200, 5000),  # А02
        _rect(9000, 6000, 12000, 9000),  # слитный контур с номерами А04 и А05
        _rect(9000, 0, 10000, 1000),  # шахта без номера
        _rect(-500, -500, 8700, 5500),  # контур этажа без номера
    ):
        msp.add_lwpolyline(corners, close=True, dxfattribs=attribs)
    # А03: прямоугольник 4 x 3 м с полукругом r = 2 м наружу сверху;
    # bulge стоит на вершине (4000, 9000) и задаёт дугу к (0, 9000).
    msp.add_lwpolyline(
        [(0, 6000, 0), (4000, 6000, 0), (4000, 9000, 1), (0, 9000, 0)],
        format="xyb",
        close=True,
        dxfattribs=attribs,
    )


def make_synthetic_plan(path: Path, names: dict[str, str] | None = None) -> Path:
    """Сохранить синтетический чертёж в `path` и вернуть `path`.

    `names` переименовывает слои, блоки и теги атрибутов: ключ — исходное имя
    (значение из SYNTH_PROFILE или имя блока двери/окна), значение — новое.
    Так тесты проверяют определение профиля при другом именовании.
    """
    renames = names or {}

    def n(name: str) -> str:
        return renames.get(name, name)

    contour_layer = n(SYNTH_PROFILE.room_contour_layer)
    room_tag_block = n(SYNTH_PROFILE.room_tag_block)
    room_tag_attr = n(SYNTH_PROFILE.room_tag_attr)
    floor_tag_block = n(SYNTH_PROFILE.floor_tag_block)
    floor_tag_attr = n(SYNTH_PROFILE.floor_tag_attr)
    openings_layer = n(SYNTH_PROFILE.openings_layer)
    door_mark_layer = n(SYNTH_PROFILE.door_mark_layer)
    window_mark_layer = n(SYNTH_PROFILE.window_mark_layer)
    door_block = n(DOOR_BLOCK)
    window_block = n(WINDOW_BLOCK)

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    for layer in (contour_layer, openings_layer, door_mark_layer, window_mark_layer):
        if layer not in doc.layers:
            doc.layers.add(layer)

    _add_tag_block(doc, room_tag_block, room_tag_attr)
    _add_tag_block(doc, floor_tag_block, floor_tag_attr)
    _add_door_block(doc, door_block)
    _add_window_block(doc, window_block)

    msp = doc.modelspace()
    _add_contours(msp, contour_layer)

    for number, at in _ROOM_TAGS:
        msp.add_blockref(room_tag_block, at).add_auto_attribs({room_tag_attr: number})
    for floor_type, at in _FLOOR_TAGS:
        msp.add_blockref(floor_tag_block, at).add_auto_attribs(
            {floor_tag_attr: floor_type}
        )

    opening_attribs = {"layer": openings_layer, "rotation": 90}
    msp.add_blockref(door_block, (4100, 2050), dxfattribs=opening_attribs)
    _add_mark(msp, "ДС-3", door_mark_layer, (4100, 3100))
    msp.add_blockref(window_block, (0, 1500), dxfattribs=opening_attribs)
    _add_mark(msp, "3", window_mark_layer, (-600, 2250))
    msp.add_blockref(door_block, (8200, 3000), dxfattribs=opening_attribs)  # без марки

    doc.saveas(path)
    return path
