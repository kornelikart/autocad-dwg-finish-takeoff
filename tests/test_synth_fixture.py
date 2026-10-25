"""Проверка самого синтетического чертежа: на него опираются тесты проёмов
и определения профиля."""

import ezdxf

from tests.fixtures.synth import SYNTH_PROFILE, make_synthetic_plan


def test_openings_and_marks_present(synth_doc):
    msp = synth_doc.modelspace()
    on_openings = [e for e in msp.query("INSERT") if e.dxf.layer == SYNTH_PROFILE.openings_layer]
    assert sorted(e.dxf.name for e in on_openings) == ["door_test", "door_test", "win_test"]
    assert all(e.dxf.rotation == 90 for e in on_openings)
    marks = {
        e.dxf.layer: e.text
        for e in msp.query("MTEXT")
    }
    assert marks == {
        SYNTH_PROFILE.door_mark_layer: "ДС-3",
        SYNTH_PROFILE.window_mark_layer: "3",
    }
    assert synth_doc.header["$INSUNITS"] == 4


def test_names_rename_layers_blocks_and_attribute_tags(tmp_path):
    names = {
        "_AREA": "L_ROOMS",
        "номерок": "B_ROOM",
        "ААА": "T_NUM",
        "тип пола": "B_FLOOR",
        "ТИП": "T_KIND",
        "_WinDoo": "L_OPEN",
        "_обозначения дверей": "L_DMARK",
        "_обозначения проемов": "L_WMARK",
        "door_test": "B_DOOR",
        "win_test": "B_WIN",
    }
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "renamed.dxf", names))
    msp = doc.modelspace()

    layers = {e.dxf.layer for e in msp}
    assert layers >= {"L_ROOMS", "L_OPEN", "L_DMARK", "L_WMARK"}
    assert not layers & {"_AREA", "_WinDoo", "_обозначения дверей", "_обозначения проемов"}

    insert_names = {e.dxf.name for e in msp.query("INSERT")}
    assert insert_names == {"B_ROOM", "B_FLOOR", "B_DOOR", "B_WIN"}
    tags = {a.dxf.tag for e in msp.query("INSERT") for a in e.attribs}
    assert tags == {"T_NUM", "T_KIND"}
    assert {b.name for b in doc.blocks} >= {"B_ROOM", "B_FLOOR", "B_DOOR", "B_WIN"}
