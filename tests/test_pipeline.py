import ezdxf
import pytest

from finishcalc import pipeline
from finishcalc.convert import ConvertError
from finishcalc.extract.tables import ClassifiedTable, RawTable
from finishcalc.llm import LLMError
from finishcalc.norms import load_norms
from finishcalc.pipeline import analyze, load_drawings, load_tables
from tests.fakes import FakeLLM
from tests.fixtures.synth import SYNTH_PROFILE, make_synthetic_plan

NORMS = load_norms()

# Таблицы в формате реальных, но выдуманные. Номера записаны и латиницей: их нормализует разбор.
EXPL = [["Экспликация помещений 1-го этажа", "", "", ""], ["№пом.", "Наименование", "Площадь м.кв.", "Кат. пом."],
        ["Корпус 1", "", "", ""], ["A01", "Холл", "20,0", ""], ["А02", "Кабинет", "20,0", ""],
        ["А03", "Склад", "18,3", ""], ["-", "Тамбур", "-", ""], ["Всего", "", "58,3", ""]]
FINISH = [["Ведомость отделки помещений (Корпус 1)"] + [""] * 8,
          ["№ п/п", "Наименование помещения", "Вид отделки элементов помещений", "", "", "", "", "", "Примечание"],
          ["", "", "Потолок", "Площадь м²", "Стены/перегородки", "Площадь м²", "Напольное покрытие", "Площадь м²", ""],
          ["A01", "Холл", "Подвесной потолок h=3,0м", "", "Штукатурка, окраска", "",
           "Керамогранит, завести на стену на 300мм.", "20,0", ""],
          ["А02", "Кабинет", "Окраска", "", "Штукатурка, окраска", "", "Керамогранит", "20,0", ""],
          ["-", "Коридор 3-9эт", "Окраска", "", "Окраска", "", "Стяжка", "-", ""],
          ["А07", "Кладовая", "Окраска", "", "Окраска", "", "Линолеум", "-", ""]]
FLOORS = [["Экспликация полов", "", "", "", ""],
          ["№пом.", "Тип пола", "Схема пола", "Данные элементов", "Площадь м.кв."],
          ["Кабинеты", "2", "", "1. Ламинат - 8мм | 2. Подложка - 3мм", "-"]]
SPEC = [["Спецификация заполнения проемов (начало - 1)"] + [""] * 12,
        ["Поз.", "Обозначение", "Наименование, В-Ш мм."] + [""] * 10,
        ["ДС-3", "ГОСТ 475-2016", "ДС", "1Рп", "21,5", "-", "10", "Г", "ПрБ", "", "", "", "1"]]

# Ответы поддельного ИИ: описание отделки -> позиции справочника. Текста строки А07 («Линолеум») здесь нет:
# запрос по нему упал бы с KeyError.
ITEMS = {
    "Подвесной потолок h=3,0м": ["suspended_ceiling"],
    "Окраска": ["primer", "paint_wd"],
    "Штукатурка, окраска": ["plaster_gypsum", "primer", "paint_wd"],
    "Керамогранит, завести на стену на 300мм.": ["porcelain_tile", "tile_adhesive"],
    "Керамогранит": ["porcelain_tile", "tile_adhesive"],
}
LAMINATE_LAYERS = {"layers": [
    {"material": "Ламинат", "thickness_mm_min": 8, "thickness_mm_max": 8, "count_as": "area", "norm_key": "laminate"},
    {"material": "Подложка", "thickness_mm_min": 3, "thickness_mm_max": 3, "count_as": "piece", "norm_key": "other"},
]}
SUGGESTED = {"ceiling": ["suspended_ceiling"], "walls": ["plaster_gypsum", "primer", "paint_wd"],
             "floor": ["porcelain_tile"]}


def ct(kind, rows):
    return ClassifiedTable(kind, rows[0][0], rows, (0.0, 0.0))


class RecordingLLM(FakeLLM):
    def __init__(self):
        self.seen = []
        super().__init__(self._answer)

    def _answer(self, system, user, schema, name):
        self.seen.append((name, user))
        if name == "finish_items":
            return {"items": ITEMS[user]}
        if name == "floor_layers":
            return LAMINATE_LAYERS
        if name == "suggest_finish":
            return SUGGESTED
        raise AssertionError(f"неожиданный вызов ИИ: {name}")


