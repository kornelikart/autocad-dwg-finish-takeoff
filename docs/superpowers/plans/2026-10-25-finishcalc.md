# finishcalc — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Python-приложение на Streamlit, которое по чертежам AutoCAD заполняет площади потолков, стен и полов в ведомости отделки и считает материалы, выгружая результат в Excel.

**Architecture:** Пакет `finishcalc` в `src/`. Извлечение данных (ezdxf + shapely) отделено от расчёта (чистые функции), ИИ (Mistral, запасной вариант Ollama) только переводит свободный текст в ключи справочника и роли слоёв. Модули связаны через dataclass-модель из `model.py`; `pipeline.analyze` собирает всё в `ProjectResult`, который потребляют `export`, `render` и `app.py`.

**Tech Stack:** Python ≥ 3.11 (локально 3.13), ezdxf ≥ 1.4, shapely ≥ 2.0, openpyxl, pandas, matplotlib, streamlit, requests, pyyaml, python-dotenv, pytest.

**Spec:** `docs/superpowers/specs/2026-10-25-finishcalc-design.md`

## Global Constraints

- Пакет `finishcalc` в `src/finishcalc/`, интерфейс — `app.py` в корне, запуск `streamlit run app.py`.
- Все команды — из корня репозитория через `.venv/Scripts/python.exe` (Windows). Тесты: `.venv/Scripts/python.exe -m pytest <путь> -v`.
- Единицы: чертёж в мм (`$INSUNITS = 4`), внутри программы вся геометрия в метрах. Коэффициент `Profile.mm_per_unit`: `$INSUNITS` 4 → 1.0, 5 (см) → 10.0, 6 (м) → 1000.0, иначе 1.0.
- ИИ: модель `ministral-14b-latest`, адрес `https://api.mistral.ai/v1/chat/completions`, `temperature = 0`, `response_format = {"type": "json_schema", "json_schema": {"name": ..., "schema": ..., "strict": true}}`. Ключ только из переменной окружения `MISTRAL_API_KEY` (файл `.env`). Без ключа — Ollama `qwen3.5` на `http://localhost:11434/api/chat`, `think: false`, `stream: false`.
- Кэш ИИ: `.cache/llm.json`. ИИ никогда не считает площади и количества.
- В репозиторий не попадают: `.env`, `data/`, `*.dwg`, `.cache/`. Реальные чертежи кладутся в `data/`. Тестовые данные — выдуманные, в формате реальных таблиц; тексты реального проекта в тесты не копировать.
- Значения по умолчанию: высота помещения 3.0 м; дверь 0.9 × 2.1 м (низ 0); окно 1.5 × 1.5 м, подоконник 0.9 м; радиус поиска марки проёма 1.5 м; проём относится к помещению, если расстояние от габарита блока до контура ≤ 0.3 м (уточнение спецификации: от габарита, а не от центра, — центр двери смещён дугой открывания).
- Округление: м² — 0.1, м³ — 0.01, остальные единицы — 0.1.
- Основание строки материалов (`MaterialLine.basis`): `"норма"` — количество посчитано по норме справочника; `"чертёж"` — площадь или объём прямо с чертежа без нормы (в Excel рядом пометка «норма не задана»).
- Коды предупреждений (`Issue.code`), ровно эти строки: `contour_without_number`, `contour_with_several_numbers`, `number_without_contour`, `several_floor_types`, `room_without_finish_row`, `finish_row_without_contour`, `floor_type_mismatch`, `floor_type_not_in_explication`, `floor_layer_without_thickness`, `opening_without_mark`, `opening_mark_not_in_spec`, `unspecified_wall_band`, `finish_suggested_by_ai`, `reveals_not_counted`.
- Текст неописанной полосы стены: `"не указано в ведомости"`.
- Листы Excel, ровно эти имена: «Ведомость отделки», «Экспликация полов», «Материалы», «По помещениям», «Проверки».
- Язык интерфейса и выгрузки — русский.

## Review Focus

1. **Повторённые и вложенные контуры** (один контур нарисован дважды; контур всего этажа вокруг комнат) — номер относится к наименьшему содержащему его контуру, одинаковые контуры не дают двух помещений. Тест: Task 2, `test_duplicate_contour_counted_once`, `test_issues`.
2. **Дуги в контурах** (скруглённые помещения) — площадь по сглаженному контуру отличается от точной не больше чем на 0,5%. Тест: Task 2, `test_bulge_room_area`.
3. **Номера с латиницей, пробелами, строчными буквами и строки «-»** (типовые этажи в ведомости) — сопоставление по нормализованному номеру, строки «-» пропускаются без ошибки. Тесты: Task 1, `test_normalize_room_number`; Task 3, `test_parse_finish_schedule`.
4. **Высота потолка диапазоном «h=2,5-3,0м» и описания без высоты** — берётся максимум, без высоты — высота этажа. Тест: Task 6, `test_ceiling_height`.
5. **Mistral недоступен** (нет ключа, 429 с лимитом 0, сетевая ошибка) — после трёх попыток `LLMError`, затем запасная Ollama; без ключа — сразу Ollama; если не работает и она, интерфейс показывает сообщение, а не трассировку. Тесты: Task 5, `test_mistral_retries_429_then_raises`, `test_fallback_used_on_llmerror`, `test_make_llm_without_key_uses_ollama`; Task 13, `test_app_shows_llm_error`.

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `pyproject.toml`, `.env.example`, `LICENSE`, `README.md` | пакет, зависимости, лицензия MIT, инструкция |
| `src/finishcalc/model.py` | dataclass-модель: `Profile`, `Room`, `Opening`, `Zone`, `FinishSpec`, `FloorLayer`, `FloorType`, `MaterialLine`, `Issue` |
| `src/finishcalc/numbers.py` | нормализация номеров, разбор чисел с запятой, ключ этажа |
| `src/finishcalc/convert.py` | DWG → DXF через ODA |
| `src/finishcalc/extract/rooms.py` | помещения с плана |
| `src/finishcalc/extract/tables.py` | чтение и разбор таблиц AutoCAD |
| `src/finishcalc/extract/openings.py` | двери и окна |
| `src/finishcalc/llm.py` | клиенты Mistral и Ollama, кэш |
| `src/finishcalc/norms.yaml`, `src/finishcalc/norms.py` | справочник норм |
| `src/finishcalc/finishes.py` | разбор описаний отделки и составов полов |
| `src/finishcalc/calc.py` | расчёт площадей и материалов |
| `src/finishcalc/profile.py` | профиль проекта |
| `src/finishcalc/pipeline.py` | сборка всего в `ProjectResult` |
| `src/finishcalc/export.py` | Excel |
| `src/finishcalc/render.py` | картинка плана |
| `src/finishcalc/chat.py` | чат по результату (бонус) |
| `app.py` | интерфейс Streamlit |
| `scripts/acceptance.py` | приёмка на реальном проекте из `data/` |
| `tests/fixtures/synth.py`, `tests/conftest.py`, `tests/fakes.py` | синтетический чертёж, фикстуры, поддельный ИИ |

---

### Task 1: Каркас пакета, модель данных, разбор чисел

**Files:**
- Create: `pyproject.toml`, `.env.example`, `src/finishcalc/__init__.py`, `src/finishcalc/model.py`, `src/finishcalc/numbers.py`
- Test: `tests/test_numbers.py`, `tests/test_model.py`

**Interfaces:**
- Produces (`model.py`, все — `@dataclass`):
  - `Profile(room_contour_layer: str, room_tag_block: str, room_tag_attr: str, floor_tag_block: str, floor_tag_attr: str, openings_layer: str, door_mark_layer: str | None, window_mark_layer: str | None, mm_per_unit: float = 1.0)` с методами `to_json() -> str` и `@classmethod from_json(s: str) -> Profile`.
  - `Opening(kind: Literal["door", "window"], mark: str | None, width_m: float, height_m: float, sill_m: float, rooms: list[str], size_source: Literal["spec", "default"], center: tuple[float, float])`.
  - `Zone(from_m: float, to_m: float | None, items: list[str], source_text: str)` — `to_m=None` значит «до высоты H».
  - `FinishSpec(ceiling_items: list[str], ceiling_height_m: float | None, walls: list[Zone], floor_items: list[str], floor_upturn_m: float, source: dict[str, str])` — `source` хранит исходные тексты по ключам `"ceiling"`, `"walls"`, `"floor"`.
  - `FloorLayer(material: str, thickness_mm_min: float | None, thickness_mm_max: float | None, count_as: Literal["volume", "area", "piece"], norm_key: str)`.
  - `FloorType(code: str, rooms_label: str, layers: list[FloorLayer], source_text: str)`.
  - `Room(number: str, name: str, floor: str | None, polygon: shapely.Polygon, area_m2: float, perimeter_m: float, floor_type: str | None, finish: FinishSpec | None = None, openings: list[Opening] = [])` (через `field(default_factory=list)`).
  - `MaterialLine(material: str, unit: str, quantity: float, reserve_pct: float, basis: Literal["норма", "чертёж"], rooms: list[str])`.
  - `Issue(code: str, room: str | None, message: str)` — в спецификации это `Warning`; переименовано, чтобы не перекрывать встроенный `Warning`.
