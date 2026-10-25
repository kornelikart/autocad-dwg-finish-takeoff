import ezdxf
import pytest
from shapely.geometry import box

from finishcalc.calc import aggregate_materials, floor_type_areas, room_areas
from finishcalc.extract.tables import FinishRow
from finishcalc.finishes import UNSPECIFIED
from finishcalc.model import FinishSpec, FloorLayer, FloorType, Issue, Opening, Room, Zone
from finishcalc.norms import load_norms
from finishcalc.pipeline import ProjectResult
from tests.fixtures.synth import make_synthetic_plan


@pytest.fixture
def synth_doc(tmp_path):
    """Синтетический чертёж: сохранён в tmp_path и открыт заново с диска."""
    return ezdxf.readfile(make_synthetic_plan(tmp_path / "synth.dxf"))


@pytest.fixture
def small_result():
    """Результат по одному помещению Т1 (4 × 5 м, дверь 0.9 × 2.1, окно 1.5 × 1.5 с подоконником 0.9,
    заход пола 0.3, зоны стен [0; 1.8] плитка, [1.8; 2.1] не указано, [2.1; H] окраска, высота 3.0),
    собранный из тех же данных, что и расчёт в test_calc."""
    door = Opening("door", "ДС-2", 0.9, 2.1, 0.0, ["Т1"], "spec", (0, 0))
    window = Opening("window", "3", 1.5, 1.5, 0.9, ["Т1"], "default", (0, 0))
    source = {"ceiling": "Подвесной потолок", "walls": "Плитка до 1800 мм, окраска выше 2100 мм",
              "floor": "Керамогранит, завести на стену на 300мм."}
    finish = FinishSpec(
        ["suspended_ceiling"], None,
        [Zone(0.0, 1.8, ["porcelain_tile", "tile_adhesive"], "плитка"), Zone(1.8, 2.1, [], UNSPECIFIED),
         Zone(2.1, None, ["primer", "paint_wd"], "окраска")],
        ["porcelain_tile", "tile_adhesive"], 0.3, source)
    room = Room("Т1", "Санузел", "1", box(0, 0, 4, 5), 20.0, 18.0, "7", finish, [door, window])
    floor_types = {"7": FloorType(
        "7", "Санузлы",
        [FloorLayer("Керамогранит", 20, 20, "area", "porcelain_tile"),
         FloorLayer("Стяжка", 50, 50, "volume", "screed")],
        "1. Керамогранит - 20мм | 2. Стяжка - 50мм")}
    areas = {"Т1": room_areas(room, 3.0)}
    materials, issues = aggregate_materials([room], areas, floor_types, load_norms())
    issues.append(Issue("reveals_not_counted", None, "Откосы проёмов не учтены"))
    finish_rows = {"Т1": FinishRow("Т1", "Санузел", source["ceiling"], source["walls"], source["floor"],
                                   20.0, "Вход с коридора")}
    return ProjectResult(
        rooms=[room],
        areas=areas,
        finish_rows=finish_rows,
        floor_types=floor_types,
        floor_type_areas=floor_type_areas([room], areas),
        materials=materials,
        issues=issues,
        mode="A",
    )
