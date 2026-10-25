import pytest
from shapely.geometry import box
from finishcalc.model import Room, Opening, FinishSpec, Zone, FloorType, FloorLayer
from finishcalc.calc import room_areas, aggregate_materials, floor_type_areas, overlap
from finishcalc.finishes import UNSPECIFIED
from finishcalc.norms import load_norms

def make_room(finish=None, floor_type="7"):
    door = Opening("door", "ДС-2", 0.9, 2.1, 0.0, ["Т1"], "spec", (0, 0))
    win = Opening("window", "3", 1.5, 1.5, 0.9, ["Т1"], "default", (0, 0))
    return Room("Т1", "Тест", "1", box(0, 0, 4, 5), 20.0, 18.0, floor_type, finish, [door, win])

FINISH = FinishSpec(["suspended_ceiling"], None,
    [Zone(0.0, 1.8, ["porcelain_tile", "tile_adhesive"], "плитка"), Zone(1.8, 2.1, [], UNSPECIFIED),
     Zone(2.1, None, ["primer", "paint_wd"], "окраска")], ["porcelain_tile", "tile_adhesive"], 0.3, {})

def test_room_areas_worked_example():
    a = room_areas(make_room(FINISH), default_height_m=3.0)
    assert (a.floor_m2, a.ceiling_m2, a.height_m) == (20.0, 20.0, 3.0)
    assert a.upturn_m2 == pytest.approx(5.13)
    assert [round(z.area_m2, 2) for z in a.zones] == [24.30, 4.68, 15.75]
    assert a.zones[1].unspecified is True
    assert a.walls_m2 == pytest.approx(44.73)

def test_ceiling_height_overrides_default():
    f = FinishSpec([], 2.5, [Zone(0.0, None, [], "x")], [], 0.0, {})
    assert room_areas(make_room(f), default_height_m=3.0).height_m == 2.5

def test_room_without_finish_single_zone():
    a = room_areas(make_room(None), default_height_m=3.0)
    assert len(a.zones) == 1 and a.walls_m2 == pytest.approx(54 - 1.89 - 2.25)

def test_materials():
    room = make_room(FINISH)
    areas = {"Т1": room_areas(room, 3.0)}
    ft = {"7": FloorType("7", "", [FloorLayer("Керамогранит", 20, 20, "area", "porcelain_tile"),
                                   FloorLayer("Стяжка", 50, 50, "volume", "screed")], "")}
    lines, issues = aggregate_materials([room], areas, ft, load_norms())
    q = {l.material: (l.unit, l.quantity) for l in lines}
    assert q["Подвесной потолок"] == ("м²", 21.0)
    assert q["Керамогранит"] == ("м²", 54.4)
    assert q["Клей плиточный"] == ("кг", 121.5)
    assert q["Грунтовка"] == ("л", 2.4)
    assert q["Краска водно-дисперсионная"] == ("л", 4.7)
    assert q["Стяжка"] == ("м³", 1.0)
    assert [i.code for i in issues] == ["unspecified_wall_band"]

def test_floor_layer_without_thickness():
    room = make_room(FINISH)
    ft = {"7": FloorType("7", "", [FloorLayer("Стяжка", None, None, "volume", "screed")], "")}
    _, issues = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, ft, load_norms())
    assert "floor_layer_without_thickness" in [i.code for i in issues]

def test_floor_type_areas():
    room = make_room(FINISH)
    assert floor_type_areas([room], {"Т1": room_areas(room, 3.0)}) == {"7": 20.0}


# --- дополнительные случаи ---

NORMS = load_norms()


def lines_by_material(lines):
    return {l.material: l for l in lines}


def test_overlap():
    assert overlap(0.0, 2.1, 0.0, 1.8) == pytest.approx(1.8)
    assert overlap(0.9, 2.4, 2.1, 3.0) == pytest.approx(0.3)
    assert overlap(0.0, 1.0, 2.0, 3.0) == 0.0


