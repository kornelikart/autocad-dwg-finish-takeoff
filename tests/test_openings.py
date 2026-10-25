import pytest

from finishcalc.extract.openings import extract_openings, lookup_size
from finishcalc.extract.rooms import extract_rooms
from tests.fixtures.synth import SYNTH_PROFILE

SPEC = {"ДС-3": (1.0, 2.15)}


def test_openings(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    ops, issues = extract_openings(synth_doc, SYNTH_PROFILE, rooms, SPEC)
    door = next(o for o in ops if o.mark == "ДС-3")
    assert (door.kind, door.width_m, door.height_m, door.sill_m, door.size_source) == ("door", 1.0, 2.15, 0.0, "spec")
    assert sorted(door.rooms) == ["А01", "А02"]
    win = next(o for o in ops if o.kind == "window")
    assert (win.mark, win.width_m, win.height_m, win.sill_m, win.rooms, win.size_source) == ("3", 1.5, 1.5, 0.9, ["А01"], "default")
    nomark = next(o for o in ops if o.mark is None)
    assert (nomark.kind, nomark.width_m, nomark.height_m, nomark.rooms) == ("door", 0.9, 2.1, ["А02"])
    assert sorted(i.code for i in issues) == ["opening_mark_not_in_spec", "opening_without_mark"]


def test_lookup_size_left_variant():
    assert lookup_size("ДС-3л", SPEC) == (1.0, 2.15)
    assert lookup_size("Д-9", SPEC) is None


def test_lookup_size_ignores_case_and_lookalike_letters():
    assert lookup_size("дс-3", SPEC) == (1.0, 2.15)
    assert lookup_size("ДC-3", SPEC) == (1.0, 2.15)  # латинская C вместо кириллической


def test_lookup_size_exact_mark_wins_over_left_variant():
    spec = {"ДС-3": (1.0, 2.15), "ДС-3Л": (0.9, 2.1)}
    assert lookup_size("ДС-3л", spec) == (0.9, 2.1)


def test_openings_geometry_and_center(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    ops, _ = extract_openings(synth_doc, SYNTH_PROFILE, rooms, SPEC)
    door = next(o for o in ops if o.mark == "ДС-3")
    win = next(o for o in ops if o.kind == "window")
    assert door.center == pytest.approx((3.65, 2.5))
    assert win.center == pytest.approx((-0.05, 2.25))


def test_window_without_mark_is_recognised_by_thin_bbox(synth_doc):
    for mtext in list(synth_doc.modelspace().query("MTEXT")):
        if mtext.dxf.layer == SYNTH_PROFILE.window_mark_layer:
            synth_doc.modelspace().delete_entity(mtext)
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    ops, issues = extract_openings(synth_doc, SYNTH_PROFILE, rooms, SPEC)
    win = next(o for o in ops if o.kind == "window")
    assert (win.mark, win.width_m, win.height_m, win.sill_m, win.size_source) == (None, 1.5, 1.5, 0.9, "default")
    assert sorted(i.code for i in issues) == ["opening_without_mark", "opening_without_mark"]
    assert all(i.room is None for i in issues)


def test_mark_matches_case_insensitively_and_keeps_original_text(synth_doc):
    for mtext in synth_doc.modelspace().query("MTEXT"):
        if mtext.dxf.layer == SYNTH_PROFILE.door_mark_layer:
            mtext.text = "дс-3Л"
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    ops, issues = extract_openings(synth_doc, SYNTH_PROFILE, rooms, SPEC)
    door = next(o for o in ops if o.mark == "дс-3Л")
    assert (door.width_m, door.height_m, door.size_source) == (1.0, 2.15, "spec")
    assert sorted(i.code for i in issues) == ["opening_mark_not_in_spec", "opening_without_mark"]
