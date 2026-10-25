from finishcalc.extract.tables import (RawTable, ClassifiedTable, classify_tables,
    parse_room_explication, parse_finish_schedule, parse_floor_explication, parse_openings_spec)

E = [""] * 3
FINISH = [["Ведомость отделки помещений (Корпус 1)"] + [""] * 8,
          ["№ п/п", "Наименование помещения", "Вид отделки элементов помещений", "", "", "", "", "", "Примечание"],
          ["", "", "Потолок", "Площадь м²", "Стены/перегородки", "Площадь м²", "Напольное покрытие", "Площадь м²", ""],
          ["A01", "Холл", "Подвесной потолок h=3,0м", "", "Штукатурка, окраска", "", "Керамогранит, завести на стену на 300мм.", "20,0", ""],
          ["Б02", "Кладовая", "Окраска", "", "Окраска", "", "Линолеум", "-", ""],
          ["-", "Коридор 3-9эт", "Окраска", "", "Окраска", "", "Стяжка", "-", ""],
          [""] * 9]
EXPL = [["Экспликация помещений 1-го этажа"] + E, ["№пом.", "Наименование", "Площадь м.кв.", "Кат. пом."],
        ["Корпус 1"] + E, ["A01", "Холл", "20,0", ""], ["Б02", "Кладовая", "-", ""], ["Всего", "", "20,0", ""]]
EXPL_CONT = [["Корпус 2"] + E, ["В05", "Склад", "7,5", ""]]
FLOORS = [["Экспликация полов", "", "", "", ""], ["№пом.", "Тип пола", "Схема пола", "Данные элементов", "Площадь м.кв."],
          ["Холлы", "7", "", "1. Керамогранит - 20мм | 2. Стяжка ЦПР - 50мм", "-"],
          ["", "7*", "", "1. Керамогранит фальшпола - 20мм", "-"]]
SPEC = [["Спецификация заполнения проемов (начало - 1)"] + [""] * 12, ["Поз.", "Обозначение", "Наименование, В-Ш мм."] + [""] * 10,
        ["ДВЕРИ ДЕРЕВЯННЫЕ ВНУТРЕННИЕ"] + [""] * 12,
        ["ДС-3", "ГОСТ 475-2016", "ДС", "1Рп", "21,5", "-", "10", "Г", "ПрБ", "", "", "", "1"],
        ["ДС-1", "ГОСТ 475-2016", "ДС", "1Рп", "21", "-", "8", "Г", "ПрБ", "", "", "", "2"]]

def ct(kind, rows, title):
    return ClassifiedTable(kind, title, rows, (0.0, 0.0))

def test_classify_and_continuation():
    out = classify_tables([RawTable(EXPL, (0.0, 0.0)), RawTable(EXPL_CONT, (9905.0, 0.0)),
                           RawTable(FINISH, (0.0, -50000.0)), RawTable([["Условные обозначения:"]], (5.0, 5.0))])
    kinds = [t.kind for t in out]
    assert kinds == ["room_explication", "room_explication", "finish_schedule", "other"]
    assert out[1].title == "Экспликация помещений 1-го этажа"

def test_parse_room_explication():
    rows = parse_room_explication(ct("room_explication", EXPL, EXPL[0][0]))
    assert [(r.number, r.name, r.area_m2, r.floor) for r in rows] == [("А01", "Холл", 20.0, "1"), ("Б02", "Кладовая", None, "1")]

def test_parse_finish_schedule():
    rows = parse_finish_schedule(ct("finish_schedule", FINISH, FINISH[0][0]))
    assert [r.number for r in rows] == ["А01", "Б02"]
    assert rows[0].ceiling == "Подвесной потолок h=3,0м" and rows[0].floor_area_m2 == 20.0
    assert rows[1].floor_area_m2 is None

def test_parse_floor_explication():
    rows = parse_floor_explication(ct("floor_explication", FLOORS, "Экспликация полов"))
    assert [(r.code, r.rooms_label) for r in rows] == [("7", "Холлы"), ("7*", "")]

def test_parse_openings_spec():
    spec = parse_openings_spec(ct("openings_spec", SPEC, SPEC[0][0]))
    assert spec == {"ДС-3": (1.0, 2.15), "ДС-1": (0.8, 2.1)}
