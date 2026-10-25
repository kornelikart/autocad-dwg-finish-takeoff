"""Приёмка на реальном проекте.

    python scripts/acceptance.py <папка> [--height ЭТАЖ=М ...] [--default-height М] [--profile ФАЙЛ]

В папке — DWG или DXF проекта: планы (в имени есть «ПЛАН») и общие данные. Скрипт считает проект,
сохраняет `<папка>/finishcalc.xlsx` и сверяет площади пола с колонкой «площадь пола» ведомости отделки.
Код возврата 0, если площадь совпала (±0.1 м²) хотя бы у 90% сопоставленных помещений и каждое
помещение ведомости с загруженных этажей посчитано или имеет предупреждение; иначе 1.
"""

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from finishcalc.calc import round_half_up
from finishcalc.convert import ConvertError
from finishcalc.export import export_excel
from finishcalc.extract.tables import ClassifiedTable, parse_room_explication
from finishcalc.llm import LLM, LLMError, make_llm
from finishcalc.model import Profile
from finishcalc.norms import load_norms
from finishcalc.pipeline import ProjectResult, analyze, load_drawings, load_tables, split_project_files
from finishcalc.profile import ProfileError, detect_profile, load_profile, refine_profile, summarize_drawing

CACHE_DIR = Path(".cache")
AREA_TOLERANCE_M2 = 0.1
REQUIRED_SHARE = 0.9
_EPS = 1e-9


@dataclass
class Report:
    """Итог сверки. `mismatches` — (номер, площадь по плану, площадь по ведомости)."""

    matched: int
    compared: int
    agreed: int
    mismatches: list[tuple[str, float, float]]
    in_scope: int
    computed: int
    warned: list[str]
    missing: list[str]
    issue_counts: Counter = field(default_factory=Counter)

    @property
    def share(self) -> float:
        return self.agreed / self.compared if self.compared else 0.0

    @property
    def passed(self) -> bool:
        return self.compared > 0 and self.share >= REQUIRED_SHARE and not self.missing


def schedule_scope(result: ProjectResult, tables: list[ClassifiedTable]) -> set[str]:
    """Номера ведомости, которые должны найтись на загруженных планах: помещения планов и помещения
    экспликаций тех же этажей. Этажи не определены — вся ведомость."""
    plan_floors = {room.floor for room in result.rooms if room.floor}
    if not plan_floors:
        return set(result.finish_rows)
    explication = {row.number for table in tables if table.kind == "room_explication"
                   for row in parse_room_explication(table) if row.floor in plan_floors}
    return ({room.number for room in result.rooms} | explication) & set(result.finish_rows)


def check(result: ProjectResult, scope: set[str]) -> Report:
    """Сверка площадей пола с ведомостью и полнота расчёта помещений ведомости из `scope`."""
    by_number = {room.number: room for room in result.rooms}
    matched = [number for number in result.finish_rows if number in by_number]
    mismatches, agreed, compared = [], 0, 0
    for number in matched:
        expected = result.finish_rows[number].floor_area_m2
        if expected is None:
            continue
        compared += 1
        actual = round_half_up(result.areas[number].floor_m2, 1)
        if abs(actual - expected) <= AREA_TOLERANCE_M2 + _EPS:
            agreed += 1
        else:
            mismatches.append((number, actual, expected))

    flagged = {issue.room for issue in result.issues if issue.room}
    computed = [n for n in scope if n in result.areas and by_number[n].finish is not None]
    rest = sorted(set(scope) - set(computed))
    return Report(
        matched=len(matched), compared=compared, agreed=agreed, mismatches=mismatches,
        in_scope=len(scope), computed=len(computed),
        warned=[n for n in rest if n in flagged], missing=[n for n in rest if n not in flagged],
        issue_counts=Counter(issue.code for issue in result.issues))


