import pytest
from matplotlib.colors import to_rgb
from matplotlib.image import imread
from shapely.geometry import MultiPolygon, box

from finishcalc.extract.rooms import extract_rooms
from finishcalc.model import Issue, Room
from finishcalc.render import COLOR_OK, COLOR_WARN, render_plan
from tests.fixtures.synth import SYNTH_PROFILE


@pytest.fixture
def synth_rooms(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    return rooms


def _room(number: str, polygon) -> Room:
    return Room(number, "", None, polygon, polygon.area, polygon.length, None)


def _pixel(png, x_share: float, y_share: float) -> tuple[float, float, float]:
    """RGB пикселя на заданной доле ширины и высоты картинки."""
    image = imread(png)
    height, width = image.shape[:2]
    return tuple(image[int(height * y_share), int(width * x_share), :3])


def _assert_color(actual, expected: str) -> None:
    assert actual == pytest.approx(to_rgb(expected), abs=0.01)


def test_render_plan_writes_png(synth_rooms, tmp_path):
    issues = [Issue("unspecified_wall_band", "А01", "")]
    out = render_plan(synth_rooms, issues, tmp_path / "p.png")
    assert out == tmp_path / "p.png"
    assert out.exists()
    assert out.stat().st_size > 10 * 1024


def test_room_without_issues_is_green(tmp_path):
    out = render_plan([_room("Т1", box(0, 0, 10, 10))], [], tmp_path / "p.png")
    _assert_color(_pixel(out, 0.25, 0.25), COLOR_OK)


def test_room_with_issue_is_orange(tmp_path):
    issues = [Issue("floor_type_mismatch", "Т1", "")]
    out = render_plan([_room("Т1", box(0, 0, 10, 10))], issues, tmp_path / "p.png")
    _assert_color(_pixel(out, 0.25, 0.25), COLOR_WARN)


def test_issue_without_room_colours_nothing(tmp_path):
    issues = [Issue("reveals_not_counted", None, "")]
    out = render_plan([_room("Т1", box(0, 0, 10, 10))], issues, tmp_path / "p.png")
    _assert_color(_pixel(out, 0.25, 0.25), COLOR_OK)


def test_each_room_coloured_by_its_own_issues(tmp_path):
    rooms = [_room("Т1", box(0, 0, 10, 10)), _room("Т2", box(10, 0, 20, 10))]
    out = render_plan(rooms, [Issue("floor_type_mismatch", "Т2", "")], tmp_path / "p.png")
    _assert_color(_pixel(out, 0.1, 0.25), COLOR_OK)
    _assert_color(_pixel(out, 0.9, 0.25), COLOR_WARN)


def test_every_part_of_multipolygon_is_drawn(tmp_path):
    parts = MultiPolygon([box(0, 0, 10, 10), box(30, 0, 40, 10)])
    out = render_plan([_room("Т1", parts)], [], tmp_path / "p.png")
    _assert_color(_pixel(out, 0.05, 0.25), COLOR_OK)
    _assert_color(_pixel(out, 0.95, 0.25), COLOR_OK)


def test_empty_room_list_writes_blank_image(tmp_path):
    out = render_plan([], [], tmp_path / "sub" / "p.png")
    assert out.exists()
    assert out.stat().st_size > 0