def test_upturn_subtracts_widths_of_the_rooms_own_doors():
    wide = Opening("door", "ДС-9", 1.2, 2.1, 0.0, ["Т2"], "spec", (9, 9))
    room = Room("Т2", "Другое", "1", box(10, 10, 14, 15), 20.0, 18.0, "7", FINISH, [wide])
    assert room_areas(room, 3.0).upturn_m2 == pytest.approx((18 - 1.2) * 0.3)


def test_window_does_not_reduce_upturn():
    win_only = make_room(FINISH)
    win_only.openings = [o for o in win_only.openings if o.kind == "window"]
    assert room_areas(win_only, 3.0).upturn_m2 == pytest.approx(18 * 0.3)


def test_no_upturn_when_not_requested():
    f = FinishSpec([], None, [Zone(0.0, None, ["primer"], "x")], [], 0.0, {})
    a = room_areas(make_room(f), 3.0)
    assert a.upturn_m2 == 0.0
    assert a.zones[0].area_m2 == pytest.approx(54 - 1.89 - 2.25)


def test_repeated_keys_counted_once():
    f = FinishSpec(["suspended_ceiling", "suspended_ceiling"], None,
                   [Zone(0.0, None, ["primer", "primer", "paint_wd"], "окраска")], [], 0.0, {})
    room = make_room(f, floor_type=None)
    lines, _ = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, {}, NORMS)
    q = lines_by_material(lines)
    assert q["Подвесной потолок"].quantity == 21.0
    assert q["Грунтовка"].quantity == 7.5


def test_floor_layer_with_one_thickness_uses_it_for_both():
    room = make_room(FINISH)
    ft = {"7": FloorType("7", "", [FloorLayer("Стяжка", 40, None, "volume", "screed")], "")}
    lines, issues = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, ft, NORMS)
    assert lines_by_material(lines)["Стяжка"].quantity == 0.8  # 20 м² × 40 мм
    assert "floor_layer_without_thickness" not in [i.code for i in issues]


def test_floor_layer_thickness_range_uses_mean():
    room = make_room(FINISH)
    ft = {"7": FloorType("7", "", [FloorLayer("Стяжка", 40, 60, "volume", "screed")], "")}
    lines, _ = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, ft, NORMS)
    assert lines_by_material(lines)["Стяжка"].quantity == 1.0


def test_layer_without_thickness_is_skipped():
    room = make_room(FINISH)
    ft = {"7": FloorType("7", "", [FloorLayer("Стяжка", None, None, "volume", "screed")], "")}
    lines, _ = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, ft, NORMS)
    assert "Стяжка" not in lines_by_material(lines)


def test_piece_layer_is_skipped():
    room = make_room(FINISH)
    ft = {"7": FloorType("7", "", [FloorLayer("Плинтус", None, None, "piece", "other")], "")}
    lines, issues = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, ft, NORMS)
    assert "Плинтус" not in lines_by_material(lines)
    assert "floor_layer_without_thickness" not in [i.code for i in issues]


def test_upturn_goes_to_first_area_layer_only():
    room = make_room(FINISH)
    ft = {"7": FloorType("7", "", [FloorLayer("Ламинат", 8, 8, "area", "laminate"),
                                   FloorLayer("Гидроизоляция", 2, 2, "area", "roll_waterproofing")], "")}
    lines, _ = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, ft, NORMS)
    q = lines_by_material(lines)
    assert q["Ламинат"].quantity == 26.4
    assert q["Рулонная гидроизоляция"].quantity == 23.0


def test_floor_items_used_without_layers():
    room = make_room(FINISH, floor_type="9")
    lines, _ = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, {}, NORMS)
    q = lines_by_material(lines)
    # 24.3 м² стены + (20 + 5.13) м² пола, запас 10 %
    assert q["Керамогранит"].quantity == 54.4
    assert q["Клей плиточный"].quantity == 247.2  # 247.15: от половины вверх


