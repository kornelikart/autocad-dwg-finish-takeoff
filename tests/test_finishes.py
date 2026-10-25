from finishcalc.finishes import (UNSPECIFIED, split_zones, ceiling_height, floor_upturn,
    map_items, parse_finish, parse_floor_type, suggest_finish)
from finishcalc.extract.tables import FinishRow, FloorTypeRow
from finishcalc.norms import load_norms
from tests.fakes import FakeLLM

N = load_norms()
WALLS = "выше 2100мм. -штукатурка, окраска. Ниже 1800мм.- настенный керамогранит"

# Описания в формате реальных ведомостей, но выдуманные.
WALLS_REAL_STYLE = ("выше 2100мм. -улучшенная штукатурка, окраска воднодисперсионной краской. Цвет- RAL 9999. "
                    "Ниже 1800мм.- настенный керамогранит (производитель условный)")


def test_split_zones():
    assert split_zones(WALLS) == [(0.0, 1.8, "настенный керамогранит"), (1.8, 2.1, UNSPECIFIED), (2.1, None, "штукатурка, окраска")]
    assert split_zones("Штукатурка, окраска ВД") == [(0.0, None, "Штукатурка, окраска ВД")]


def test_split_zones_real_style_text():
    assert split_zones(WALLS_REAL_STYLE) == [
        (0.0, 1.8, "настенный керамогранит (производитель условный)"),
        (1.8, 2.1, UNSPECIFIED),
        (2.1, None, "улучшенная штукатурка, окраска воднодисперсионной краской. Цвет- RAL 9999"),
    ]


def test_split_zones_ignores_case_spaces_and_yo():
    zones = split_zones("ВЫШЕ 2100 мм - Шпаклёвка, окраска.\nНИЖЕ 1800 мм – Плитка")
    assert zones == [(0.0, 1.8, "Плитка"), (1.8, 2.1, UNSPECIFIED), (2.1, None, "Шпаклёвка, окраска")]


def test_split_zones_adjacent_bands_have_no_gap():
    assert split_zones("выше 1800мм - окраска. ниже 1800мм - плитка") == [
        (0.0, 1.8, "плитка"), (1.8, None, "окраска")]


def test_split_zones_single_marker_leaves_other_band_unspecified():
    assert split_zones("ниже 1800мм - плитка") == [(0.0, 1.8, "плитка"), (1.8, None, UNSPECIFIED)]
    assert split_zones("выше 2100мм - окраска") == [(0.0, 2.1, UNSPECIFIED), (2.1, None, "окраска")]


def test_split_zones_blank_text_is_unspecified():
    assert split_zones("") == [(0.0, None, UNSPECIFIED)]
    assert split_zones(" - ") == [(0.0, None, UNSPECIFIED)]
    assert split_zones("выше 2100мм. ниже 1800мм - плитка") == [
        (0.0, 1.8, "плитка"), (1.8, 2.1, UNSPECIFIED), (2.1, None, UNSPECIFIED)]


def test_ceiling_height():
    assert ceiling_height("Подвесной потолок. h=3,0м") == 3.0
    assert ceiling_height("Подвесной потолок h=2,5-3,0м") == 3.0
    assert ceiling_height("Окраска по шпатлевке") is None


def test_ceiling_height_variants():
    assert ceiling_height("Подвесной потолок Armstrong. Цвет- RAL 9999 h=3,0м") == 3.0
    assert ceiling_height("потолок H = 2.7 м") == 2.7
    assert ceiling_height("потолок h=2,5–3,0м") == 3.0  # длинное тире
    assert ceiling_height("потолок h=3000мм") == 3.0  # миллиметры, а не метры


def test_floor_upturn():
    assert floor_upturn("Керамогранит, завести на стену на 300мм.") == 0.3
    assert floor_upturn("Ламинат") == 0.0


def test_floor_upturn_ignores_case_and_spacing():
    assert floor_upturn("Керамогранит. Завести на стену  на 150 мм") == 0.15


def test_map_items_drops_unknown_keys():
    llm = FakeLLM({"finish_items": {"items": ["plaster_gypsum", "bogus", "paint_wd"]}})
    assert map_items("штукатурка, окраска", N, llm) == ["plaster_gypsum", "other", "paint_wd"]


def test_map_items_prompt_schema_and_text():
    seen = {}

    def answer(system, user, schema, name):
        seen.update(system=system, user=user, schema=schema, name=name)
        return {"items": ["laminate"]}

    assert map_items("Ламинат 33 класса", N, FakeLLM(answer)) == ["laminate"]
    assert seen["name"] == "finish_items" and seen["user"] == "Ламинат 33 класса"
    assert seen["schema"] == {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string", "enum": [*N, "other"]}}},
        "required": ["items"], "additionalProperties": False}
    assert seen["system"].startswith("Ты сметчик.")
    for key, norm in N.items():
        assert f"{key} — {norm.title}" in seen["system"]


def test_map_items_blank_text_does_not_call_llm():
    llm = FakeLLM(lambda *a: (_ for _ in ()).throw(AssertionError("LLM не должен вызываться")))
    assert map_items("", N, llm) == [] and map_items("  \n", N, llm) == []
    assert llm.calls == 0