- Produces (`numbers.py`): `normalize_room_number(s: str) -> str`, `parse_ru_float(s: str) -> float | None`, `floor_key(title: str) -> str | None`.

- [ ] **Step 1: Создать окружение и `pyproject.toml`**

Зависимости: `ezdxf>=1.4`, `shapely>=2.0`, `openpyxl>=3.1`, `pandas>=2.0`, `matplotlib>=3.8`, `streamlit>=1.40`, `requests>=2.31`, `pyyaml>=6.0`, `python-dotenv>=1.0`; extra `dev`: `pytest>=8`. Сборка setuptools, `package-dir = {"" = "src"}`, `package-data` включает `norms.yaml`. `[tool.pytest.ini_options] testpaths = ["tests"]`, `pythonpath = ["."]`. `.env.example` содержит одну строку `MISTRAL_API_KEY=`.

Run: `python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` и `.venv/Scripts/python.exe -c "import finishcalc"`
Expected: установка без ошибок, импорт без вывода.

- [ ] **Step 2: Написать падающие тесты**

```python
# tests/test_numbers.py
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
```

```python
# tests/test_model.py
from finishcalc.model import Profile

def test_profile_json_roundtrip():
    p = Profile("_AREA", "номерок", "ААА", "тип пола", "ТИП", "_WinDoo", "_обозначения дверей", None, 1.0)
    assert Profile.from_json(p.to_json()) == p
```

- [ ] **Step 3: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_numbers.py tests/test_model.py -v`
Expected: FAIL, `ModuleNotFoundError: finishcalc.numbers`.

- [ ] **Step 4: Реализовать `model.py` и `numbers.py`**

`normalize_room_number`: перевести в верхний регистр, убрать все пробельные символы, заменить латинские `ABCEHKMOPTXY` на кириллические `АВСЕНКМОРТХУ`; строка `"-"` → `""`. `parse_ru_float`: убрать обычные и неразрывные пробелы, запятую заменить точкой, принимать только `^\d+(\.\d+)?$`. `floor_key`: `(\d+)-?го` → цифра; содержит «подвал» → `"подвал"`; иначе `None`.

- [ ] **Step 5: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_numbers.py tests/test_model.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .env.example src/finishcalc tests/test_numbers.py tests/test_model.py
git commit -m "feat: package skeleton, data model and number parsing"
```

---

### Task 2: Синтетический чертёж и извлечение помещений

**Files:**
- Create: `tests/fixtures/__init__.py`, `tests/fixtures/synth.py`, `tests/conftest.py`, `src/finishcalc/extract/__init__.py`, `src/finishcalc/extract/rooms.py`
- Test: `tests/test_extract_rooms.py`

**Interfaces:**
- Consumes: `Profile`, `Room`, `Issue` (Task 1).
- Produces:
  - `tests/fixtures/synth.py`: `SYNTH_PROFILE: Profile`; `make_synthetic_plan(path: Path, names: dict[str, str] | None = None) -> Path` — `names` переименовывает слои, блоки и атрибуты (ключи — значения из `SYNTH_PROFILE`).
  - `tests/conftest.py`: фикстура `synth_doc` (сохраняет чертёж в `tmp_path` и открывает `ezdxf.readfile`).
  - `extract_rooms(doc: ezdxf.document.Drawing, profile: Profile) -> tuple[list[Room], list[Issue]]` — у `Room` заполнены `number`, `polygon` (м), `area_m2`, `perimeter_m`, `floor_type`; `name=""`, `floor=None`.

- [ ] **Step 1: Написать генератор синтетического чертежа**

`SYNTH_PROFILE = Profile("_AREA", "номерок", "ААА", "тип пола", "ТИП", "_WinDoo", "_обозначения дверей", "_обозначения проемов", 1.0)`. DXF R2018, `$INSUNITS = 4`. Блоки: `номерок` (ATTDEF `ААА`), `тип пола` (ATTDEF `ТИП`), `door_test` (отрезок (0,0)–(900,0) и дуга с центром (0,0), r = 900, 0°–90°), `win_test` (прямоугольник 1500 × 100). Координаты в мм:

| Объект | Слой/блок | Геометрия |
|---|---|---|
| А01 | `_AREA` | прямоугольник (0,0)–(4000,5000) |
| дубль А01 | `_AREA` | тот же прямоугольник ещё раз |
| А02 | `_AREA` | прямоугольник (4200,0)–(8200,5000) |
| А03 | `_AREA` | (0,6000) → (4000,6000) → (4000,9000, bulge = 1) → (0,9000), замкнут: прямоугольник 4 × 3 м с полукругом r = 2 м наружу сверху |
| слитный контур | `_AREA` | прямоугольник (9000,6000)–(12000,9000) |
| шахта | `_AREA` | прямоугольник (9000,0)–(10000,1000) |
| контур этажа | `_AREA` | прямоугольник (−500,−500)–(8700,5500) |
| номера | `номерок` | А01 (2000,2500), А02 (6200,2500), А03 (2000,7500), А04 (10000,7500), А05 (11000,7500), А99 (20000,20000) |
| типы пола | `тип пола` | «7» (1000,1000), «2» (5000,1000), «7*» (1000,7000) |
| дверь с маркой | `door_test` на `_WinDoo` | вставка (4100,2050), поворот 90° |
| марка двери | MTEXT на `_обозначения дверей` | «ДС-3» в (4100,3100) |
| окно | `win_test` на `_WinDoo` | вставка (0,1500), поворот 90° |
| марка окна | MTEXT на `_обозначения проемов` | «3» в (−600,2250) |
| дверь без марки | `door_test` на `_WinDoo` | вставка (8200,3000), поворот 90° |

- [ ] **Step 2: Написать падающие тесты**

```python
# tests/test_extract_rooms.py
import math, pytest
from finishcalc.extract.rooms import extract_rooms
from tests.fixtures.synth import SYNTH_PROFILE

def test_rooms_found(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    by = {r.number: r for r in rooms}
    assert set(by) == {"А01", "А02", "А03"}
    assert by["А01"].area_m2 == pytest.approx(20.0, abs=0.01)
    assert by["А01"].perimeter_m == pytest.approx(18.0, abs=0.01)
    assert by["А01"].floor_type == "7"
    assert by["А02"].floor_type == "2"
    assert by["А03"].floor_type == "7*"

def test_bulge_room_area(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    a03 = next(r for r in rooms if r.number == "А03")
    assert a03.area_m2 == pytest.approx(12 + math.pi * 2**2 / 2, rel=0.005)

def test_duplicate_contour_counted_once(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    assert [r.number for r in rooms].count("А01") == 1

def test_issues(synth_doc):
    _, issues = extract_rooms(synth_doc, SYNTH_PROFILE)
    codes = sorted(i.code for i in issues)
    assert codes == ["contour_with_several_numbers", "contour_without_number",
                     "contour_without_number", "number_without_contour"]
    assert next(i for i in issues if i.code == "number_without_contour").room == "А99"
```

- [ ] **Step 3: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_extract_rooms.py -v`
Expected: FAIL, `ModuleNotFoundError: finishcalc.extract.rooms`.

- [ ] **Step 4: Реализовать `extract_rooms`**

Алгоритм:
1. Контуры: `LWPOLYLINE` и `POLYLINE` на `profile.room_contour_layer` → `ezdxf.path.make_path(e).flattening(distance=1.0)` → `Polygon` (невалидный — `buffer(0)`), масштаб `profile.mm_per_unit / 1000`.
2. Дубли: контур отбрасывается, если уже принятый контур отличается от него по симметрической разности меньше чем на 1% площади.
3. Номера и типы пола: вставки блоков, значение атрибута с нужным тегом, точка вставки в метрах.
4. Каждый номер и каждый тип пола относятся к **наименьшему** по площади контуру, который их содержит.
5. Контур без номеров → `contour_without_number`; с 2+ номерами → `contour_with_several_numbers`, помещение не создаётся; номер вне контуров → `number_without_contour` (`room` = номер).
6. Тип пола: один → он; 2+ разных → `None` и `several_floor_types`.
7. Номера нормализуются через `normalize_room_number`.

- [ ] **Step 5: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_extract_rooms.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures tests/conftest.py tests/test_extract_rooms.py src/finishcalc/extract
git commit -m "feat: extract rooms from contours, number and floor-type tags"
```

