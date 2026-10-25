import dataclasses

import pytest

from finishcalc.model import Issue
from finishcalc.pipeline import analyze
from scripts.acceptance import _height, check, run, schedule_scope
from tests.fakes import FakeLLM
from tests.fixtures.synth import SYNTH_PROFILE, make_synthetic_plan
from tests.test_pipeline import NORMS, SUGGESTED, RecordingLLM, ct, EXPL, FINISH


def test_floor_area_agreeing_with_schedule_passes(small_result):
    report = check(small_result, {"Т1"})
    assert (report.matched, report.compared, report.agreed, report.computed) == (1, 1, 1, 1)
    assert report.passed


def test_floor_area_off_by_more_than_tolerance_fails(small_result):
    row = small_result.finish_rows["Т1"]
    small_result.finish_rows["Т1"] = dataclasses.replace(row, floor_area_m2=20.2)
    report = check(small_result, {"Т1"})
    assert report.mismatches == [("Т1", 20.0, 20.2)]
    assert not report.passed


def test_schedule_room_without_areas_needs_a_warning(small_result):
    small_result.finish_rows["Т2"] = dataclasses.replace(small_result.finish_rows["Т1"], number="Т2")
    assert check(small_result, {"Т1", "Т2"}).missing == ["Т2"]
    small_result.issues.append(Issue("finish_row_without_contour", "Т2", "нет на плане"))
    report = check(small_result, {"Т1", "Т2"})
    assert (report.warned, report.missing) == (["Т2"], [])
    assert report.passed


def test_scope_is_schedule_rooms_of_loaded_floors(synth_doc):
    tables = [ct("room_explication", EXPL), ct("finish_schedule", FINISH)]
    result = analyze([synth_doc], tables, SYNTH_PROFILE, RecordingLLM(), NORMS, {})
    # А07 есть в ведомости, но не в экспликации 1-го этажа: это другой этаж, его планы не загружены.
    assert schedule_scope(result, tables) == {"А01", "А02"}
    report = check(result, schedule_scope(result, tables))
    assert (report.compared, report.agreed) == (2, 2)
    assert report.passed


def test_run_without_schedule_saves_excel_and_fails(tmp_path, capsys):
    make_synthetic_plan(tmp_path / "ПЛАН_синтетический.dxf")
    code = run(tmp_path, {}, 3.0, FakeLLM({"suggest_finish": SUGGESTED}))
    assert code == 1
    assert (tmp_path / "finishcalc.xlsx").is_file()
    out = capsys.readouterr().out
    assert "ведомости нет" in out
    assert "ПРИЁМКА НЕ ПРОЙДЕНА" in out


def test_run_without_plans_fails(tmp_path, capsys):
    assert run(tmp_path, {}, 3.0, FakeLLM({})) == 1
    assert "нет планов" in capsys.readouterr().out


def test_height_argument():
    assert _height("подвал=2,7") == ("подвал", 2.7)
    assert _height(" 1 = 3.3") == ("1", 3.3)
    with pytest.raises(Exception, match="ЭТАЖ=М"):
        _height("3.3")
