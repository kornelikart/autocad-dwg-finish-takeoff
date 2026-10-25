"""Общая модель данных: все остальные модули пакета обмениваются этими классами."""

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

from shapely.geometry import Polygon


@dataclass
class Profile:
    """Профиль чертежа: в каких слоях и блоках искать контуры, номерки, проёмы."""

    room_contour_layer: str
    room_tag_block: str
    room_tag_attr: str
    floor_tag_block: str
    floor_tag_attr: str
    openings_layer: str
    door_mark_layer: str | None
    window_mark_layer: str | None
    mm_per_unit: float = 1.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "Profile":
        return cls(**json.loads(s))


@dataclass
class Opening:
    """Дверь или окно; размеры и положение в метрах."""

    kind: Literal["door", "window"]
    mark: str | None
    width_m: float
    height_m: float
    sill_m: float
    rooms: list[str]
    size_source: Literal["spec", "default"]
    center: tuple[float, float]


@dataclass
class Zone:
    """Полоса стены: от высоты from_m до to_m (None — «до высоты H»)."""

    from_m: float
    to_m: float | None
    items: list[str]
    source_text: str


@dataclass
class FinishSpec:
    """Отделка одного помещения из ведомости; source хранит исходные тексты
    по ключам "ceiling", "walls", "floor"."""

    ceiling_items: list[str]
    ceiling_height_m: float | None
    walls: list[Zone]
    floor_items: list[str]
    floor_upturn_m: float
    source: dict[str, str]


@dataclass
class FloorLayer:
    """Слой пирога пола из экспликации полов."""

    material: str
    thickness_mm_min: float | None
    thickness_mm_max: float | None
    count_as: Literal["volume", "area", "piece"]
    norm_key: str


@dataclass
class FloorType:
    """Тип пола: код, помещения, слои, исходный текст."""

    code: str
    rooms_label: str
    layers: list[FloorLayer]
    source_text: str


@dataclass
class Room:
    """Помещение: геометрия в метрах, номер и тип пола из чертежа."""

    number: str
    name: str
    floor: str | None
    polygon: Polygon
    area_m2: float
    perimeter_m: float
    floor_type: str | None
    finish: FinishSpec | None = None
    openings: list[Opening] = field(default_factory=list)


@dataclass
class MaterialLine:
    """Строка итоговой ведомости материалов."""

    material: str
    unit: str
    quantity: float
    reserve_pct: float
    basis: Literal["норма", "чертёж"]
    rooms: list[str]


@dataclass
class Issue:
    """Предупреждение проверки. В спецификации — Warning; переименовано,
    чтобы не перекрывать встроенный Warning."""

    code: str
    room: str | None
    message: str
