"""Интерфейс Streamlit: 1. Загрузка, 2. Профиль, 3. Параметры, 4. Результат.

Тяжёлые действия (чтение чертежей, вызов ИИ, расчёт) выполняются по кнопкам, а их итог лежит
в `st.session_state`: перезапуск скрипта при каждом клике ничего из этого не повторяет.
Ошибки ИИ, конвертера и профиля попадают в `st.session_state["llm_error"]` и показываются
сообщением вместо трассировки под тем шагом, где случились (без пометки шага — под шагом 1).
"""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from typing import get_args

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

from finishcalc.convert import ConvertError
from finishcalc.export import export_excel
from finishcalc.extract.rooms import extract_rooms
from finishcalc.extract.tables import parse_room_explication
from finishcalc.llm import LLMError, make_llm
from finishcalc.model import Issue, Profile, Room
from finishcalc.norms import Norm, Unit, load_norms, save_norms
from finishcalc.pipeline import ProjectResult, analyze, load_drawings, load_tables
from finishcalc.profile import (
    ProfileError, detect_profile, load_profile, refine_profile, save_profile, summarize_drawing)
from finishcalc.render import render_plan

CACHE_DIR = Path(".cache")
PROFILE_PATH = CACHE_DIR / "profile.json"
NORMS_PATH = CACHE_DIR / "norms.yaml"
DEMO_DIR_ENV = "FINISHCALC_DEMO_DIR"
DRAWING_SUFFIXES = {".dwg", ".dxf"}
PLAN_MARKER = "план"  # файл с этим словом в имени (без учёта регистра) — план, остальные — общие данные
DEFAULT_HEIGHT_M = 3.0
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NO_VALUE = "— нет —"
STEP_UPLOAD, STEP_PROFILE, STEP_RESULT = "upload", "profile", "result"

# Поле профиля, подпись, вид списка значений, поле необязательное.
PROFILE_FIELDS = [
    ("room_contour_layer", "Слой контуров помещений", "layer", False),
    ("room_tag_block", "Блок-номерок помещения", "block", False),
    ("room_tag_attr", "Атрибут с номером помещения", "attr", False),
    ("floor_tag_block", "Блок типа пола", "block", False),
    ("floor_tag_attr", "Атрибут с типом пола", "attr", False),
    ("openings_layer", "Слой дверей и окон", "layer", False),
    ("door_mark_layer", "Слой марок дверей", "layer", True),
    ("window_mark_layer", "Слой марок окон и проёмов", "layer", True),
]
PROFILE_LABELS = {name: label for name, label, _, _ in PROFILE_FIELDS}
UNIT_LABELS = {1.0: "миллиметры (1 ед. = 1 мм)", 10.0: "сантиметры (1 ед. = 10 мм)", 1000.0: "метры (1 ед. = 1000 мм)"}

TABLE_KIND_LABELS = {
    "finish_schedule": "ведомость отделки",
    "room_explication": "экспликация помещений",
    "floor_explication": "экспликация полов",
    "openings_spec": "спецификация проёмов",
}
NORM_COLUMNS = ["key", "title", "unit", "per_m2", "coats", "reserve_pct"]


# --- ошибки ---

def error_message(error: Exception) -> str:
    """Читаемый текст для ошибки ИИ, конвертера или профиля."""
    if isinstance(error, LLMError):
        return (f"ИИ не ответил: {error}. Проверьте ключ MISTRAL_API_KEY в файле .env "
                "и интернет либо запустите Ollama.")
    if isinstance(error, ProfileError):
        labels = ", ".join(PROFILE_LABELS.get(name, name) for name in error.fields)
        return f"ИИ не смог определить: {labels}. Выберите значения вручную на шаге 2."
    return str(error)


@contextmanager
def reporting_errors(step: str) -> Iterator[None]:
    """Сбрасывает прежнюю ошибку; `LLMError`, `ConvertError` и `ProfileError` внутри блока
    не прерывают страницу, а кладут сообщение в `st.session_state["llm_error"]` (шаг — в `llm_error_step`)."""
    st.session_state["llm_error"] = None
    try:
        yield
    except (LLMError, ConvertError, ProfileError) as error:
        st.session_state["llm_error"] = error_message(error)
        st.session_state["llm_error_step"] = step


def show_error(step: str) -> None:
    """Сообщение об ошибке, если она случилась на этом шаге."""
    if st.session_state.get("llm_error") and st.session_state.get("llm_error_step", STEP_UPLOAD) == step:
        st.error(st.session_state["llm_error"])