---

### Task 3: Таблицы AutoCAD

**Files:**
- Create: `src/finishcalc/extract/tables.py`
- Test: `tests/test_tables.py`

**Interfaces:**
- Consumes: `normalize_room_number`, `parse_ru_float`, `floor_key` (Task 1).
- Produces (`@dataclass`):
  - `RawTable(rows: list[list[str]], insert: tuple[float, float])`.
  - `ClassifiedTable(kind: Literal["finish_schedule", "room_explication", "floor_explication", "openings_spec", "other"], title: str, rows: list[list[str]], insert: tuple[float, float])`.
  - `ExplRow(number: str, name: str, area_m2: float | None, floor: str | None)`.
  - `FinishRow(number: str, name: str, ceiling: str, walls: str, floor: str, floor_area_m2: float | None, note: str)`.
  - `FloorTypeRow(code: str, rooms_label: str, layers_text: str)`.
- Produces (функции): `read_tables(doc) -> list[RawTable]`, `classify_tables(raw: list[RawTable]) -> list[ClassifiedTable]`, `parse_room_explication(t: ClassifiedTable) -> list[ExplRow]`, `parse_finish_schedule(t: ClassifiedTable) -> list[FinishRow]`, `parse_floor_explication(t: ClassifiedTable) -> list[FloorTypeRow]`, `parse_openings_spec(t: ClassifiedTable) -> dict[str, tuple[float, float]]` (марка → (ширина м, высота м)).

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_tables.py
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
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tables.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Реализовать `tables.py`**

- `read_tables`: `doc.modelspace().query("ACAD_TABLE")` → `ezdxf.entities.acad_table.read_acad_table_content(t)`; каждая ячейка — `plain_mtext(str(c), split=False)` со схлопнутыми пробелами; `insert` из `t.dxf.insert`.
- Классификация по склеенному тексту первых двух строк без учёта регистра: «ведомость отделки» → `finish_schedule`; «экспликация помещений» → `room_explication`; «экспликация полов» → `floor_explication`; «спецификация заполнения проем» → `openings_spec`; иначе `other`. `title` — первая непустая ячейка первой строки.
- Продолжение: таблица `other` получает вид и `title` ближайшей слева классифицированной таблицы с тем же числом колонок и той же координатой Y (разница ≤ 1.0 единицы чертежа).
- Экспликация помещений: пропустить строки шапки (содержат «Наименование»), строки разделов (пустая вторая ячейка), «Всего» и «в т.ч.»; номер — нормализованный; этаж — `floor_key(title)`.
- Ведомость: колонки 0–8 как в `FinishRow`; строки без номера, со «-» в номере и строки шапки (содержат «Потолок» или «Наименование помещения») пропускаются.
- Экспликация полов: колонка 1 — код, 0 — `rooms_label`, 3 — `layers_text`; строки без кода пропускаются.
- Спецификация проёмов: марка из колонки 0 (нормализованная), ячейки со 2-й по 11-ю склеиваются через пробел, размеры — регулярное выражение `(\d{2}(?:[.,]\d)?)\s*-\s*(\d{1,2}(?:[.,]\d)?)`: высота и ширина в дециметрах → метры.

`read_tables` не покрыт модульными тестами (ezdxf не умеет создавать `ACAD_TABLE`); его проверяет приёмка в Task 14.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tables.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/finishcalc/extract/tables.py tests/test_tables.py
git commit -m "feat: read and parse AutoCAD tables"
```

---

### Task 4: Проёмы

**Files:**
- Create: `src/finishcalc/extract/openings.py`
- Test: `tests/test_openings.py`

**Interfaces:**
- Consumes: `Profile`, `Room`, `Opening`, `Issue` (Task 1); `extract_rooms` (Task 2); формат спецификации `dict[str, tuple[float, float]]` (Task 3).
- Produces: `extract_openings(doc, profile: Profile, rooms: list[Room], spec: dict[str, tuple[float, float]]) -> tuple[list[Opening], list[Issue]]`; `lookup_size(mark: str, spec: dict[str, tuple[float, float]]) -> tuple[float, float] | None`.

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_openings.py
from finishcalc.extract.rooms import extract_rooms
from finishcalc.extract.openings import extract_openings, lookup_size
from tests.fixtures.synth import SYNTH_PROFILE

SPEC = {"ДС-3": (1.0, 2.15)}

def test_openings(synth_doc):
    rooms, _ = extract_rooms(synth_doc, SYNTH_PROFILE)
    ops, issues = extract_openings(synth_doc, SYNTH_PROFILE, rooms, SPEC)
    door = next(o for o in ops if o.mark == "ДС-3")
    assert (door.kind, door.width_m, door.height_m, door.sill_m, door.size_source) == ("door", 1.0, 2.15, 0.0, "spec")
    assert sorted(door.rooms) == ["А01", "А02"]
    win = next(o for o in ops if o.kind == "window")
    assert (win.mark, win.width_m, win.height_m, win.sill_m, win.rooms, win.size_source) == ("3", 1.5, 1.5, 0.9, ["А01"], "default")
    nomark = next(o for o in ops if o.mark is None)
    assert (nomark.kind, nomark.width_m, nomark.height_m, nomark.rooms) == ("door", 0.9, 2.1, ["А02"])
    assert sorted(i.code for i in issues) == ["opening_mark_not_in_spec", "opening_without_mark"]

def test_lookup_size_left_variant():
    assert lookup_size("ДС-3л", SPEC) == (1.0, 2.15)
    assert lookup_size("Д-9", SPEC) is None
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_openings.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Реализовать `extract_openings` и `lookup_size`**

Для каждой вставки на `profile.openings_layer`: габарит `ezdxf.bbox.extents([insert], fast=True)` в метрах, центр габарита. Ближайшая марка (MTEXT на `door_mark_layer` или `window_mark_layer`) в радиусе 1.5 м задаёт вид и марку; без марки вид определяется по короткой стороне габарита: > 0.3 м — дверь, иначе окно. Размер: `lookup_size` (точная марка, затем без конечной «л») → `"spec"`; иначе значения по умолчанию из Global Constraints → `"default"`. Предупреждения: нет марки → `opening_without_mark`; марка есть, но не в спецификации → `opening_mark_not_in_spec`. Помещения — те, у которых `polygon.distance(bbox_polygon) <= 0.3`.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_openings.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/finishcalc/extract/openings.py tests/test_openings.py
git commit -m "feat: detect openings, marks and sizes"
```

---

### Task 5: Клиенты ИИ и кэш