def test_other_key_uses_source_text_and_drawing_basis():
    long_text = "Х" * 80
    f = FinishSpec(["other"], None, [Zone(0.0, None, ["other"], "  декоративная панель  ")],
                   ["other"], 0.0, {"ceiling": long_text, "floor": ""})
    room = make_room(f, floor_type=None)
    lines, _ = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, {}, NORMS)
    q = lines_by_material(lines)
    assert (q["Х" * 60].unit, q["Х" * 60].quantity, q["Х" * 60].basis) == ("м²", 20.0, "чертёж")
    assert q["декоративная панель"].quantity == 49.9
    assert q["Без нормы"].quantity == 20.0  # пол: исходного текста нет


def test_key_missing_from_catalogue_is_treated_like_other():
    f = FinishSpec(["no_such_norm"], None, [Zone(0.0, None, [], "x")], [], 0.0, {"ceiling": "потолок Грильято"})
    room = make_room(f, floor_type=None)
    lines, _ = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, {}, NORMS)
    line = lines_by_material(lines)["потолок Грильято"]
    assert (line.unit, line.quantity, line.basis, line.reserve_pct) == ("м²", 20.0, "чертёж", 0.0)


def test_norm_lines_carry_reserve_basis_and_rooms():
    room = make_room(FINISH, floor_type=None)
    lines, _ = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, {}, NORMS)
    ceiling = lines_by_material(lines)["Подвесной потолок"]
    assert (ceiling.reserve_pct, ceiling.basis, ceiling.rooms) == (5.0, "норма", ["Т1"])


def test_rounding_happens_after_summing_rooms():
    # 0.04 + 0.04 = 0.08 -> 0.1, а не 0.0 + 0.0
    f = FinishSpec([], None, [Zone(0.0, None, ["primer"], "x")], [], 0.0, {})
    rooms = []
    for n in ("A", "B"):
        r = Room(n, "", "1", box(0, 0, 1, 1), 1.0, 4.0, None, f, [])
        r.perimeter_m = 0.04 / 0.15 / 3.0
        rooms.append(r)
    areas = {r.number: room_areas(r, 3.0) for r in rooms}
    lines, _ = aggregate_materials(rooms, areas, {}, NORMS)
    line = lines_by_material(lines)["Грунтовка"]
    assert line.quantity == 0.1 and line.rooms == ["A", "B"]


def test_unspecified_band_one_issue_per_room_with_area():
    room = make_room(FINISH, floor_type=None)
    _, issues = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, {}, NORMS)
    assert [(i.code, i.room) for i in issues] == [("unspecified_wall_band", "Т1")]
    assert "4,7" in issues[0].message


def test_zero_area_unspecified_zone_gives_no_issue():
    f = FinishSpec([], None, [Zone(0.0, 3.0, ["primer"], "x"), Zone(3.0, None, [], UNSPECIFIED)], [], 0.0, {})
    room = make_room(f, floor_type=None)
    _, issues = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, {}, NORMS)
    assert issues == []


def test_room_without_finish_has_no_materials_from_finish():
    room = make_room(None, floor_type=None)
    lines, issues = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, {}, NORMS)
    assert lines == [] and issues == []


def test_floor_type_areas_sums_rooms_and_skips_untyped():
    a = make_room(FINISH, floor_type="7")
    b = Room("Т2", "", "1", box(0, 0, 3, 3), 9.04, 12.0, "7", None, [])
    c = Room("Т3", "", "1", box(0, 0, 2, 2), 4.0, 8.0, None, None, [])
    d = Room("Т4", "", "1", box(0, 0, 2, 2), 4.0, 8.0, "9", None, [])
    rooms = [a, b, c, d]
    areas = {r.number: room_areas(r, 3.0) for r in rooms}
    assert floor_type_areas(rooms, areas) == {"7": 29.0, "9": 4.0}