def next_version() -> int:
    """Номер версии профиля: он входит в ключи виджетов, и при новом черновике они создаются заново."""
    st.session_state["version_counter"] = st.session_state.get("version_counter", 0) + 1
    return st.session_state["version_counter"]


# --- шаг 1: загрузка ---

def demo_dir() -> Path | None:
    """Демо-папка из `FINISHCALC_DEMO_DIR`; читается при каждом запуске скрипта."""
    value = os.environ.get(DEMO_DIR_ENV)
    if value and Path(value).is_dir():
        return Path(value)
    return None


def split_demo_files(folder: Path) -> tuple[list[Path], list[Path]]:
    """DWG и DXF папки: планы (в имени есть «ПЛАН») и общие данные."""
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in DRAWING_SUFFIXES)
    plans = [p for p in files if PLAN_MARKER in p.name.casefold()]
    return plans, [p for p in files if p not in plans]


def save_uploads(files, folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for uploaded in files:
        path = folder / Path(uploaded.name).name
        path.write_bytes(uploaded.getvalue())
        paths.append(path)
    return paths


def read_project(plans: list[Path], data: list[Path], work: Path) -> dict:
    """Читает чертежи, таблицы и сводку первого плана; роли профиля ищутся по правилам."""
    plan_docs = load_drawings(plans, work / "plans")
    data_docs = load_drawings(data, work / "data")
    found, unresolved = detect_profile(plan_docs[0])
    return {
        "version": next_version(),
        "plan_names": [p.name for p in plans],
        "data_names": [p.name for p in data],
        "plan_docs": plan_docs,
        "tables": load_tables([*plan_docs, *data_docs]),
        "summary": summarize_drawing(plan_docs[0]),
        "needs_ai": bool(unresolved),
        "found": found,
        "unresolved": unresolved,
        # Черновик профиля: нерешённые поля пусты, пока их не заполнит ИИ или пользователь.
        "draft": {**{name: found.get(name) for name, *_ in PROFILE_FIELDS}, "mm_per_unit": found["mm_per_unit"]},
    }


def suggest_profile(project: dict) -> None:
    """ИИ выбирает значения нерешённых полей; удалось — черновик и версия обновляются."""
    profile = refine_profile(project["summary"], project["found"], project["unresolved"],
                             make_llm(os.environ, CACHE_DIR))
    project["draft"] = asdict(profile)
    project["needs_ai"] = False
    project["version"] = next_version()


def open_project(plans: list[Path], data: list[Path], work: Path) -> None:
    """Загружает проект в сессию (прежний результат сбрасывается) и просит ИИ уточнить профиль."""
    project = read_project(plans, data, work)
    st.session_state["project"] = project
    st.session_state["preview"] = None
    st.session_state["result"] = None
    if project["needs_ai"]:
        suggest_profile(project)


def step_upload() -> dict | None:
    st.header("1. Загрузка")
    folder = demo_dir()
    if folder is not None:
        if st.button("Открыть демо-проект", key="demo"):
            plans, data = split_demo_files(folder)
            if not plans:
                st.warning(f"В демо-папке {folder} нет планов: ищу DWG или DXF с «ПЛАН» в имени.")
            else:
                with reporting_errors(STEP_UPLOAD), st.spinner("Читаю чертежи демо-проекта…"):
                    with tempfile.TemporaryDirectory() as tmp:
                        open_project(plans, data, Path(tmp))
        st.caption(f"Демо-папка: {folder}")

    plan_files = st.file_uploader("Планы этажей (DWG или DXF)", type=["dwg", "dxf"],
                                  accept_multiple_files=True, key="upload_plans")
    data_files = st.file_uploader(
        "Общие данные: ведомость отделки, экспликации, спецификации (DWG или DXF), если их нет на планах",
        type=["dwg", "dxf"], accept_multiple_files=True, key="upload_data")
    if st.button("Прочитать чертежи", key="read", disabled=not plan_files):
        with reporting_errors(STEP_UPLOAD), st.spinner("Читаю чертежи…"):
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                open_project(save_uploads(plan_files, work / "upload_plans"),
                             save_uploads(data_files, work / "upload_data"), work)
    show_error(STEP_UPLOAD)

    project = st.session_state.get("project")
    if project is not None:
        kinds = [table.kind for table in project["tables"]]
        found = [f"{label}: {kinds.count(kind)}" for kind, label in TABLE_KIND_LABELS.items() if kind in kinds]
        st.success(f"Прочитано планов: {len(project['plan_names'])}, файлов с общими данными: "
                   f"{len(project['data_names'])}. Таблицы: {'; '.join(found) or 'не найдены'}.")
        if "finish_schedule" not in kinds:
            st.info("Ведомости отделки нет: отделку помещений предложит ИИ по их названиям.")
    return project


# --- шаг 2: профиль ---

def pick(label: str, options: list[str], current: str | None, key: str, optional: bool = False) -> str | None:
    """Выпадающий список; `current` выбран заранее, если он есть среди значений."""
    choices = [NO_VALUE, *options] if optional else list(options)
    if current in choices:
        index = choices.index(current)
    else:
        index = 0 if optional else None
    chosen = st.selectbox(label, choices, index=index, key=key, placeholder="Выберите…")
    return None if chosen in (None, NO_VALUE) else chosen


def profile_form(project: dict) -> Profile | None:
    """Выпадающие списки по каждому полю профиля; Profile, когда обязательные поля выбраны."""
    summary, draft, version = project["summary"], project["draft"], project["version"]
    layers = [layer["name"] for layer in summary["layers"]]
    tag_blocks = {b["name"]: b["attribs"] for b in summary["blocks"] if b["attribs"]}
    all_tags = list(dict.fromkeys(tag for tags in tag_blocks.values() for tag in tags))

    values: dict = {}
    columns = st.columns(2)
    for position, (name, label, kind, optional) in enumerate(PROFILE_FIELDS):
        key = f"profile_{version}_{name}"
        with columns[0 if position < 5 else 1]:
            if kind == "layer":
                values[name] = pick(label, layers, draft.get(name), key, optional)
            elif kind == "block":
                values[name] = pick(label, list(tag_blocks), draft.get(name), key)
            else:
                block = values[name.replace("_attr", "_block")]
                values[name] = pick(label, tag_blocks.get(block, all_tags), draft.get(name),
                                    f"{key}_{block}")
    with columns[1]:
        units = list(UNIT_LABELS)
        current = draft["mm_per_unit"]
        values["mm_per_unit"] = st.selectbox(
            "Единица чертежа", units, index=units.index(current) if current in units else 0,
            format_func=UNIT_LABELS.get, key=f"profile_{version}_units")

    missing = [PROFILE_LABELS[name] for name, _, _, optional in PROFILE_FIELDS
               if not optional and values[name] is None]
    if missing:
        st.warning("Выберите значения: " + ", ".join(missing) + ".")
        return None
    return Profile(**values)


def png_bytes(rooms: list[Room], issues: list[Issue]) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        return render_plan(rooms, issues, Path(tmp) / "plan.png").read_bytes()


def plan_preview(project: dict, profile: Profile) -> dict:
    """Помещения и картинка каждого плана при этом профиле; пересчитывается, только если профиль другой."""
    cached = st.session_state.get("preview")
    if cached is not None and cached["profile"] == profile:
        return cached
    rooms_by_plan, images = [], []
    for doc in project["plan_docs"]:
        rooms, issues = extract_rooms(doc, profile)
        rooms_by_plan.append(rooms)
        images.append(png_bytes(rooms, issues))
    preview = {"profile": profile, "rooms_by_plan": rooms_by_plan, "images": images}
    st.session_state["preview"] = preview
    return preview


def step_profile(project: dict | None) -> Profile | None:
    st.header("2. Профиль")
    if project is None:
        st.info("Сначала загрузите планы на шаге 1.")
        return None
    st.caption(f"Слои и блоки определяются по первому плану: {project['plan_names'][0]}. "
               "Ненайденное правилами подставляет ИИ, всё можно поправить вручную.")

    if project["needs_ai"] and st.button("Определить остальное с помощью ИИ", key="refine"):
        with reporting_errors(STEP_PROFILE), st.spinner("Спрашиваю ИИ…"):
            suggest_profile(project)
    show_error(STEP_PROFILE)
    if PROFILE_PATH.is_file() and st.button("Взять сохранённый профиль", key="load_profile"):
        try:
            project["draft"] = asdict(load_profile(PROFILE_PATH))
            project["version"] = next_version()
        except (OSError, ValueError, TypeError) as error:
            st.error(f"Не удалось прочитать {PROFILE_PATH}: {error}")

    profile = profile_form(project)
    if profile is None:
        return None
    if st.button("Сохранить профиль", key="save_profile"):
        save_profile(profile, PROFILE_PATH)
        st.success(f"Профиль сохранён: {PROFILE_PATH}")

    with st.spinner("Рисую план…"):
        preview = plan_preview(project, profile)
    found = sum(len(rooms) for rooms in preview["rooms_by_plan"])
    if found == 0:
        st.warning("Помещения не найдены: проверьте слой контуров и блок-номерок.")
    else:
        st.caption(f"Найдено помещений: {found}. Зелёные — без замечаний, оранжевые — с замечаниями.")
    for name, image in zip(project["plan_names"], preview["images"]):
        st.image(image, caption=name)
    return profile


# --- шаг 3: параметры ---

def floor_order(floor: str) -> tuple[int, int, str]:
    """Подвал, затем этажи по номеру, затем прочее."""
    if floor.isdigit():
        return 1, int(floor), ""
    return (0 if floor == "подвал" else 2), 0, floor


def project_floors(project: dict) -> list[str]:
    """Этажи из заголовков экспликаций помещений."""
    floors = {row.floor for table in project["tables"] if table.kind == "room_explication"
              for row in parse_room_explication(table) if row.floor}
    return sorted(floors, key=floor_order)


def load_saved_norms() -> dict[str, Norm]:
    """Справочник из `.cache/norms.yaml`, если он есть и читается, иначе стандартный."""
    if NORMS_PATH.is_file():
        try:
            return load_norms(NORMS_PATH)
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
            st.warning(f"Не удалось прочитать {NORMS_PATH}, взят стандартный справочник норм.")
    return load_norms()


def norms_frame(norms: dict[str, Norm]) -> pd.DataFrame:
    return pd.DataFrame([{column: getattr(norm, column) for column in NORM_COLUMNS} for norm in norms.values()],
                        columns=NORM_COLUMNS)


def norms_from_frame(frame: pd.DataFrame) -> dict[str, Norm]:
    """Нормы из таблицы редактора. Raises: ValueError, если обязательная ячейка пуста."""
    norms = {}
    for row in frame.to_dict("records"):
        key = row["key"]
        if pd.isna(row["title"]) or not str(row["title"]).strip():
            raise ValueError(f"Норма «{key}»: не заполнено название.")
        if row["unit"] not in get_args(Unit):
            raise ValueError(f"Норма «{key}»: выберите единицу.")
        if pd.isna(row["coats"]) or pd.isna(row["reserve_pct"]):
            raise ValueError(f"Норма «{key}»: заполните число слоёв и запас.")
        per_m2 = None if pd.isna(row["per_m2"]) else float(row["per_m2"])
        norms[key] = Norm(key, str(row["title"]).strip(), row["unit"], per_m2,
                          int(row["coats"]), float(row["reserve_pct"]))
    return norms


def norms_editor() -> dict[str, Norm] | None:
    """Справочник норм в редактируемой таблице; None, пока в ней есть пустые обязательные ячейки."""
    st.subheader("Справочник норм")
    if "norms" not in st.session_state:
        st.session_state["norms"] = load_saved_norms()
    st.caption("Значения по умолчанию предварительные: проверьте их у сметчика. Пустой расход на 1 м² "
               "значит «количество берётся прямо с чертежа».")
    frame = st.data_editor(
        norms_frame(st.session_state["norms"]), key="norms_editor", hide_index=True, num_rows="fixed",
        column_config={
            "key": st.column_config.TextColumn("Ключ", disabled=True),
            "title": st.column_config.TextColumn("Материал", required=True),
            "unit": st.column_config.SelectboxColumn("Ед.", options=list(get_args(Unit)), required=True),
            "per_m2": st.column_config.NumberColumn("Расход на 1 м² за слой", min_value=0.0),
            "coats": st.column_config.NumberColumn("Слоёв", min_value=1, step=1, required=True),
            "reserve_pct": st.column_config.NumberColumn("Запас, %", min_value=0.0, required=True),
        })
    try:
        norms = norms_from_frame(frame)
    except ValueError as error:
        st.error(str(error))
        return None
    if st.button("Сохранить справочник норм", key="save_norms"):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        save_norms(norms, NORMS_PATH)
        st.success(f"Нормы сохранены: {NORMS_PATH}")
    return norms


def step_params(project: dict | None) -> tuple[dict[str, float], float, dict[str, Norm]] | None:
    """Высоты по этажам, высота по умолчанию и нормы; None, пока нет проекта или нормы заполнены не до конца.
    Виджеты рисуются при любом состоянии шагов, иначе Streamlit забыл бы правки в них."""
    st.header("3. Параметры")
    st.subheader("Высоты помещений")
    default_height = st.number_input("Высота по умолчанию, м", min_value=1.0, max_value=30.0,
                                     value=DEFAULT_HEIGHT_M, step=0.1, key="height_default")
    floors = project_floors(project) if project is not None else []
    if project is None:
        st.caption("Высоты по этажам появятся после загрузки планов.")
    elif not floors:
        st.caption("Этажи в экспликациях помещений не найдены: у всех помещений высота по умолчанию.")
    heights = {floor: st.number_input(f"Высота этажа «{floor}», м", min_value=1.0, max_value=30.0,
                                      value=DEFAULT_HEIGHT_M, step=0.1, key=f"height_{floor}")
               for floor in floors}
    norms = norms_editor()
    return None if project is None or norms is None else (heights, default_height, norms)


# --- шаг 4: результат ---

def calculate(project: dict, profile: Profile, heights: dict[str, float], default_height: float,
              norms: dict[str, Norm]) -> dict:
    """Расчёт, Excel и картинки планов; помещения раскладываются по планам, где их нашёл предпросмотр."""
    result = analyze(project["plan_docs"], project["tables"], profile, make_llm(os.environ, CACHE_DIR),
                     norms, heights, default_height)
    owner: dict[str, int] = {}
    for index, rooms in enumerate(plan_preview(project, profile)["rooms_by_plan"]):
        for room in rooms:
            owner.setdefault(room.number, index)
    groups: list[list[Room]] = [[] for _ in project["plan_names"]]
    for room in result.rooms:
        groups[owner.get(room.number, 0)].append(room)
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = export_excel(result, Path(tmp) / "finishcalc.xlsx").read_bytes()
    plans = [(name, png_bytes(rooms, result.issues)) for name, rooms in zip(project["plan_names"], groups) if rooms]
    return {"result": result, "xlsx": xlsx, "plans": plans}


def show_result(bundle: dict) -> None:
    result: ProjectResult = bundle["result"]
    if result.mode == "B":
        st.warning("В чертежах не найдена ведомость отделки, поэтому отделку помещений предложил ИИ "
                   "по их названиям. Проверьте её.")
    st.download_button("Скачать Excel", bundle["xlsx"], file_name="finishcalc.xlsx", mime=XLSX_MIME,
                       type="primary", key="download")
    sheets = pd.read_excel(BytesIO(bundle["xlsx"]), sheet_name=None)  # на экране то же, что на листах Excel
    st.subheader("По помещениям")
    st.dataframe(sheets["По помещениям"], hide_index=True)
    st.caption("Откосы проёмов не учтены.")
    st.subheader("Материалы")
    st.dataframe(sheets["Материалы"], hide_index=True)
    st.subheader(f"Предупреждения: {len(result.issues)}")
    st.dataframe(sheets["Проверки"][["Помещение", "Сообщение", "Код"]], hide_index=True)
    for name, image in bundle["plans"]:
        st.image(image, caption=f"{name}: зелёные помещения без замечаний, оранжевые — с замечаниями")


def step_result(project: dict | None, profile: Profile | None, params) -> None:
    st.header("4. Результат")
    if project is None or profile is None or params is None:
        st.info("Загрузите планы, выберите профиль и заполните нормы, затем нажмите «Посчитать».")
        return
    heights, default_height, norms = params
    inputs = (profile, heights, default_height, norms)
    if st.button("Посчитать", type="primary", key="calculate"):
        with reporting_errors(STEP_RESULT), st.spinner("Считаю площади и материалы…"):
            bundle = calculate(project, profile, heights, default_height, norms)
            st.session_state["result"] = {**bundle, "inputs": inputs}
    show_error(STEP_RESULT)
    bundle = st.session_state.get("result")
    if bundle is None:
        st.info("Нажмите «Посчитать».")
    elif bundle["inputs"] != inputs:
        st.warning("Профиль, высоты или нормы изменились после расчёта: нажмите «Посчитать» ещё раз.")
    else:
        show_result(bundle)


def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="Расчёт отделки", layout="wide")
    st.title("Расчёт отделочных материалов по чертежам")
    project = step_upload()
    profile = step_profile(project)
    params = step_params(project)
    step_result(project, profile, params)


if __name__ == "__main__":  # Streamlit запускает скрипт как __main__; тесты могут импортировать помощники
    main()