**Files:**
- Create: `src/finishcalc/llm.py`, `tests/fakes.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces:
  - `class LLM(Protocol): def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict`.
  - `class LLMError(Exception)`.
  - `MistralLLM(api_key: str, model: str = "ministral-14b-latest", base_url: str = "https://api.mistral.ai/v1", timeout: float = 60)`.
  - `OllamaLLM(model: str = "qwen3.5", base_url: str = "http://localhost:11434", timeout: float = 300)`.
  - `FallbackLLM(primary: LLM, fallback: LLM)` с атрибутами `primary`, `fallback`: при `LLMError` основного вызывает запасной.
  - `CachedLLM(inner: LLM, path: Path, model_id: str)` с атрибутом `inner`.
  - `make_llm(env: Mapping[str, str], cache_dir: Path) -> CachedLLM` — с ключом `CachedLLM(FallbackLLM(MistralLLM, OllamaLLM))`, без ключа `CachedLLM(OllamaLLM)`.
  - `tests/fakes.py`: `FakeLLM(responses: dict | Callable[[str, str, dict, str], dict])` со счётчиком `calls`; если `responses` — словарь по `name`, возвращает `responses[name]`.

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_llm.py
import pytest, requests
from finishcalc import llm as L
from tests.fakes import FakeLLM

class Resp:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b

def test_cached_llm_calls_inner_once(tmp_path):
    inner = FakeLLM({"t": {"items": ["paint_wd"]}})
    c = L.CachedLLM(inner, tmp_path / "llm.json", model_id="m")
    assert c.complete_json("s", "u", {"type": "object"}, "t") == {"items": ["paint_wd"]}
    assert c.complete_json("s", "u", {"type": "object"}, "t") == {"items": ["paint_wd"]}
    assert inner.calls == 1
    again = L.CachedLLM(FakeLLM(lambda *a: 1 / 0), tmp_path / "llm.json", model_id="m")
    assert again.complete_json("s", "u", {"type": "object"}, "t") == {"items": ["paint_wd"]}

def test_mistral_request(monkeypatch):
    seen = {}
    def post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers)
        return Resp(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})
    monkeypatch.setattr(L.requests, "post", post)
    out = L.MistralLLM("k").complete_json("s", "u", {"type": "object"}, "roles")
    assert out == {"ok": True}
    assert seen["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert seen["json"]["model"] == "ministral-14b-latest" and seen["json"]["temperature"] == 0
    rf = seen["json"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and rf["json_schema"]["name"] == "roles"
    assert seen["headers"]["Authorization"] == "Bearer k"

def test_mistral_retries_429_then_raises(monkeypatch):
    calls = []
    monkeypatch.setattr(L.requests, "post", lambda *a, **k: calls.append(1) or Resp(429, {"message": "Rate limit exceeded"}))
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    with pytest.raises(L.LLMError, match="429"):
        L.MistralLLM("k").complete_json("s", "u", {}, "t")
    assert len(calls) == 3

def test_mistral_network_error_becomes_llmerror(monkeypatch):
    def boom(*a, **k): raise requests.ConnectionError("offline")
    monkeypatch.setattr(L.requests, "post", boom)
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    with pytest.raises(L.LLMError):
        L.MistralLLM("k").complete_json("s", "u", {}, "t")

def test_ollama_request(monkeypatch):
    seen = {}
    def post(url, json, timeout):
        seen.update(url=url, json=json)
        return Resp(200, {"message": {"content": '{"ok": 1}'}})
    monkeypatch.setattr(L.requests, "post", post)
    assert L.OllamaLLM().complete_json("s", "u", {"type": "object"}, "t") == {"ok": 1}
    assert seen["url"] == "http://localhost:11434/api/chat"
    assert seen["json"]["format"] == {"type": "object"} and seen["json"]["think"] is False
    assert seen["json"]["stream"] is False and seen["json"]["options"]["temperature"] == 0

def test_make_llm_without_key_uses_ollama(tmp_path):
    assert isinstance(L.make_llm({}, tmp_path).inner, L.OllamaLLM)
    with_key = L.make_llm({"MISTRAL_API_KEY": "k"}, tmp_path).inner
    assert isinstance(with_key, L.FallbackLLM)
    assert isinstance(with_key.primary, L.MistralLLM) and isinstance(with_key.fallback, L.OllamaLLM)

def test_fallback_used_on_llmerror():
    def fail(*a): raise L.LLMError("429")
    assert L.FallbackLLM(FakeLLM(fail), FakeLLM({"t": {"ok": 1}})).complete_json("s", "u", {}, "t") == {"ok": 1}
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm.py -v`
Expected: FAIL, `ModuleNotFoundError: finishcalc.llm`.

- [ ] **Step 3: Реализовать `llm.py` и `tests/fakes.py`**

Mistral: до 3 попыток; при 429, 5xx и `requests.RequestException` пауза `time.sleep(2 * попытка)`; после третьей — `LLMError` с кодом и текстом ответа. Ответ: `json.loads(choices[0].message.content)`. Кэш: ключ — sha256 от `json.dumps([name, model_id, system, user, schema], ensure_ascii=False, sort_keys=True)`, файл читается при создании и перезаписывается после каждой новой записи. `make_llm`: ключ есть → `MistralLLM`, `model_id = "ministral-14b-latest"`; нет → `OllamaLLM`, `model_id = "qwen3.5"`; файл кэша `cache_dir / "llm.json"`.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/finishcalc/llm.py tests/fakes.py tests/test_llm.py
git commit -m "feat: Mistral and Ollama clients with disk cache"
```

---

### Task 6: Справочник норм и разбор описаний отделки

**Files:**
- Create: `src/finishcalc/norms.yaml`, `src/finishcalc/norms.py`, `src/finishcalc/finishes.py`
- Test: `tests/test_norms.py`, `tests/test_finishes.py`

**Interfaces:**
- Consumes: `LLM` (Task 5), `FinishSpec`, `Zone`, `FloorType`, `FloorLayer` (Task 1), `FinishRow`, `FloorTypeRow` (Task 3).
- Produces:
  - `Norm(key: str, title: str, unit: Literal["м²", "м³", "кг", "л"], per_m2: float | None, coats: int, reserve_pct: float)`; `load_norms(path: Path | None = None) -> dict[str, Norm]` (по умолчанию — `norms.yaml` пакета); `save_norms(norms: dict[str, Norm], path: Path) -> None`.
  - `UNSPECIFIED = "не указано в ведомости"`.
  - `split_zones(text: str) -> list[tuple[float, float | None, str]]`, `ceiling_height(text: str) -> float | None`, `floor_upturn(text: str) -> float`.
  - `map_items(text: str, norms: dict[str, Norm], llm: LLM) -> list[str]` (ключи справочника или `"other"`).
  - `parse_finish(row: FinishRow, norms, llm) -> FinishSpec`, `parse_floor_type(row: FloorTypeRow, norms, llm) -> FloorType`, `suggest_finish(room_name: str, norms, llm) -> FinishSpec`.

- [ ] **Step 1: Создать `norms.yaml`**

Значения из спецификации (раздел 7), предварительные, ровно такие:

| key | title | unit | per_m2 | coats | reserve_pct |
|---|---|---|---|---|---|
| plaster_gypsum | Штукатурка гипсовая (слой 10 мм) | кг | 8.5 | 1 | 0 |
| putty | Шпаклёвка | кг | 1.0 | 1 | 0 |
| primer | Грунтовка | л | 0.15 | 1 | 0 |
| paint_wd | Краска водно-дисперсионная | л | 0.15 | 2 | 0 |
| porcelain_tile | Керамогранит | м² | null | 1 | 10 |
| tile_adhesive | Клей плиточный | кг | 5.0 | 1 | 0 |
| suspended_ceiling | Подвесной потолок | м² | null | 1 | 5 |
| laminate | Ламинат | м² | null | 1 | 5 |
| roll_waterproofing | Рулонная гидроизоляция | м² | null | 1 | 15 |
| screed | Стяжка | м³ | null | 1 | 0 |

- [ ] **Step 2: Написать падающие тесты**

```python
# tests/test_norms.py
from finishcalc.norms import load_norms, save_norms

def test_defaults_and_roundtrip(tmp_path):
    n = load_norms()
    assert n["paint_wd"].per_m2 == 0.15 and n["paint_wd"].coats == 2
    assert n["porcelain_tile"].reserve_pct == 10 and n["screed"].unit == "м³"
    save_norms(n, tmp_path / "n.yaml")
    assert load_norms(tmp_path / "n.yaml") == n
```

```python
# tests/test_finishes.py
from finishcalc.finishes import (UNSPECIFIED, split_zones, ceiling_height, floor_upturn,
    map_items, parse_finish, parse_floor_type, suggest_finish)
from finishcalc.extract.tables import FinishRow, FloorTypeRow
from finishcalc.norms import load_norms
from tests.fakes import FakeLLM

N = load_norms()
WALLS = "выше 2100мм. -штукатурка, окраска. Ниже 1800мм.- настенный керамогранит"

def test_split_zones():
    assert split_zones(WALLS) == [(0.0, 1.8, "настенный керамогранит"), (1.8, 2.1, UNSPECIFIED), (2.1, None, "штукатурка, окраска")]
    assert split_zones("Штукатурка, окраска ВД") == [(0.0, None, "Штукатурка, окраска ВД")]

def test_ceiling_height():
    assert ceiling_height("Подвесной потолок. h=3,0м") == 3.0
    assert ceiling_height("Подвесной потолок h=2,5-3,0м") == 3.0
    assert ceiling_height("Окраска по шпатлевке") is None

def test_floor_upturn():
    assert floor_upturn("Керамогранит, завести на стену на 300мм.") == 0.3
    assert floor_upturn("Ламинат") == 0.0

def test_map_items_drops_unknown_keys():
    llm = FakeLLM({"finish_items": {"items": ["plaster_gypsum", "bogus", "paint_wd"]}})
    assert map_items("штукатурка, окраска", N, llm) == ["plaster_gypsum", "other", "paint_wd"]

