"""Справочник норм расхода материалов: чтение и запись `norms.yaml`."""

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal, get_args

import yaml

Unit = Literal["м²", "м³", "кг", "л"]
_UNITS = get_args(Unit)


@dataclass
class Norm:
    """Норма одного материала. `per_m2` — расход на 1 м² за слой (None — количество
    берётся прямо с чертежа), `coats` — число слоёв, `reserve_pct` — запас, %."""

    key: str
    title: str
    unit: Unit
    per_m2: float | None
    coats: int
    reserve_pct: float


def _default_text() -> str:
    return resources.files("finishcalc").joinpath("norms.yaml").read_text(encoding="utf-8")


def _parse_norm(key: str, fields: dict) -> Norm:
    unit = fields["unit"]
    if unit not in _UNITS:
        raise ValueError(f"Норма «{key}»: единица «{unit}» не поддерживается (допустимы: {', '.join(_UNITS)})")
    per_m2 = fields["per_m2"]
    return Norm(
        key=key,
        title=fields["title"],
        unit=unit,
        per_m2=None if per_m2 is None else float(per_m2),
        coats=int(fields["coats"]),
        reserve_pct=float(fields["reserve_pct"]),
    )


def load_norms(path: Path | None = None) -> dict[str, Norm]:
    """Нормы из файла; без пути — `norms.yaml`, поставляемый с пакетом."""
    text = _default_text() if path is None else Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return {key: _parse_norm(key, fields) for key, fields in data.items()}


def save_norms(norms: dict[str, Norm], path: Path) -> None:
    """Записывает нормы в YAML того же вида, что читает `load_norms`."""
    data = {
        key: {"title": n.title, "unit": n.unit, "per_m2": n.per_m2,
              "coats": n.coats, "reserve_pct": n.reserve_pct}
        for key, n in norms.items()
    }
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    Path(path).write_text(text, encoding="utf-8")