def print_report(report: Report, mode: str) -> None:
    print(f"Режим: {mode} ({'ведомость отделки найдена' if mode == 'A' else 'ведомости нет, отделку предложил ИИ'})")
    print(f"Помещений плана, найденных в ведомости: {report.matched}")
    print(f"Совпадение площади пола с ведомостью (±{AREA_TOLERANCE_M2} м²): {report.agreed}/{report.compared} "
          f"({report.share:.0%}, нужно ≥ {REQUIRED_SHARE:.0%})")
    for number, actual, expected in report.mismatches:
        print(f"  {number}: по плану {actual} м², по ведомости {expected} м²")
    print(f"Помещений ведомости на загруженных этажах: {report.in_scope}; посчитано: {report.computed}; "
          f"с предупреждением вместо площадей: {len(report.warned)}")
    if report.missing:
        print(f"  без площадей и без предупреждения: {', '.join(report.missing)}")
    print("Предупреждения по кодам:")
    for code, count in report.issue_counts.most_common():
        print(f"  {code}: {count}")
    print("ПРИЁМКА ПРОЙДЕНА" if report.passed else "ПРИЁМКА НЕ ПРОЙДЕНА")


def run(folder: Path, heights: dict[str, float], default_height: float, llm: LLM,
        profile: Profile | None = None) -> int:
    """Считает проект из `folder`, печатает сверку и сохраняет Excel; возвращает код возврата."""
    plans, data = split_project_files(folder)
    if not plans:
        print(f"В {folder} нет планов: нужны DWG или DXF с «ПЛАН» в имени.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        plan_docs = load_drawings(plans, Path(tmp) / "plans")
        data_docs = load_drawings(data, Path(tmp) / "data")
    tables = load_tables([*plan_docs, *data_docs])
    if profile is None:
        found, unresolved = detect_profile(plan_docs[0])
        profile = refine_profile(summarize_drawing(plan_docs[0]), found, unresolved, llm)
    print(f"Планы: {', '.join(p.name for p in plans)}; общие данные: {', '.join(p.name for p in data) or 'нет'}")
    print(f"Профиль: {profile.to_json()}")

    result = analyze(plan_docs, tables, profile, llm, load_norms(), heights, default_height)
    xlsx = export_excel(result, Path(folder) / "finishcalc.xlsx")
    print(f"Excel: {xlsx}")
    report = check(result, schedule_scope(result, tables))
    print_report(report, result.mode)
    return 0 if report.passed else 1


def _height(text: str) -> tuple[str, float]:
    floor, sep, value = text.partition("=")
    if not sep or not floor.strip():
        raise argparse.ArgumentTypeError(f"ожидается ЭТАЖ=М, например подвал=2.7 или 1=3.3, получено «{text}»")
    try:
        return floor.strip(), float(value.replace(",", "."))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"высота «{value}» — не число") from error


def _cached_answers(path: Path) -> int:
    try:
        return len(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Приёмка finishcalc на реальном проекте")
    parser.add_argument("folder", type=Path, help="папка с DWG/DXF проекта")
    parser.add_argument("--height", type=_height, action="append", default=[], metavar="ЭТАЖ=М",
                        help="высота помещений этажа, например --height подвал=2.7 --height 1=3.3")
    parser.add_argument("--default-height", type=float, default=3.0, metavar="М",
                        help="высота, если этаж не указан (по умолчанию 3.0)")
    parser.add_argument("--profile", type=Path, help="профиль, сохранённый в интерфейсе (.cache/profile.json)")
    args = parser.parse_args(argv)

    load_dotenv()
    cache_file = CACHE_DIR / "llm.json"
    before = _cached_answers(cache_file)
    profile = load_profile(args.profile) if args.profile else None
    try:
        code = run(args.folder, dict(args.height), args.default_height, make_llm(os.environ, CACHE_DIR), profile)
    except (LLMError, ConvertError, ProfileError) as error:
        print(f"Ошибка: {error}")
        return 1
    print(f"Новых ответов ИИ в кэше: {_cached_answers(cache_file) - before} "
          "(при повторном запуске должно быть 0 — кэш для демо готов)")
    return code


if __name__ == "__main__":
    sys.exit(main())