def test_parse_finish():
    by_text = {"Подвесной потолок h=3,0м": ["suspended_ceiling"], "настенный керамогранит": ["porcelain_tile", "tile_adhesive"],
               "штукатурка, окраска": ["plaster_gypsum", "primer", "paint_wd"], "Керамогранит, завести на стену на 300мм.": ["porcelain_tile", "tile_adhesive"]}
    llm = FakeLLM(lambda s, u, sch, name: {"items": next(v for k, v in by_text.items() if k in u)})
    f = parse_finish(FinishRow("А01", "Холл", "Подвесной потолок h=3,0м", WALLS, "Керамогранит, завести на стену на 300мм.", 20.0, ""), N, llm)
    assert f.ceiling_items == ["suspended_ceiling"] and f.ceiling_height_m == 3.0 and f.floor_upturn_m == 0.3
    assert [(z.from_m, z.to_m, z.items) for z in f.walls] == [(0.0, 1.8, ["porcelain_tile", "tile_adhesive"]), (1.8, 2.1, []), (2.1, None, ["plaster_gypsum", "primer", "paint_wd"])]
    assert f.walls[1].source_text == UNSPECIFIED

def test_parse_floor_type():
    llm = FakeLLM({"floor_layers": {"layers": [
        {"material": "Керамогранит", "thickness_mm_min": 20, "thickness_mm_max": 20, "count_as": "area", "norm_key": "porcelain_tile"},
        {"material": "Стяжка ЦПР", "thickness_mm_min": 50, "thickness_mm_max": 95, "count_as": "volume", "norm_key": "screed"},
        {"material": "Плита перекрытия", "thickness_mm_min": 0, "thickness_mm_max": 0, "count_as": "piece", "norm_key": "other"}]}})
    ft = parse_floor_type(FloorTypeRow("7", "Холлы", "1. Керамогранит - 20мм | 2. Стяжка ЦПР - 50-95мм"), N, llm)
    assert ft.code == "7" and [l.norm_key for l in ft.layers] == ["porcelain_tile", "screed", "other"]
    assert (ft.layers[1].thickness_mm_min, ft.layers[1].thickness_mm_max) == (50, 95)
    assert ft.layers[2].thickness_mm_min is None

def test_suggest_finish_single_zone():
    llm = FakeLLM({"suggest_finish": {"ceiling": ["primer", "paint_wd"], "walls": ["plaster_gypsum", "paint_wd"], "floor": ["laminate"]}})
    f = suggest_finish("Кабинет", N, llm)
    assert [(z.from_m, z.to_m) for z in f.walls] == [(0.0, None)] and f.floor_items == ["laminate"]
```

- [ ] **Step 3: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_norms.py tests/test_finishes.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 4: Реализовать `norms.py` и `finishes.py`**

- `split_zones`: ищет «выше N мм» и «ниже M мм» (без учёта регистра, N и M — целые миллиметры); текст зоны — то, что идёт после маркера до следующего маркера, без ведущих и концевых `.-–— ` и пробелов. Есть оба маркера → `[0, M]`, при `M < N` ещё `[M, N]` с `UNSPECIFIED`, затем `[N, None]`; нет маркеров → одна зона `[0, None]`.
- `ceiling_height`: `h\s*=\s*(\d+(?:[.,]\d+)?)(?:\s*-\s*(\d+(?:[.,]\d+)?))?\s*м` → максимум из найденных значений.
- `floor_upturn`: `завести на стену на (\d+)\s*мм` → мм / 1000, иначе 0.0.
- `map_items`: `name="finish_items"`, схема `{"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string", "enum": [*ключи, "other"]}}}, "required": ["items"], "additionalProperties": false}`. Системный промпт по-русски: «Ты сметчик. Разложи описание отделки на позиции справочника в порядке слоёв» + список `ключ — название` + правила: «штукатурка» → `plaster_gypsum`; «улучшенная штукатурка» или «по шпатлевке» → добавить `putty`; «окраска» → `primer`, `paint_wd`; «керамогранит» или «плитка» → `porcelain_tile`, `tile_adhesive`; «подвесной потолок», «Armstrong» → `suspended_ceiling`; «ламинат» → `laminate`; «гидроизоляция», «изопласт» → `roll_waterproofing`; «стяжка» → `screed`; не подходит ничего → `other`. Ключи вне справочника заменяются на `"other"`. Для зоны `UNSPECIFIED` ИИ не вызывается, `items = []`.
- `parse_floor_type`: `name="floor_layers"`, схема — массив объектов с полями из теста (`count_as` — enum `volume|area|piece`, `norm_key` — enum ключей + `other`, толщины — `number`); толщина `0` → `None`.
- `suggest_finish`: `name="suggest_finish"`, свойства `ceiling`, `walls`, `floor` — массивы ключей; стены — одна зона `[0, None]`, `source` — `{"walls": "предложено ИИ", ...}`.

- [ ] **Step 5: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_norms.py tests/test_finishes.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/finishcalc/norms.yaml src/finishcalc/norms.py src/finishcalc/finishes.py tests/test_norms.py tests/test_finishes.py
git commit -m "feat: norms catalogue and finish description parsing"
```

---

### Task 7: Расчёт площадей и материалов

**Files:**
- Create: `src/finishcalc/calc.py`
- Test: `tests/test_calc.py`

**Interfaces:**
- Consumes: `Room`, `Opening`, `FinishSpec`, `Zone`, `FloorType`, `FloorLayer`, `MaterialLine`, `Issue` (Task 1); `Norm` (Task 6).
- Produces:
  - `ZoneArea(from_m: float, to_m: float, area_m2: float, items: list[str], unspecified: bool, source_text: str)`.
  - `RoomAreas(height_m: float, floor_m2: float, ceiling_m2: float, walls_m2: float, upturn_m2: float, zones: list[ZoneArea])`.
  - `overlap(a0: float, a1: float, b0: float, b1: float) -> float`.
  - `room_areas(room: Room, default_height_m: float) -> RoomAreas`.
  - `aggregate_materials(rooms: list[Room], areas: dict[str, RoomAreas], floor_types: dict[str, FloorType], norms: dict[str, Norm]) -> tuple[list[MaterialLine], list[Issue]]`.
  - `floor_type_areas(rooms: list[Room], areas: dict[str, RoomAreas]) -> dict[str, float]`.

- [ ] **Step 1: Написать падающие тесты**

Пример: помещение 4 × 5 м (P = 18, S = 20), H = 3.0, дверь 0.9 × 2.1, окно 1.5 × 1.5 с подоконником 0.9, заход на стену 0.3, зоны [0; 1.8] плитка, [1.8; 2.1] не указано, [2.1; H] окраска.

```python
# tests/test_calc.py
import pytest
from shapely.geometry import box
from finishcalc.model import Room, Opening, FinishSpec, Zone, FloorType, FloorLayer
from finishcalc.calc import room_areas, aggregate_materials, floor_type_areas
from finishcalc.finishes import UNSPECIFIED
from finishcalc.norms import load_norms

def make_room(finish=None, floor_type="7"):
    door = Opening("door", "ДС-2", 0.9, 2.1, 0.0, ["Т1"], "spec", (0, 0))
    win = Opening("window", "3", 1.5, 1.5, 0.9, ["Т1"], "default", (0, 0))
    return Room("Т1", "Тест", "1", box(0, 0, 4, 5), 20.0, 18.0, floor_type, finish, [door, win])

FINISH = FinishSpec(["suspended_ceiling"], None,
    [Zone(0.0, 1.8, ["porcelain_tile", "tile_adhesive"], "плитка"), Zone(1.8, 2.1, [], UNSPECIFIED),
     Zone(2.1, None, ["primer", "paint_wd"], "окраска")], ["porcelain_tile", "tile_adhesive"], 0.3, {})

def test_room_areas_worked_example():
    a = room_areas(make_room(FINISH), default_height_m=3.0)
    assert (a.floor_m2, a.ceiling_m2, a.height_m) == (20.0, 20.0, 3.0)
    assert a.upturn_m2 == pytest.approx(5.13)
    assert [round(z.area_m2, 2) for z in a.zones] == [24.30, 4.68, 15.75]
    assert a.zones[1].unspecified is True
    assert a.walls_m2 == pytest.approx(44.73)

def test_ceiling_height_overrides_default():
    f = FinishSpec([], 2.5, [Zone(0.0, None, [], "x")], [], 0.0, {})
    assert room_areas(make_room(f), default_height_m=3.0).height_m == 2.5

def test_room_without_finish_single_zone():
    a = room_areas(make_room(None), default_height_m=3.0)
    assert len(a.zones) == 1 and a.walls_m2 == pytest.approx(54 - 1.89 - 2.25)

def test_materials():
    room = make_room(FINISH)
    areas = {"Т1": room_areas(room, 3.0)}
    ft = {"7": FloorType("7", "", [FloorLayer("Керамогранит", 20, 20, "area", "porcelain_tile"),
                                   FloorLayer("Стяжка", 50, 50, "volume", "screed")], "")}
    lines, issues = aggregate_materials([room], areas, ft, load_norms())
    q = {l.material: (l.unit, l.quantity) for l in lines}
    assert q["Подвесной потолок"] == ("м²", 21.0)
    assert q["Керамогранит"] == ("м²", 54.4)
    assert q["Клей плиточный"] == ("кг", 121.5)
    assert q["Грунтовка"] == ("л", 2.4)
    assert q["Краска водно-дисперсионная"] == ("л", 4.7)
    assert q["Стяжка"] == ("м³", 1.0)
    assert [i.code for i in issues] == ["unspecified_wall_band"]

def test_floor_layer_without_thickness():
    room = make_room(FINISH)
    ft = {"7": FloorType("7", "", [FloorLayer("Стяжка", None, None, "volume", "screed")], "")}
    _, issues = aggregate_materials([room], {"Т1": room_areas(room, 3.0)}, ft, load_norms())
    assert "floor_layer_without_thickness" in [i.code for i in issues]

def test_floor_type_areas():
    room = make_room(FINISH)
    assert floor_type_areas([room], {"Т1": room_areas(room, 3.0)}) == {"7": 20.0}
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calc.py -v`
Expected: FAIL, `ModuleNotFoundError: finishcalc.calc`.

