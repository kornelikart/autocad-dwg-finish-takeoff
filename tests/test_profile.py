import ezdxf
import pytest
from dataclasses import asdict

from finishcalc.model import Profile
from finishcalc.profile import (
    ProfileError,
    detect_profile,
    load_profile,
    refine_profile,
    save_profile,
    summarize_drawing,
)
from tests.fakes import FakeLLM
from tests.fixtures.synth import SYNTH_PROFILE, make_synthetic_plan

RENAMED = {"_AREA": "L1", "номерок": "B1", "ААА": "N", "тип пола": "B2", "ТИП": "T"}
ROLE_FIELDS = [
    "room_contour_layer", "room_tag_block", "room_tag_attr", "floor_tag_block",
    "floor_tag_attr", "openings_layer", "door_mark_layer", "window_mark_layer",
]
SQUARE = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]


def _new_doc(insunits: int = 4):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = insunits
    return doc


def _add_tag_block(doc, block_name: str, tags: list[str]) -> None:
    block = doc.blocks.new(name=block_name)
    for tag in tags:
        block.add_attdef(tag, insert=(0, 0), text="", height=100)


def _insert_tag(msp, block_name: str, values: dict[str, str], at=(0, 0)) -> None:
    msp.add_blockref(block_name, at).add_auto_attribs(values)


def _add_marks(msp, layer: str, texts: list[str]) -> None:
    for i, text in enumerate(texts):
        msp.add_mtext(text, dxfattribs={"layer": layer, "insert": (i * 100, 0)})


def test_detect_profile_by_rules(synth_doc):
    found, unresolved = detect_profile(synth_doc)
    assert unresolved == []
    assert found == asdict(SYNTH_PROFILE)


def test_refine_with_llm(tmp_path):
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "r.dxf", RENAMED))
    found, unresolved = detect_profile(doc)
    assert {"room_contour_layer", "room_tag_block", "floor_tag_block"} <= set(unresolved)
    llm = FakeLLM({"profile": {"room_contour_layer": "L1", "room_tag_block": "B1", "room_tag_attr": "N",
                               "floor_tag_block": "B2", "floor_tag_attr": "T"}})
    p = refine_profile(summarize_drawing(doc), found, unresolved, llm)
    assert (p.room_contour_layer, p.room_tag_block, p.floor_tag_attr) == ("L1", "B1", "T")


def test_refine_rejects_unknown_names(tmp_path):
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "r.dxf", RENAMED))
    found, unresolved = detect_profile(doc)
    llm = FakeLLM({"profile": {k: "нет_такого" for k in unresolved}})
    with pytest.raises(ProfileError) as e:
        refine_profile(summarize_drawing(doc), found, unresolved, llm)
    assert "room_contour_layer" in e.value.fields


def test_summarize_synthetic_drawing(synth_doc):
    summary = summarize_drawing(synth_doc)
    assert summary["insunits"] == 4
    layers = {layer["name"]: layer for layer in summary["layers"]}
    assert layers["_AREA"]["types"] == {"LWPOLYLINE": 7}
    assert layers["_AREA"]["samples"] == []
    assert layers["_WinDoo"]["types"] == {"INSERT": 3}
    assert layers["_обозначения дверей"]["types"] == {"MTEXT": 1}
    assert layers["_обозначения дверей"]["samples"] == ["ДС-3"]
    blocks = {block["name"]: block for block in summary["blocks"]}
    assert blocks["номерок"]["count"] == 6
    assert blocks["номерок"]["attribs"] == ["ААА"]
    assert blocks["номерок"]["samples"] == ["А01", "А02", "А03", "А04", "А05"]
    assert blocks["тип пола"]["attribs"] == ["ТИП"]
    assert blocks["тип пола"]["samples"] == ["7", "2", "7*"]


def test_summarize_limits_samples_and_skips_anonymous_blocks():
    doc = _new_doc()
    msp = doc.modelspace()
    _add_marks(msp, "Marks", ["a", "b", "a", "c", "d", "e"])
    _add_tag_block(doc, "tag", ["V"])
    for i in range(7):
        _insert_tag(msp, "tag", {"V": f"v{i % 6}"})
    doc.blocks.new(name="*U7")
    msp.add_blockref("*U7", (0, 0))
    msp.add_text("подпись", dxfattribs={"layer": "Marks"})

    summary = summarize_drawing(doc)
    marks = next(layer for layer in summary["layers"] if layer["name"] == "Marks")
    assert marks["types"] == {"MTEXT": 6, "TEXT": 1}
    assert marks["samples"] == ["a", "b", "c"]
    assert [block["name"] for block in summary["blocks"]] == ["tag"]
    tag = summary["blocks"][0]
    assert tag["count"] == 7
    assert tag["samples"] == ["v0", "v1", "v2", "v3", "v4"]