@pytest.fixture
def fake_llm():
    return RecordingLLM()


@pytest.fixture
def tables_a():
    return [ct("room_explication", EXPL), ct("finish_schedule", FINISH), ct("floor_explication", FLOORS),
            ct("openings_spec", SPEC)]


@pytest.fixture
def tables_no_schedule(tables_a):
    return [t for t in tables_a if t.kind != "finish_schedule"]


def codes_of(result):
    return [i.code for i in result.issues]


def test_analyze_mode_a(synth_doc, tables_a, fake_llm):
    r = analyze([synth_doc], tables_a, SYNTH_PROFILE, fake_llm, NORMS, {"1": 2.8})
    assert r.mode == "A"
    a01 = r.areas["А01"]
    assert a01.height_m == 3.0 and a01.ceiling_m2 == pytest.approx(20.0)
    # P×H − дверь ДС-3 − окно по умолчанию − заход: 54 − 2.15 − 2.25 − (18 − 1.0)×0.3
    assert a01.walls_m2 == pytest.approx(44.5)
    assert r.areas["А02"].height_m == 2.8
    codes = codes_of(r)
    for c in ["finish_row_without_contour", "room_without_finish_row", "floor_type_mismatch",
              "opening_without_mark", "opening_mark_not_in_spec", "reveals_not_counted"]:
        assert c in codes
    assert next(i for i in r.issues if i.code == "finish_row_without_contour").room == "А07"
    assert codes.count("reveals_not_counted") == 1
    assert {r_.number: r_.name for r_ in r.rooms}["А01"] == "Холл"


def test_analyze_mode_a_result_contents(synth_doc, tables_a, fake_llm):
    r = analyze([synth_doc], tables_a, SYNTH_PROFILE, fake_llm, NORMS, {"1": 2.8})
    assert [room.number for room in r.rooms] == ["А01", "А02", "А03"]
    assert {room.number: room.floor for room in r.rooms} == {"А01": "1", "А02": "1", "А03": "1"}
    assert list(r.finish_rows) == ["А01", "А02", "А07"]  # строка без номера («-») не попала
    assert list(r.floor_types) == ["2"]
    assert r.floor_type_areas == {"7": 20.0, "2": 20.0, "7*": 18.3}
    # Один проём ДС-3 лежит между А01 и А02 и попал в оба; окно — только в А01, дверь без марки — только в А02.
    openings = {room.number: {o.mark for o in room.openings} for room in r.rooms}
    assert openings == {"А01": {"ДС-3", "3"}, "А02": {"ДС-3", None}, "А03": set()}
    # 2.8 м: 18 × 2.8 − ДС-3 (1.0 × 2.15) − дверь по умолчанию (0.9 × 2.1)
    assert r.areas["А02"].walls_m2 == pytest.approx(46.36)
    laminate = next(m for m in r.materials if m.material == "Ламинат")
    assert (laminate.unit, laminate.quantity, laminate.basis, laminate.rooms) == ("м²", 21.0, "норма", ["А02"])
    assert not any(i.code == "finish_suggested_by_ai" for i in r.issues)


def test_analyze_mode_a_floor_type_issues(synth_doc, tables_a, fake_llm):
    r = analyze([synth_doc], tables_a, SYNTH_PROFILE, fake_llm, NORMS, {})
    by_code = {}
    for issue in r.issues:
        by_code.setdefault(issue.code, []).append(issue.room)
    assert by_code["floor_type_not_in_explication"] == ["А01", "А03"]  # типы 7 и 7* в экспликации нет
    assert by_code["floor_type_mismatch"] == ["А02"]  # ламинат по экспликации, керамогранит по ведомости
    assert by_code["room_without_finish_row"] == ["А03"]