- [ ] **Step 3: Реализовать `calc.py`**

Формулы — раздел 7 спецификации. H = `finish.ceiling_height_m` или `default_height_m`. Заход: `(P − Σ ширин дверей) × floor_upturn_m`, вычитается из зоны, начинающейся с 0. `walls_m2` = сумма площадей зон. Без отделки — одна зона [0; H] без позиций.

Материалы:
- потолок: каждый ключ × `ceiling_m2`;
- стены: каждый ключ зоны × площадь зоны;
- пол: если у помещения есть тип пола с разобранными слоями — слои (`volume` → `floor_m2 × (min + max) / 2 / 1000` м³; `area` → `floor_m2`, у первого слоя плюс `upturn_m2`; `piece` пропускается); иначе ключи `floor_items` × (`floor_m2` + `upturn_m2`).
- Количество по норме: единица м² → площадь × (1 + запас/100); м³ → объём × (1 + запас/100); кг и л → площадь × `per_m2` × `coats` × (1 + запас/100); `basis = "норма"`.
- Ключ `"other"` или нет в справочнике: `material` = исходный текст (не длиннее 60 символов), единица м², `basis = "чертёж"`.
- Агрегация по паре (`material`, `unit`), округление по Global Constraints в конце.
- Зона `unspecified` с площадью > 0 → одно `unspecified_wall_band` на помещение (в сообщении — площадь); слой `volume` без толщины → `floor_layer_without_thickness`.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_calc.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/finishcalc/calc.py tests/test_calc.py
git commit -m "feat: wall zone, floor and ceiling areas and material totals"
```

---

### Task 8: Профиль проекта

**Files:**
- Create: `src/finishcalc/profile.py`
- Modify: `tests/fixtures/synth.py` (если переименование через `names` ещё не покрывает слои меток)
- Test: `tests/test_profile.py`

**Interfaces:**
- Consumes: `Profile` (Task 1), `LLM` (Task 5), `make_synthetic_plan`, `SYNTH_PROFILE` (Task 2).
- Produces: `summarize_drawing(doc) -> dict` (`{"layers": [{"name", "types": {тип: число}, "samples": [до 3 текстов]}], "blocks": [{"name", "count", "attribs": [теги], "samples": [до 5 значений]}], "insunits": int}`); `detect_profile(doc) -> tuple[dict, list[str]]` (найденные поля `Profile` и список нерешённых); `refine_profile(summary: dict, found: dict, unresolved: list[str], llm: LLM) -> Profile`; `class ProfileError(Exception)` с атрибутом `fields: list[str]`; `save_profile(p: Profile, path: Path)`, `load_profile(path: Path) -> Profile`.

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_profile.py
import ezdxf, pytest
from dataclasses import asdict
from finishcalc.profile import summarize_drawing, detect_profile, refine_profile, ProfileError
from tests.fixtures.synth import make_synthetic_plan, SYNTH_PROFILE
from tests.fakes import FakeLLM

RENAMED = {"_AREA": "L1", "номерок": "B1", "ААА": "N", "тип пола": "B2", "ТИП": "T"}

def test_detect_profile_by_rules(synth_doc):
    found, unresolved = detect_profile(synth_doc)
    assert unresolved == []
    assert found == asdict(SYNTH_PROFILE)

def test_refine_with_llm(tmp_path):
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "r.dxf", RENAMED))
    found, unresolved = detect_profile(doc)
    assert {"room_contour_layer", "room_tag_block", "floor_tag_block"} <= set(unresolved)
    llm = FakeLLM({"profile": {"room_contour_layer": "L1", "room_tag_block": "B1", "room_tag_attr": "N",
                               "floor_tag_block": "B2", "floor_tag_attr": "T"}})
    p = refine_profile(summarize_drawing(doc), found, unresolved, llm)
    assert (p.room_contour_layer, p.room_tag_block, p.floor_tag_attr) == ("L1", "B1", "T")

def test_refine_rejects_unknown_names(tmp_path):
    doc = ezdxf.readfile(make_synthetic_plan(tmp_path / "r.dxf", RENAMED))
    found, unresolved = detect_profile(doc)
    llm = FakeLLM({"profile": {k: "нет_такого" for k in unresolved}})
    with pytest.raises(ProfileError) as e:
        refine_profile(summarize_drawing(doc), found, unresolved, llm)
    assert "room_contour_layer" in e.value.fields
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_profile.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Реализовать `profile.py`**

Правила (без учёта регистра):
- контуры — слой с замкнутыми полилиниями, имя которого совпадает с `area|площ|помещ`;
- номер помещения — блок с одним атрибутом, имя совпадает с `ном|room|пом`;
- тип пола — блок с одним атрибутом, имя совпадает с `пол|floor`;
- проёмы — слой с наибольшим числом `INSERT`, имя совпадает с `win|door|двер|окн|проем`;
- марки дверей — слой MTEXT по `двер`, марки окон — слой MTEXT по `окон|проем` с наибольшим числом MTEXT, без слоя дверей;
- атрибут — единственный тег блока;
- `mm_per_unit` — по `$INSUNITS` (Global Constraints).

Несколько равных кандидатов или ни одного → поле нерешённое. `refine_profile`: `name="profile"`, схема — только нерешённые поля, у каждого `enum` из имён слоёв (для `*_layer`), блоков (для `*_block`) или тегов атрибутов (для `*_attr`) плюс `"none"`; ответ вне кандидатов или `"none"` для обязательного поля → `ProfileError(fields)`. Необязательные: `door_mark_layer`, `window_mark_layer`.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_profile.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/finishcalc/profile.py tests/test_profile.py tests/fixtures/synth.py
git commit -m "feat: project profile detection with rules and AI fallback"
```

---

### Task 9: Конвертация DWG → DXF

**Files:**
- Create: `src/finishcalc/convert.py`
- Test: `tests/test_convert.py`

**Interfaces:**
- Produces: `find_oda(env: Mapping[str, str] = os.environ, search_root: Path = Path(r"C:\Program Files\ODA")) -> Path | None`; `dwg_to_dxf(src: Path, out_dir: Path, oda: Path | None = None) -> Path`; `class ConvertError(Exception)`.

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_convert.py
import ezdxf, pytest
from finishcalc.convert import find_oda, dwg_to_dxf, ConvertError
from tests.fixtures.synth import make_synthetic_plan

def test_dxf_passthrough(tmp_path):
    p = make_synthetic_plan(tmp_path / "a.dxf")
    assert dwg_to_dxf(p, tmp_path / "out") == p

def test_find_oda_env_override(tmp_path):
    exe = tmp_path / "ODAFileConverter.exe"; exe.write_text("")
    assert find_oda({"ODA_FILE_CONVERTER": str(exe)}, tmp_path / "none") == exe

def test_dwg_without_oda_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("finishcalc.convert.find_oda", lambda *a, **k: None)
    (tmp_path / "a.dwg").write_bytes(b"")
    with pytest.raises(ConvertError, match="ODA File Converter"):
        dwg_to_dxf(tmp_path / "a.dwg", tmp_path / "out")

