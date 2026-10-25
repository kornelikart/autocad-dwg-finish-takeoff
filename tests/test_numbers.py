from finishcalc.numbers import normalize_room_number, parse_ru_float, floor_key

def test_normalize_room_number():
    assert normalize_room_number("A11") == "А11"      # латинская A → кириллическая
    assert normalize_room_number(" а 15 ") == "А15"
    assert normalize_room_number("B12") == "В12"
    assert normalize_room_number("9-H.2") == "9-Н.2"
    assert normalize_room_number("-") == ""

def test_parse_ru_float():
    assert parse_ru_float("25,5") == 25.5
    assert parse_ru_float("1 848,8") == 1848.8
    assert parse_ru_float("1\u00a0848,8") == 1848.8
    assert parse_ru_float("-") is None
    assert parse_ru_float("") is None
    assert parse_ru_float("около 5") is None

def test_floor_key():
    assert floor_key("Экспликация помещений 1-го этажа") == "1"
    assert floor_key("Экспликация помещений 2-го этажа") == "2"
    assert floor_key("Экспликация помещений подвального этажа") == "подвал"
    assert floor_key("Экспликация помещений") is None