def test_analyze_mode_a_calls_ai_once_per_matched_row(synth_doc, tables_a, fake_llm):
    analyze([synth_doc], tables_a, SYNTH_PROFILE, fake_llm, NORMS, {})
    texts = [user for name, user in fake_llm.seen if name == "finish_items"]
    assert "Линолеум" not in texts  # строка без контура ИИ не разбирается
    assert sorted(texts) == sorted([
        "Подвесной потолок h=3,0м", "Штукатурка, окраска", "Керамогранит, завести на стену на 300мм.",
        "Окраска", "Штукатурка, окраска", "Керамогранит"])
    assert [name for name, _ in fake_llm.seen].count("floor_layers") == 1


def test_analyze_mode_b(synth_doc, tables_no_schedule, fake_llm):
    r = analyze([synth_doc], tables_no_schedule, SYNTH_PROFILE, fake_llm, NORMS, {})
    assert r.mode == "B"
    assert sum(i.code == "finish_suggested_by_ai" for i in r.issues) == len(r.rooms)
    assert r.finish_rows == {}
    assert not any(i.code in ("room_without_finish_row", "finish_row_without_contour", "floor_type_mismatch")
                   for i in r.issues)
    assert all(room.finish is not None for room in r.rooms)
    assert r.areas["А01"].height_m == 3.0
    suggested_for = [user for name, user in fake_llm.seen if name == "suggest_finish"]
    assert sorted(suggested_for) == ["Кабинет", "Склад", "Холл"]


def test_room_outside_explication_gets_default_height(synth_doc, tables_a, fake_llm):
    tables = [ct("room_explication", [row for row in EXPL if row[0] != "А03"])] + tables_a[1:]
    r = analyze([synth_doc], tables, SYNTH_PROFILE, fake_llm, NORMS, {"1": 2.8}, default_height_m=2.5)
    room = next(room for room in r.rooms if room.number == "А03")
    assert room.floor is None and room.name == ""
    assert r.areas["А03"].height_m == 2.5
    assert r.areas["А02"].height_m == 2.8


def test_room_found_in_several_explications_takes_first(synth_doc, tables_a, fake_llm):
    other = [["Экспликация помещений 2-го этажа", "", "", ""], ["№пом.", "Наименование", "Площадь м.кв.", "Кат. пом."],
             ["А01", "Приёмная", "20,0", ""]]
    tables = [tables_a[0], ct("room_explication", other)] + tables_a[1:]
    r = analyze([synth_doc], tables, SYNTH_PROFILE, fake_llm, NORMS, {"1": 2.8, "2": 3.3})
    a01 = next(room for room in r.rooms if room.number == "А01")
    assert (a01.name, a01.floor) == ("Холл", "1")