@pytest.mark.skipif(find_oda() is None, reason="ODA File Converter не установлен")
def test_roundtrip_through_dwg(tmp_path):
    from ezdxf.addons import odafc
    src = make_synthetic_plan(tmp_path / "a.dxf")
    dwg = tmp_path / "a.dwg"
    odafc.convert(src, dwg, version="R2018", replace=True)
    out = dwg_to_dxf(dwg, tmp_path / "out")
    assert out.suffix == ".dxf" and len(ezdxf.readfile(out).modelspace().query('LWPOLYLINE[layer=="_AREA"]')) == 7
```

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_convert.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Реализовать `convert.py`**

`find_oda`: переменная `ODA_FILE_CONVERTER`, если файл существует; иначе `search_root.glob("*/ODAFileConverter.exe")`, последняя версия по имени папки. `dwg_to_dxf`: `.dxf` возвращается как есть; для `.dwg` — `oda` или `find_oda()`, не найден → `ConvertError("Не найден ODA File Converter: установите его с opendesign.com/guestfiles/oda_file_converter")`; конвертация — `ezdxf.options.set("odafc-addon", "win_exec_path", str(oda))` и `odafc.convert(src, out_dir / (src.stem + ".dxf"), version="R2018", audit=True, replace=True)`; исключение `odafc` заворачивается в `ConvertError` с текстом ошибки и именем файла.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_convert.py -v`
Expected: 4 passed (на этом компьютере ODA установлен, round-trip выполняется).

- [ ] **Step 5: Commit**

```bash
git add src/finishcalc/convert.py tests/test_convert.py
git commit -m "feat: DWG to DXF conversion via ODA File Converter"
```

---

### Task 10: Сборка в `ProjectResult`

**Files:**
- Create: `src/finishcalc/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: всё из Tasks 1–9.
- Produces:
  - `ProjectResult(rooms: list[Room], areas: dict[str, RoomAreas], finish_rows: dict[str, FinishRow], floor_types: dict[str, FloorType], floor_type_areas: dict[str, float], materials: list[MaterialLine], issues: list[Issue], mode: Literal["A", "B"])`.
  - `load_drawings(paths: list[Path], work_dir: Path) -> list[Drawing]`, `load_tables(docs: list[Drawing]) -> list[ClassifiedTable]`.
  - `analyze(plan_docs: list[Drawing], tables: list[ClassifiedTable], profile: Profile, llm: LLM, norms: dict[str, Norm], floor_heights: dict[str, float], default_height_m: float = 3.0) -> ProjectResult`.

- [ ] **Step 1: Написать падающие тесты**

Таблицы собираются в тесте как `ClassifiedTable` из строк формата Task 3: экспликация 1-го этажа (А01, А02, А03); ведомость (А01: потолок «Подвесной потолок h=3,0м», стены «Штукатурка, окраска», пол «Керамогранит, завести на стену на 300мм.»; А02: потолок «Окраска», стены «Штукатурка, окраска», пол «Керамогранит»; А07 — строка без контура); экспликация полов (тип «2» — слой ламината, `norm_key = "laminate"`); спецификация с «ДС-3» = 1.0 × 2.15. Поддельный ИИ отвечает по `name` и по тексту описания.

```python
# tests/test_pipeline.py (ключевые проверки)
def test_analyze_mode_a(synth_doc, tables_a, fake_llm):
    r = analyze([synth_doc], tables_a, SYNTH_PROFILE, fake_llm, load_norms(), {"1": 2.8})
    assert r.mode == "A"
    a01 = r.areas["А01"]
    assert a01.height_m == 3.0 and a01.ceiling_m2 == pytest.approx(20.0)
    # P×H − дверь ДС-3 − окно по умолчанию − заход: 54 − 2.15 − 2.25 − (18 − 1.0)×0.3
    assert a01.walls_m2 == pytest.approx(44.5)
    assert r.areas["А02"].height_m == 2.8
    codes = [i.code for i in r.issues]
    for c in ["finish_row_without_contour", "room_without_finish_row", "floor_type_mismatch",
              "opening_without_mark", "opening_mark_not_in_spec", "reveals_not_counted"]:
        assert c in codes
    assert next(i for i in r.issues if i.code == "finish_row_without_contour").room == "А07"
    assert codes.count("reveals_not_counted") == 1
    assert {r_.number: r_.name for r_ in r.rooms}["А01"] == "Холл"

def test_analyze_mode_b(synth_doc, tables_no_schedule, fake_llm):
    r = analyze([synth_doc], tables_no_schedule, SYNTH_PROFILE, fake_llm, load_norms(), {})
    assert r.mode == "B"
    assert sum(i.code == "finish_suggested_by_ai" for i in r.issues) == len(r.rooms)
```

Фикстуры `tables_a`, `tables_no_schedule`, `fake_llm` описываются в самом тесте по приведённым данным.

- [ ] **Step 2: Запустить и убедиться, что тесты падают**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Реализовать `pipeline.py`**

Порядок `analyze`:
1. `extract_rooms` по каждому плану.
2. Таблицы по видам; имена и этажи — из экспликации помещений.
3. Спецификация проёмов → `extract_openings`; проёмы раскладываются в `room.openings`.
4. Есть ведомость → режим A: `parse_finish` по строке с тем же номером; нет строки → `room_without_finish_row`; строка без контура → `finish_row_without_contour`. Нет ведомости → режим B: `suggest_finish` и `finish_suggested_by_ai` на каждое помещение.
5. Типы пола: `parse_floor_type` по каждой строке экспликации полов; код с плана, которого нет в экспликации → `floor_type_not_in_explication`.
6. Расхождение: верхний слой типа пола имеет ключ не `"other"`, ключи пола в ведомости не пусты и не содержат этот ключ → `floor_type_mismatch`.
7. `room_areas` с высотой `floor_heights.get(room.floor, default_height_m)`.
8. `aggregate_materials`, `floor_type_areas`.
9. Одно `reveals_not_counted` (`room=None`, «Откосы проёмов не учтены»).

`load_drawings` — `dwg_to_dxf` и `ezdxf.readfile`; `load_tables` — `read_tables` по всем документам и `classify_tables`.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/finishcalc/pipeline.py tests/test_pipeline.py
git commit -m "feat: end-to-end analysis pipeline"
```

---

### Task 11: Выгрузка в Excel

**Files:**
- Create: `src/finishcalc/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `ProjectResult` (Task 10).
- Produces: `export_excel(result: ProjectResult, path: Path) -> Path`.

Колонки листов, ровно так:
- «Ведомость отделки»: `№ п/п`, `Наименование помещения`, `Потолок`, `Площадь потолка, м²`, `Стены/перегородки`, `Площадь стен, м²`, `Напольное покрытие`, `Площадь пола, м²`, `Зоны стен`, `Примечание`. «Зоны стен» — строка вида `0–1,8 м: 24,3 м²; 1,8–2,1 м: 4,7 м² (не указано); 2,1–3,0 м: 15,8 м²`.
- «Экспликация полов»: `Тип пола`, `Помещения`, `Площадь, м²`, `Состав`.
- «Материалы»: `Материал`, `Ед.`, `Количество`, `Запас, %`, `Основание` (для `"чертёж"` — `чертёж (норма не задана)`).
- «По помещениям»: `№`, `Наименование`, `Этаж`, `Тип пола`, `Площадь пола, м²`, `Периметр, м`, `Высота, м`, `Площадь стен, м²`, `Площадь потолка, м²`, `Заход на стену, м²`, `Проёмы`.
- «Проверки»: `Код`, `Помещение`, `Сообщение`.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_export.py
import openpyxl
from finishcalc.export import export_excel

def test_export(tmp_path, small_result):   # small_result — ProjectResult из одного помещения Т1 (как в Task 7)
    p = export_excel(small_result, tmp_path / "out.xlsx")
    wb = openpyxl.load_workbook(p)
    assert wb.sheetnames == ["Ведомость отделки", "Экспликация полов", "Материалы", "По помещениям", "Проверки"]
    ws = wb["Ведомость отделки"]
    assert [c.value for c in ws[1]][:4] == ["№ п/п", "Наименование помещения", "Потолок", "Площадь потолка, м²"]
    assert ws.cell(2, 1).value == "Т1" and ws.cell(2, 6).value == 44.7
    assert "1,8–2,1 м: 4,7 м² (не указано)" in ws.cell(2, 9).value
```

Фикстура `small_result` строится в `tests/conftest.py` из данных Task 7 (`room_areas` и `aggregate_materials`).

- [ ] **Step 2: Запустить и убедиться, что тест падает**

