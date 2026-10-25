import pytest

from finishcalc.norms import Norm, load_norms, save_norms


def test_defaults_and_roundtrip(tmp_path):
    n = load_norms()
    assert n["paint_wd"].per_m2 == 0.15 and n["paint_wd"].coats == 2
    assert n["porcelain_tile"].reserve_pct == 10 and n["screed"].unit == "м³"
    save_norms(n, tmp_path / "n.yaml")
    assert load_norms(tmp_path / "n.yaml") == n


def test_default_catalogue_values(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # файл ищется рядом с модулем, а не в текущей папке
    n = load_norms()
    assert list(n) == ["plaster_gypsum", "putty", "primer", "paint_wd", "porcelain_tile",
                       "tile_adhesive", "suspended_ceiling", "laminate", "roll_waterproofing", "screed"]
    assert n["plaster_gypsum"] == Norm("plaster_gypsum", "Штукатурка гипсовая (слой 10 мм)", "кг", 8.5, 1, 0)
    assert n["putty"] == Norm("putty", "Шпаклёвка", "кг", 1.0, 1, 0)
    assert n["primer"] == Norm("primer", "Грунтовка", "л", 0.15, 1, 0)
    assert n["paint_wd"] == Norm("paint_wd", "Краска водно-дисперсионная", "л", 0.15, 2, 0)
    assert n["porcelain_tile"] == Norm("porcelain_tile", "Керамогранит", "м²", None, 1, 10)
    assert n["tile_adhesive"] == Norm("tile_adhesive", "Клей плиточный", "кг", 5.0, 1, 0)
    assert n["suspended_ceiling"] == Norm("suspended_ceiling", "Подвесной потолок", "м²", None, 1, 5)
    assert n["laminate"] == Norm("laminate", "Ламинат", "м²", None, 1, 5)
    assert n["roll_waterproofing"] == Norm("roll_waterproofing", "Рулонная гидроизоляция", "м²", None, 1, 15)
    assert n["screed"] == Norm("screed", "Стяжка", "м³", None, 1, 0)


def test_edited_norm_survives_roundtrip(tmp_path):
    n = load_norms()
    n["putty"] = Norm("putty", "Шпаклёвка финишная", "кг", 1.4, 2, 3.5)
    save_norms(n, tmp_path / "n.yaml")
    assert load_norms(tmp_path / "n.yaml")["putty"] == Norm("putty", "Шпаклёвка финишная", "кг", 1.4, 2, 3.5)


def test_unknown_unit_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("x:\n  title: Что-то\n  unit: пуд\n  per_m2: 1\n  coats: 1\n  reserve_pct: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="x"):
        load_norms(p)