def test_detect_ignores_case_and_yo():
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline(SQUARE, close=True, dxfattribs={"layer": "ПОМЕЩЕНИЯ"})
    _add_tag_block(doc, "Room_Tag", ["N"])
    _add_tag_block(doc, "Тип ПОЛА", ["T"])
    _add_tag_block(doc, "отметка", ["H"])
    _insert_tag(msp, "Room_Tag", {"N": "1"})
    _insert_tag(msp, "Тип ПОЛА", {"T": "2"})
    _insert_tag(msp, "отметка", {"H": "+3.000"})
    block = doc.blocks.new(name="leaf")
    block.add_line((0, 0), (900, 0))
    for _ in range(2):
        msp.add_blockref("leaf", (0, 0), dxfattribs={"layer": "Проёмы"})
    _add_marks(msp, "ДВЕРИ_МАРКИ", ["Д1"])
    _add_marks(msp, "Марки ПРОЁМОВ", ["1", "2", "3"])
    _add_marks(msp, "Марки окон", ["9"])

    found, unresolved = detect_profile(doc)
    assert unresolved == []
    assert found == {
        "room_contour_layer": "ПОМЕЩЕНИЯ",
        "room_tag_block": "Room_Tag",
        "room_tag_attr": "N",
        "floor_tag_block": "Тип ПОЛА",
        "floor_tag_attr": "T",
        "openings_layer": "Проёмы",
        "door_mark_layer": "ДВЕРИ_МАРКИ",
        "window_mark_layer": "Марки ПРОЁМОВ",
        "mm_per_unit": 1.0,
    }


def test_detect_leaves_tied_candidates_unresolved():
    doc = _new_doc()
    msp = doc.modelspace()
    for layer in ("area_1", "area_2"):
        msp.add_lwpolyline(SQUARE, close=True, dxfattribs={"layer": layer})
    block = doc.blocks.new(name="leaf")
    block.add_line((0, 0), (900, 0))
    for layer in ("win_a", "win_b"):
        msp.add_blockref("leaf", (0, 0), dxfattribs={"layer": layer})
    _add_tag_block(doc, "room_a", ["N"])
    _add_tag_block(doc, "room_b", ["N"])
    _add_tag_block(doc, "floor", ["T"])
    _add_tag_block(doc, "floor_2", ["T"])
    for name in ("room_a", "room_b", "floor", "floor_2"):
        _insert_tag(msp, name, {"N" if name.startswith("room") else "T": "x"})
    _add_marks(msp, "Марки окон", ["1", "2"])
    _add_marks(msp, "Марки проёмов", ["3", "4"])

    found, unresolved = detect_profile(doc)
    assert unresolved == [
        "room_contour_layer", "room_tag_block", "room_tag_attr", "floor_tag_block",
        "floor_tag_attr", "openings_layer", "door_mark_layer", "window_mark_layer",
    ]
    assert found == {"mm_per_unit": 1.0}


def test_detect_requires_closed_polylines_and_single_attribute():
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline(SQUARE, close=False, dxfattribs={"layer": "area_open"})
    _add_marks(msp, "area_text", ["12 м2"])
    _add_tag_block(doc, "room_two", ["N", "M"])
    _insert_tag(msp, "room_two", {"N": "1", "M": "2"})

    found, unresolved = detect_profile(doc)
    assert "room_contour_layer" in unresolved
    assert "room_tag_block" in unresolved
    assert "room_tag_attr" in unresolved


def test_detect_window_marks_exclude_door_layer():
    doc = _new_doc()
    msp = doc.modelspace()
    _add_marks(msp, "двери и окон", ["Д1", "Д2", "Д3"])

    found, unresolved = detect_profile(doc)
    assert found["door_mark_layer"] == "двери и окон"
    assert "window_mark_layer" in unresolved


@pytest.mark.parametrize("insunits, expected", [(4, 1.0), (5, 10.0), (6, 1000.0), (0, 1.0), (1, 1.0)])
def test_detect_mm_per_unit(insunits, expected):
    found, _ = detect_profile(_new_doc(insunits))
    assert found["mm_per_unit"] == expected


