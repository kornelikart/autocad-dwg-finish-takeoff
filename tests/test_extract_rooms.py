import math
from dataclasses import replace

import ezdxf
import pytest

from finishcalc.extract.rooms import extract_rooms
from tests.fixtures.synth import SYNTH_PROFILE, make_synthetic_plan


def test_rooms_found(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    by = {r.number: r for r in rooms}
    assert set(by) == {"А01", "А02", "А03"}
    assert by["А01"].area_m2 == pytest.approx(20.0, abs=0.01)
    assert by["А01"].perimeter_m == pytest.approx(18.0, abs=0.01)
    assert by["А01"].floor_type == "7"
    assert by["А02"].floor_type == "2"
    assert by["А03"].floor_type == "7*"


def test_bulge_room_area(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    a03 = next(r for r in rooms if r.number == "А03")
    assert a03.area_m2 == pytest.approx(12 + math.pi * 2**2 / 2, rel=0.005)


def _nested_plan_doc():
    """Помещение 5×4 м с номером, который случайно попал во вложенный безымянный контур 1×1 м
    (колонна, шахта, зона внутри помещения); тип пола стоит там же."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    for block, attr in [(SYNTH_PROFILE.room_tag_block, SYNTH_PROFILE.room_tag_attr),
                        (SYNTH_PROFILE.floor_tag_block, SYNTH_PROFILE.floor_tag_attr)]:
        doc.blocks.new(block).add_attdef(attr, (0, 0))
    msp = doc.modelspace()
    layer = {"layer": SYNTH_PROFILE.room_contour_layer}
    msp.add_lwpolyline([(0, 0), (5000, 0), (5000, 4000), (0, 4000)], close=True, dxfattribs=layer)
    msp.add_lwpolyline([(500, 500), (1500, 500), (1500, 1500), (500, 1500)], close=True, dxfattribs=layer)
    msp.add_blockref(SYNTH_PROFILE.room_tag_block, (1000, 1000)).add_auto_attribs(
        {SYNTH_PROFILE.room_tag_attr: "Б01"})
    msp.add_blockref(SYNTH_PROFILE.floor_tag_block, (1200, 1200)).add_auto_attribs(
        {SYNTH_PROFILE.floor_tag_attr: "4"})
    return doc


def test_number_in_nested_untagged_contour_belongs_to_outer_room():
    rooms, issues = extract_rooms(_nested_plan_doc(), SYNTH_PROFILE)
    assert [(r.number, round(r.area_m2, 2), r.floor_type) for r in rooms] == [("Б01", 20.0, "4")]
    assert [i.code for i in issues] == ["contour_without_number"]


def test_duplicate_contour_counted_once(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    assert [r.number for r in rooms].count("А01") == 1


def test_issues(synth_doc):
    _, issues = extract_rooms(synth_doc, SYNTH_PROFILE)
    codes = sorted(i.code for i in issues)
    assert codes == ["contour_with_several_numbers", "contour_without_number",
                     "contour_without_number", "number_without_contour"]
    assert next(i for i in issues if i.code == "number_without_contour").room == "А99"


def test_room_geometry_in_metres_and_defaults(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    a02 = next(r for r in rooms if r.number == "А02")
    assert a02.polygon.bounds == pytest.approx((4.2, 0.0, 8.2, 5.0))
    assert a02.name == ""
    assert a02.floor is None


def test_renamed_layers_blocks_and_tags(tmp_path):
    names = {
        "_AREA": "ROOMS",
        "номерок": "room_tag",
        "ААА": "NUM",
        "тип пола": "floor_tag",
        "ТИП": "KIND",
    }
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "renamed.dxf", names))
    profile = replace(
        SYNTH_PROFILE,
        room_contour_layer="ROOMS",
        room_tag_block="room_tag",
        room_tag_attr="NUM",
        floor_tag_block="floor_tag",
        floor_tag_attr="KIND",
    )
    rooms, _ = extract_rooms(doc, profile)
    assert {r.number: r.floor_type for r in rooms} == {"А01": "7", "А02": "2", "А03": "7*"}
    # под прежним профилем в переименованном чертеже ничего не находится
    assert extract_rooms(doc, SYNTH_PROFILE)[0] == []


def test_scale_follows_mm_per_unit(synth_doc):
    # тот же чертёж, прочитанный как сантиметры: все размеры в 10 раз больше
    rooms, _ = extract_rooms(synth_doc, replace(SYNTH_PROFILE, mm_per_unit=10.0))
    a01 = next(r for r in rooms if r.number == "А01")
    assert a01.area_m2 == pytest.approx(2000.0, abs=0.1)