Run: `.venv/Scripts/python.exe -m pytest tests/test_export.py -v`
Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Реализовать `export_excel`** через openpyxl: жирная шапка, закреплённая первая строка, ширина колонок по содержимому (не больше 60), числа округлены по Global Constraints, в строках ведомости — только помещения с отделкой, по возрастанию номера.

- [ ] **Step 4: Запустить тест**

Run: `.venv/Scripts/python.exe -m pytest tests/test_export.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/finishcalc/export.py tests/test_export.py tests/conftest.py
git commit -m "feat: Excel export"
```

---

### Task 12: Картинка плана

**Files:**
- Create: `src/finishcalc/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Room`, `Issue`.
- Produces: `render_plan(rooms: list[Room], issues: list[Issue], out_png: Path) -> Path` — помещение без предупреждений зелёное, с предупреждением оранжевое, номер в центре.

- [ ] **Step 1: Тест** — `render_plan(rooms_from_synth, [Issue("unspecified_wall_band", "А01", "")], tmp_path / "p.png")` возвращает путь, файл существует, размер > 10 КБ.
- [ ] **Step 2: Запустить** — FAIL.
- [ ] **Step 3: Реализовать** на matplotlib с бэкендом Agg, равный масштаб осей, без осей, 150 dpi.
- [ ] **Step 4: Запустить** `.venv/Scripts/python.exe -m pytest tests/test_render.py -v` — 1 passed.
- [ ] **Step 5: Commit** `git add src/finishcalc/render.py tests/test_render.py && git commit -m "feat: plan preview image"`

---

### Task 13: Интерфейс Streamlit

**Files:**
- Create: `app.py`, `.claude/launch.json`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `load_drawings`, `load_tables`, `analyze` (Task 10), `detect_profile`, `refine_profile`, `save_profile`, `load_profile` (Task 8), `make_llm`, `LLMError` (Task 5), `load_norms`, `save_norms` (Task 6), `export_excel` (Task 11), `render_plan` (Task 12).
- Produces: страница с четырьмя шагами — заголовки `1. Загрузка`, `2. Профиль`, `3. Параметры`, `4. Результат`. Переменная окружения `FINISHCALC_DEMO_DIR`: если папка существует, на шаге 1 есть кнопка «Открыть демо-проект», загружающая все DWG/DXF из неё (планы — файлы с «ПЛАН» в имени, остальные — общие данные).

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_app.py
from streamlit.testing.v1 import AppTest

def test_app_starts():
    at = AppTest.from_file("app.py", default_timeout=60).run()
    assert not at.exception
    assert "1. Загрузка" in [h.value for h in at.header]

def test_app_shows_llm_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FINISHCALC_DEMO_DIR", str(tmp_path))   # пустая демо-папка
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    at = AppTest.from_file("app.py", default_timeout=60).run()
    at.session_state["llm_error"] = "Mistral: 429 Rate limit exceeded"
    at.run()
    assert not at.exception
    assert any("429" in e.value for e in at.error)
```

- [ ] **Step 2: Запустить** `.venv/Scripts/python.exe -m pytest tests/test_app.py -v` — FAIL (нет `app.py`).
- [ ] **Step 3: Реализовать `app.py`**

`load_dotenv()` в начале. Шаг 1: `st.file_uploader` (несколько файлов, `dwg`, `dxf`) для планов и для общих данных → файлы во временную папку → `load_drawings`. Шаг 2: `detect_profile`, нерешённое — `refine_profile`; профиль в `st.selectbox` по каждому полю, кнопка «Сохранить профиль»; картинка `render_plan`. Шаг 3: `st.number_input` высоты для каждого найденного этажа (по умолчанию 3.0), справочник норм в `st.data_editor`. Шаг 4: кнопка «Посчитать» → `analyze`; таблица «По помещениям», сводка материалов, предупреждения, план, `st.download_button` с Excel. Любое `LLMError` и `ConvertError` кладётся в `st.session_state["llm_error"]` и показывается `st.error`, без трассировки.

`.claude/launch.json`: конфигурация `finishcalc`, `runtimeExecutable` = `.venv/Scripts/python.exe`, `runtimeArgs` = `["-m", "streamlit", "run", "app.py", "--server.port", "8501", "--server.headless", "true"]`, `port` = 8501.

- [ ] **Step 4: Запустить тесты** — 2 passed. Ручная проверка: `preview_start` с именем `finishcalc`, загрузить синтетический DXF из `tests/fixtures`, дойти до шага 4, скачать Excel.
- [ ] **Step 5: Commit** `git add app.py .claude/launch.json tests/test_app.py && git commit -m "feat: Streamlit interface"`

---

### Task 14: Приёмка на реальном проекте

**Files:**
- Create: `scripts/acceptance.py`
- Данные (не коммитятся): `data/` — копии планов этажей и листа общих данных (DWG).

**Interfaces:**
- Consumes: `load_drawings`, `load_tables`, `analyze`, `detect_profile`, `refine_profile`, `make_llm`, `export_excel`.
- Produces: скрипт `scripts/acceptance.py <папка>`: печатает число сопоставленных помещений, долю помещений, у которых площадь пола совпадает с `FinishRow.floor_area_m2` в пределах ±0.1 м², число помещений из ведомости с заполненными площадями потолка и стен, число помещений с предупреждением вместо площадей, сводку предупреждений по кодам; сохраняет `<папка>/finishcalc.xlsx`. Код возврата 0, если доля совпадений ≥ 90% и каждое помещение ведомости на загруженных планах посчитано или имеет предупреждение; иначе 1.

- [ ] **Step 1: Скопировать чертежи в `data/`** и убедиться, что `git status` их не показывает.
- [ ] **Step 2: Написать скрипт** и запустить: `.venv/Scripts/python.exe scripts/acceptance.py data`
Expected: код возврата 0, строка вида `floor area match: N/M (≥ 90%)`. Если меньше — разобрать список несовпадений и исправить извлечение в соответствующей задаче (новым тестом на синтетическом чертеже), затем повторить.
- [ ] **Step 3: Прогреть кэш ИИ** — повторный запуск проходит без запросов к Mistral (проверить по времени и по отсутствию новых ключей в `.cache/llm.json`).
- [ ] **Step 4: Commit** `git add scripts/acceptance.py && git commit -m "test: acceptance script for a real project"`

---

### Task 15: README и лицензия

**Files:**
- Create: `README.md`, `LICENSE`

- [ ] **Step 1: Написать `README.md`** по-русски: что делает инструмент; установка (Python, `pip install -e .`, ODA File Converter, `.env` с `MISTRAL_API_KEY`, Ollama как запасной вариант); запуск `streamlit run app.py`; формат входных данных (контуры, номера, типы пола, таблицы); где работает ИИ и почему он не считает цифры; ограничения из раздела 13 спецификации; как запустить тесты.
- [ ] **Step 2: `LICENSE`** — текст MIT, правообладатель «finishcalc contributors», 2026.
- [ ] **Step 3: Проверить**, что все тесты зелёные: `.venv/Scripts/python.exe -m pytest -v` — все passed (round-trip ODA — если установлен).
- [ ] **Step 4: Commit** `git add README.md LICENSE && git commit -m "docs: README and MIT license"`

---

### Task 16 (бонус): Чат по результату

**Files:**
- Create: `src/finishcalc/chat.py`
- Modify: `app.py` (поле вопроса на шаге 4)
- Test: `tests/test_chat.py`

**Interfaces:**
- Consumes: `ProjectResult`, `LLM`.
- Produces: `answer(question: str, result: ProjectResult, llm: LLM) -> str` — `name="chat"`, схема `{"answer": string}`; контекст — материалы, площади по типам пола и таблица помещений (номер, название, этаж, пол, стены, потолок) в виде CSV; системный промпт требует отвечать только по этим данным и говорить «в данных этого нет», если ответа нет.

- [ ] **Step 1: Тест** — с `FakeLLM`, который проверяет, что в `user` есть строка «Керамогранит» и номер «Т1», и возвращает `{"answer": "54,4 м²"}`; `answer(...) == "54,4 м²"`.
- [ ] **Step 2: Запустить** — FAIL.
- [ ] **Step 3: Реализовать** `chat.py` и поле `st.chat_input` на шаге 4.
- [ ] **Step 4: Запустить** `.venv/Scripts/python.exe -m pytest tests/test_chat.py tests/test_app.py -v` — passed.
- [ ] **Step 5: Commit** `git add src/finishcalc/chat.py app.py tests/test_chat.py && git commit -m "feat: chat over results"`