def test_refine_asks_only_about_unresolved_fields(tmp_path):
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "r.dxf", RENAMED))
    summary = summarize_drawing(doc)
    found, unresolved = detect_profile(doc)
    seen = {}

    def answer(system, user, schema, name):
        seen.update(name=name, schema=schema, user=user)
        return {"room_contour_layer": "L1", "room_tag_block": "B1", "room_tag_attr": "N",
                "floor_tag_block": "B2", "floor_tag_attr": "T"}

    llm = FakeLLM(answer)
    p = refine_profile(summary, found, unresolved, llm)

    assert llm.calls == 1
    assert seen["name"] == "profile"
    schema = seen["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(unresolved) == set(schema["required"])
    props = schema["properties"]
    assert props["room_contour_layer"]["enum"] == [*[layer["name"] for layer in summary["layers"]], "none"]
    assert props["room_tag_block"]["enum"] == ["B1", "B2", "none"]
    assert props["room_tag_attr"]["enum"] == ["N", "T", "none"]
    assert p == Profile(
        room_contour_layer="L1", room_tag_block="B1", room_tag_attr="N",
        floor_tag_block="B2", floor_tag_attr="T",
        openings_layer=SYNTH_PROFILE.openings_layer,
        door_mark_layer=SYNTH_PROFILE.door_mark_layer,
        window_mark_layer=SYNTH_PROFILE.window_mark_layer,
        mm_per_unit=1.0,
    )


def test_refine_without_unresolved_does_not_call_llm(synth_doc):
    found, unresolved = detect_profile(synth_doc)
    llm = FakeLLM({})
    assert refine_profile(summarize_drawing(synth_doc), found, unresolved, llm) == SYNTH_PROFILE
    assert llm.calls == 0


def test_refine_none_allowed_only_for_optional_fields():
    doc = _new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline(SQUARE, close=True, dxfattribs={"layer": "plan_rooms"})
    _add_tag_block(doc, "room_tag", ["N"])
    _add_tag_block(doc, "floor_tag", ["T"])
    _insert_tag(msp, "room_tag", {"N": "1"})
    _insert_tag(msp, "floor_tag", {"T": "2"})
    block = doc.blocks.new(name="leaf")
    block.add_line((0, 0), (900, 0))
    msp.add_blockref("leaf", (0, 0), dxfattribs={"layer": "plan_openings"})
    summary = summarize_drawing(doc)
    found, unresolved = detect_profile(doc)
    assert unresolved == ["room_contour_layer", "openings_layer", "door_mark_layer", "window_mark_layer"]

    ok = FakeLLM({"profile": {"room_contour_layer": "plan_rooms", "openings_layer": "plan_openings",
                              "door_mark_layer": "none", "window_mark_layer": "none"}})
    p = refine_profile(summary, found, unresolved, ok)
    assert (p.room_contour_layer, p.openings_layer) == ("plan_rooms", "plan_openings")
    assert p.door_mark_layer is None and p.window_mark_layer is None

    bad = FakeLLM({"profile": {"room_contour_layer": "none", "openings_layer": "plan_openings",
                               "door_mark_layer": "none", "window_mark_layer": "nowhere"}})
    with pytest.raises(ProfileError) as e:
        refine_profile(summary, found, unresolved, bad)
    assert e.value.fields == ["room_contour_layer", "window_mark_layer"]
    assert "room_contour_layer" in str(e.value)


def test_refine_rejects_attribute_of_another_block(tmp_path):
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "r.dxf", RENAMED))
    found, unresolved = detect_profile(doc)
    llm = FakeLLM({"profile": {"room_contour_layer": "L1", "room_tag_block": "B1", "room_tag_attr": "T",
                               "floor_tag_block": "B2", "floor_tag_attr": "T"}})
    with pytest.raises(ProfileError) as e:
        refine_profile(summarize_drawing(doc), found, unresolved, llm)
    assert e.value.fields == ["room_tag_attr"]


def test_refine_rejects_missing_answer(tmp_path):
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "r.dxf", RENAMED))
    found, unresolved = detect_profile(doc)
    llm = FakeLLM({"profile": {"room_contour_layer": "L1"}})
    with pytest.raises(ProfileError) as e:
        refine_profile(summarize_drawing(doc), found, unresolved, llm)
    assert e.value.fields == ["room_tag_block", "room_tag_attr", "floor_tag_block", "floor_tag_attr"]


def test_save_and_load_profile(tmp_path):
    path = tmp_path / "project" / "profile.json"
    profile = Profile(
        room_contour_layer="Помещения", room_tag_block="номерок", room_tag_attr="ААА",
        floor_tag_block="тип пола", floor_tag_attr="ТИП", openings_layer="Проёмы",
        door_mark_layer=None, window_mark_layer="Марки", mm_per_unit=10.0,
    )
    save_profile(profile, path)
    assert "Помещения" in path.read_text(encoding="utf-8")
    assert load_profile(path) == profile