def test_parse_finish():
    by_text = {"Подвесной потолок h=3,0м": ["suspended_ceiling"], "настенный керамогранит": ["porcelain_tile", "tile_adhesive"],
               "штукатурка, окраска": ["plaster_gypsum", "primer", "paint_wd"], "Керамогранит, завести на стену на 300мм.": ["porcelain_tile", "tile_adhesive"]}
    llm = FakeLLM(lambda s, u, sch, name: {"items": next(v for k, v in by_text.items() if k in u)})
    f = parse_finish(FinishRow("А01", "Холл", "Подвесной потолок h=3,0м", WALLS, "Керамогранит, завести на стену на 300мм.", 20.0, ""), N, llm)
    assert f.ceiling_items == ["suspended_ceiling"] and f.ceiling_height_m == 3.0 and f.floor_upturn_m == 0.3
    assert [(z.from_m, z.to_m, z.items) for z in f.walls] == [(0.0, 1.8, ["porcelain_tile", "tile_adhesive"]), (1.8, 2.1, []), (2.1, None, ["plaster_gypsum", "primer", "paint_wd"])]
    assert f.walls[1].source_text == UNSPECIFIED


def test_parse_finish_keeps_source_texts_and_skips_llm_for_unspecified_band():
    llm = FakeLLM({"finish_items": {"items": ["paint_wd"]}})
    row = FinishRow("А02", "Склад", "Окраска", WALLS, "Окраска", None, "")
    f = parse_finish(row, N, llm)
    assert f.source == {"ceiling": "Окраска", "walls": WALLS, "floor": "Окраска"}
    assert llm.calls == 4  # потолок, пол и две описанные полосы стены; полоса «не указано» без ИИ
    assert f.walls[0].source_text == "настенный керамогранит"
    assert f.ceiling_height_m is None and f.floor_upturn_m == 0.0


def test_parse_finish_empty_cells():
    llm = FakeLLM(lambda *a: (_ for _ in ()).throw(AssertionError("LLM не должен вызываться")))
    f = parse_finish(FinishRow("А03", "Тамбур", "", "", "", None, ""), N, llm)
    assert f.ceiling_items == [] and f.floor_items == []
    assert [(z.from_m, z.to_m, z.items, z.source_text) for z in f.walls] == [(0.0, None, [], UNSPECIFIED)]


def test_parse_floor_type():
    llm = FakeLLM({"floor_layers": {"layers": [
        {"material": "Керамогранит", "thickness_mm_min": 20, "thickness_mm_max": 20, "count_as": "area", "norm_key": "porcelain_tile"},
        {"material": "Стяжка ЦПР", "thickness_mm_min": 50, "thickness_mm_max": 95, "count_as": "volume", "norm_key": "screed"},
        {"material": "Плита перекрытия", "thickness_mm_min": 0, "thickness_mm_max": 0, "count_as": "piece", "norm_key": "other"}]}})
    ft = parse_floor_type(FloorTypeRow("7", "Холлы", "1. Керамогранит - 20мм | 2. Стяжка ЦПР - 50-95мм"), N, llm)
    assert ft.code == "7" and [l.norm_key for l in ft.layers] == ["porcelain_tile", "screed", "other"]
    assert (ft.layers[1].thickness_mm_min, ft.layers[1].thickness_mm_max) == (50, 95)
    assert ft.layers[2].thickness_mm_min is None


def test_parse_floor_type_request_and_unknown_norm_key():
    seen = {}

    def answer(system, user, schema, name):
        seen.update(system=system, user=user, schema=schema, name=name)
        return {"layers": [{"material": "Мастика", "thickness_mm_min": 3, "thickness_mm_max": 3,
                            "count_as": "area", "norm_key": "bogus"}]}

    text = "1. Ламинат - 8мм. 2. Подложка - 3мм 3. 1 слой Изопласта внахлест"
    ft = parse_floor_type(FloorTypeRow("12", "Кабинеты", text), N, FakeLLM(answer))
    assert seen["name"] == "floor_layers" and seen["user"] == text
    assert ft.rooms_label == "Кабинеты" and ft.source_text == text
    assert ft.layers[0].norm_key == "other" and ft.layers[0].count_as == "area"
    layer = seen["schema"]["properties"]["layers"]["items"]
    assert layer["properties"]["count_as"]["enum"] == ["volume", "area", "piece"]
    assert layer["properties"]["norm_key"]["enum"] == [*N, "other"]
    assert layer["properties"]["thickness_mm_min"] == {"type": "number"}
    assert set(layer["required"]) == set(layer["properties"]) and layer["additionalProperties"] is False
    # слои нумеруются «1.», «2.», … и могут быть разделены «|» или только номерами
    assert "«1.»" in seen["system"] and "«|»" in seen["system"]


def test_suggest_finish_single_zone():
    llm = FakeLLM({"suggest_finish": {"ceiling": ["primer", "paint_wd"], "walls": ["plaster_gypsum", "paint_wd"], "floor": ["laminate"]}})
    f = suggest_finish("Кабинет", N, llm)
    assert [(z.from_m, z.to_m) for z in f.walls] == [(0.0, None)] and f.floor_items == ["laminate"]


def test_suggest_finish_marks_source_and_cleans_keys():
    seen = {}

    def answer(system, user, schema, name):
        seen.update(user=user, schema=schema, name=name)
        return {"ceiling": ["bogus"], "walls": ["putty"], "floor": []}

    f = suggest_finish("Кабинет директора", N, FakeLLM(answer))
    assert seen["name"] == "suggest_finish" and seen["user"] == "Кабинет директора"
    assert set(seen["schema"]["properties"]) == {"ceiling", "walls", "floor"}
    assert seen["schema"]["required"] == ["ceiling", "walls", "floor"]
    assert f.ceiling_items == ["other"] and f.walls[0].items == ["putty"] and f.floor_items == []
    assert f.walls[0].source_text == "предложено ИИ"
    assert f.source == {"ceiling": "предложено ИИ", "walls": "предложено ИИ", "floor": "предложено ИИ"}
    assert f.ceiling_height_m is None and f.floor_upturn_m == 0.0