def _plan_with_numbers(path, rooms):
    """Чертёж из прямоугольных контуров с номерками: rooms — [(номер, (x0, y0, x1, y1)), …], мм."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    doc.layers.add(SYNTH_PROFILE.room_contour_layer)
    doc.blocks.new(name=SYNTH_PROFILE.room_tag_block).add_attdef(
        SYNTH_PROFILE.room_tag_attr, insert=(0, 0), text="", height=250)
    msp = doc.modelspace()
    for number, (x0, y0, x1, y1) in rooms:
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                           dxfattribs={"layer": SYNTH_PROFILE.room_contour_layer})
        msp.add_blockref(SYNTH_PROFILE.room_tag_block, (x0 + 500, y0 + 500)).add_auto_attribs(
            {SYNTH_PROFILE.room_tag_attr: number})
    doc.saveas(path)
    return ezdxf.readfile(path)


def test_duplicate_room_number_keeps_first_contour(tmp_path, tables_a, fake_llm):
    doc = _plan_with_numbers(tmp_path / "dup.dxf", [("А01", (0, 0, 4000, 5000)), ("A01", (6000, 0, 9000, 3000)),
                                                    ("А02", (10000, 0, 14000, 5000))])
    r = analyze([doc], tables_a, SYNTH_PROFILE, fake_llm, NORMS, {})
    assert [(room.number, round(room.area_m2, 1)) for room in r.rooms] == [("А01", 20.0), ("А02", 20.0)]
    dups = [i for i in r.issues if i.code == "duplicate_room_number"]
    assert [i.room for i in dups] == ["А01"]
    assert "9.0" in dups[0].message  # по площади видно, какой контур пропущен
    assert sum(i.code == "room_without_finish_row" for i in r.issues) == 0


def test_same_number_on_two_plans_keeps_first(tmp_path, tables_a, fake_llm):
    first = _plan_with_numbers(tmp_path / "first.dxf", [("А01", (0, 0, 4000, 5000))])
    second = _plan_with_numbers(tmp_path / "second.dxf", [("А01", (0, 0, 2000, 2000)), ("А02", (5000, 0, 9000, 5000))])
    r = analyze([first, second], tables_a, SYNTH_PROFILE, fake_llm, NORMS, {})
    assert [(room.number, round(room.area_m2, 1)) for room in r.rooms] == [("А01", 20.0), ("А02", 20.0)]
    assert codes_of(r).count("duplicate_room_number") == 1


def test_floor_type_without_layers_text_skips_ai(synth_doc, tables_a, fake_llm):
    floors = [FLOORS[0], FLOORS[1], ["Кабинеты", "2", "", "", "-"]]
    tables = [t for t in tables_a if t.kind != "floor_explication"] + [ct("floor_explication", floors)]
    r = analyze([synth_doc], tables, SYNTH_PROFILE, fake_llm, NORMS, {})
    assert r.floor_types["2"].layers == []
    assert "floor_layers" not in [name for name, _ in fake_llm.seen]
    assert "floor_type_mismatch" not in codes_of(r)


def test_llm_error_propagates(synth_doc, tables_a):
    def broken(system, user, schema, name):
        raise LLMError("нет связи")

    with pytest.raises(LLMError):
        analyze([synth_doc], tables_a, SYNTH_PROFILE, FakeLLM(broken), NORMS, {})


def test_no_plans_gives_empty_result(tables_a, fake_llm):
    r = analyze([], tables_a, SYNTH_PROFILE, fake_llm, NORMS, {})
    assert r.rooms == [] and r.areas == {} and r.materials == []
    assert [i.code for i in r.issues].count("finish_row_without_contour") == 3  # все строки ведомости без контура
    assert r.mode == "A"


def test_load_drawings_reads_dxf(tmp_path):
    path = make_synthetic_plan(tmp_path / "plan.dxf")
    docs = load_drawings([path, path], tmp_path / "work")
    assert len(docs) == 2
    assert len(docs[0].modelspace().query("LWPOLYLINE")) > 0


def test_load_drawings_converts_dwg(tmp_path, monkeypatch):
    dxf = make_synthetic_plan(tmp_path / "converted.dxf")
    calls = []

    def fake_convert(src, out_dir):
        calls.append((src, out_dir))
        return dxf

    monkeypatch.setattr(pipeline, "dwg_to_dxf", fake_convert)
    docs = load_drawings([tmp_path / "plan.dwg"], tmp_path / "work")
    assert calls == [(tmp_path / "plan.dwg", tmp_path / "work")]
    assert len(docs) == 1


def test_load_drawings_propagates_convert_error(tmp_path, monkeypatch):
    def failing(src, out_dir):
        raise ConvertError("нет конвертера")

    monkeypatch.setattr(pipeline, "dwg_to_dxf", failing)
    with pytest.raises(ConvertError, match="нет конвертера"):
        load_drawings([tmp_path / "plan.dwg"], tmp_path)


def test_load_drawings_unreadable_dxf_is_convert_error(tmp_path):
    bad = tmp_path / "bad.dxf"
    bad.write_text("это не чертёж\n", encoding="utf-8")
    with pytest.raises(ConvertError, match="bad.dxf"):
        load_drawings([bad], tmp_path)


def test_load_tables_classifies_each_drawing_on_its_own(monkeypatch):
    docs = ["plan", "common"]
    legend = [["", "", "", ""], ["x", "y", "z", "w"]]  # те же 4 колонки, что у экспликации, и та же высота
    raw = {"plan": [RawTable(EXPL, (0.0, 0.0))],
           "common": [RawTable(legend, (5000.0, 0.0)), RawTable(FLOORS, (0.0, -9000.0))]}
    monkeypatch.setattr(pipeline, "read_tables", lambda doc: raw[doc])
    tables = load_tables(docs)
    assert [t.kind for t in tables] == ["room_explication", "other", "floor_explication"]
