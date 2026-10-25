# Устройство finishcalc

Как устроено приложение, в каком виде оно хранит данные и что выдаёт.

## Конвейер расчёта

Чертежи идут слева направо. ИИ подключается только в трёх местах: роли слоёв в профиле, разбор описаний отделки и чат. Все площади и количества считает модуль `calc`.

```mermaid
flowchart LR
    dwg["Чертежи<br/>DWG · DXF"] -->|файлы| convert["convert<br/>DWG → DXF, ODA"]
    convert -->|DXF| extract["extract<br/>помещения · проёмы<br/>таблицы ACAD_TABLE"]
    extract -->|Room, таблицы| pipeline["pipeline<br/>analyze()"]
    pipeline -->|отделка, проёмы| calc["calc<br/>площади · материалы<br/>без ИИ"]
    calc -->|площади| result["ProjectResult"]

    profile["profile<br/>роли слоёв и блоков"] -->|Profile| extract
    pipeline -->|описание отделки| finishes["finishes<br/>текст → ключи норм"]
    finishes -->|ключи норм| pipeline
    result -->|таблицы CSV| chat["chat<br/>вопрос → ответ из таблиц"]

    profile -->|запрос| llm["llm · CachedLLM<br/>кэш .cache/llm.json → Mistral → при ошибке Ollama<br/>ответ по JSON-схеме, temperature 0"]
    finishes -->|запрос| llm
    chat -->|запрос| llm

    classDef ai fill:#f7e6ef,stroke:#a8306f,color:#17212a
    classDef math fill:#e3edf8,stroke:#1d5ea8,color:#17212a
    class profile,finishes,chat,llm ai
    class calc math
```

| Модуль | Что делает | ИИ |
|---|---|---|
| `convert` | DWG → DXF через ODA File Converter | — |
| `extract.rooms` | контуры помещений, номерки, типы пола; номер относится к наименьшему контуру, повторный контур не даёт второго помещения | — |
| `extract.tables` | ACAD_TABLE: ведомость отделки, экспликации помещений и полов, спецификация проёмов; продолжения таблиц | — |
| `extract.openings` | двери и окна, ближайшая марка в радиусе 1,5 м, размер по спецификации | — |
| `profile` | какой слой и блок за что отвечает; сначала правила | если правила не нашли |
| `finishes` | зоны стен, высота «h=…», заход пола на стену правилами; позиции справочника — ИИ | текст → ключи |
| `calc` | площади пола, потолка, стен по зонам за вычетом проёмов; материалы по нормам | — |
| `pipeline` | сборка всего в `ProjectResult`, режим A или B | — |
| `export` · `render` | Excel из пяти листов; картинка плана | — |
| `chat` | ответ по таблицам расчёта; итоги по этажам заранее считает `calc` | поиск в таблицах |

## Интерфейс и файлы

Streamlit перезапускает скрипт при каждом клике, поэтому чтение чертежей, вызов ИИ и расчёт выполняются только по кнопкам. Их итог хранится в `st.session_state`: `project`, `preview`, `result`. Ошибка ИИ или конвертера показывается сообщением под своим шагом.

```mermaid
flowchart LR
    s1["Шаг 1. Загрузка<br/>load_drawings · load_tables"] -->|чертежи| s2["Шаг 2. Профиль<br/>detect_profile, затем ИИ"]
    s2 -->|Profile| s3["Шаг 3. Параметры<br/>высоты этажей, нормы"]
    s3 -->|нормы| s4["Шаг 4. Результат<br/>analyze · export · render"]
    s1 -->|файлы| tmp[("временная папка<br/>DWG → DXF")]
    s2 <-->|сохранить · взять| prof[(".cache/profile.json")]
    s3 <-->|сохранить · прочитать| norms[(".cache/norms.yaml")]
    s4 -->|выдаёт| out["finishcalc.xlsx · 5 листов<br/>план с раскраской<br/>чат по результату"]
```

## Данные

Отдельной базы данных нет: проект считается за минуты и целиком помещается в памяти. Данные — это классы из `src/finishcalc/model.py` и `pipeline.py`, связанные тремя ключами, как таблицы в БД:

- **номер помещения** («А01»; латиница-двойники заменяются кириллицей) связывает `Room`, `RoomAreas`, `FinishRow`, `Opening`, `MaterialLine`, `Issue`;
- **код типа пола** («7», «7*») связывает `Room` и `FloorType`;
- **ключ нормы** (`plaster_gypsum`, `porcelain_tile`…) связывает `FinishSpec`, `Zone`, `FloorLayer` со справочником `Norm`.

```mermaid
erDiagram
    ProjectResult ||--o{ Room : rooms
    ProjectResult ||--o{ RoomAreas : "areas по номеру"
    ProjectResult ||--o{ FinishRow : "finish_rows по номеру"
    ProjectResult ||--o{ FloorType : "floor_types по коду"
    ProjectResult ||--o{ MaterialLine : materials
    ProjectResult ||--o{ Issue : issues
    Room ||--o| FinishSpec : finish
    Room ||--o{ Opening : openings
    FinishSpec ||--|{ Zone : walls
    FloorType ||--o{ FloorLayer : layers
    Room }o--o| FloorType : "floor_type = code"
    Room ||--|| RoomAreas : "number"
    Room |o--o| FinishRow : "number"
    Zone }o--o{ Norm : "items = key"
    FloorLayer }o--o| Norm : "norm_key = key"

    ProjectResult {
        string mode "A — ведомость проектировщика, B — отделку предложил ИИ"
    }
    Room {
        string number PK "номер помещения"
        string name "из экспликации"
        string floor "1, 2, подвал"
        float area_m2
        float perimeter_m
        string floor_type FK "код типа пола"
    }
    FinishSpec {
        list ceiling_items FK "ключи норм"
        float ceiling_height_m "из h=3,0м"
        list floor_items FK "ключи норм"
        float floor_upturn_m "завести на стену"
        dict source "тексты ведомости"
    }
    Zone {
        float from_m
        float to_m "пусто — до потолка"
        list items FK "ключи норм"
        string source_text
    }
    Opening {
        string kind "door или window"
        string mark
        float width_m
        float height_m
        float sill_m
        list rooms FK "номера помещений"
        string size_source "spec или default"
    }
    RoomAreas {
        string number PK "ключ словаря"
        float height_m
        float floor_m2
        float ceiling_m2
        float walls_m2 "за вычетом проёмов"
        float upturn_m2
    }
    FinishRow {
        string number PK
        string ceiling
        string walls
        string floor
        float floor_area_m2 "для сверки"
    }
    FloorType {
        string code PK "7, 7*"
        string rooms_label
        string source_text
    }
    FloorLayer {
        string material
        float thickness_mm_min
        float thickness_mm_max
        string count_as "volume, area, piece"
        string norm_key FK
    }
    Norm {
        string key PK
        string title
        string unit "м², м³, кг, л"
        float per_m2 "пусто — прямо с чертежа"
        int coats
        float reserve_pct
    }
    MaterialLine {
        string material
        string unit
        float quantity "округлено"
        float reserve_pct
        string basis "норма или чертёж"
        list rooms FK "номера помещений"
    }
    Issue {
        string code "15 кодов проверок"
        string room FK "номер или пусто"
        string message
    }
```

`Profile` (роли слоёв и блоков, `mm_per_unit`) — вход расчёта, в `ProjectResult` не входит.

### Что хранится между запусками

| Файл | Содержимое | Кто пишет | В git |
|---|---|---|---|
| `src/finishcalc/norms.yaml` | справочник норм по умолчанию: ключ → название, единица, расход, слои, запас | поставляется с пакетом | да |
| `.cache/norms.yaml` | тот же справочник с правками сметчика | шаг 3, «Сохранить справочник норм» | нет |
| `.cache/profile.json` | поля `Profile` | шаг 2, «Сохранить профиль» | нет |
| `.cache/llm.json` | кэш ИИ: sha256(задача, модель, промпт, текст, схема) → JSON-ответ; ответы запасной модели не кэшируются | `CachedLLM` после каждого удачного запроса | нет |
| `finishcalc.xlsx` | Ведомость отделки, Экспликация полов, Материалы, По помещениям, Проверки | шаг 4 или `scripts/acceptance.py` | нет |
| `.env` | `MISTRAL_API_KEY` | вручную | нет |

## Результат на демо-плане

Демо-план — синтетический чертёж из тестов (`tests/fixtures/synth.py`) с таблицами в формате реальных (`tests/test_pipeline.py`). В него специально заложены типичные ошибки чертежа: контур без номера, два номера в одном контуре, повторный контур, номер вне контуров, проём без марки. Найдено 3 помещения из 6 номеров, выдано 12 предупреждений.

**Ведомость отделки.** В ведомости проектировщика площади потолка и стен пустые, программа их заполняет:

| № | Потолок | м² | Стены | м² | Пол | м² |
|---|---|---:|---|---:|---|---:|
| А01 Холл | Подвесной потолок h=3,0м | **20,0** | Штукатурка, окраска | **44,5** | Керамогранит, завести на стену на 300мм. | 20,0 |
| А02 Кабинет | Окраска | **20,0** | Штукатурка, окраска | **50,0** | Керамогранит | 20,0 |

Стены А01: 18 м × 3 м − дверь ДС-3 1 × 2,15 − окно 1,5 × 1,5 − заход пола на стену (18 − 1) × 0,3 = 44,5 м².

**Материалы:**

| Материал | Ед. | Количество | Запас |
|---|---|---:|---:|
| Подвесной потолок | м² | 21,0 | 5% |
| Штукатурка гипсовая (слой 10 мм) | кг | 802,9 | 0% |
| Грунтовка | л | 17,2 | 0% |
| Краска водно-дисперсионная | л | 34,3 | 0% |
| Керамогранит | м² | 27,6 | 10% |
| Клей плиточный | кг | 125,5 | 0% |
| Ламинат | м² | 21,0 | 5% |

Ламинат в А02 взят из экспликации полов (тип 2), хотя в ведомости там керамогранит; это попало в проверки как `floor_type_mismatch`.

![Шаг 4 интерфейса: таблицы «По помещениям» и «Материалы»](images/ui_result.png)

![Чат по результату](images/ui_chat.png)

На снимках ответы ИИ подставлены тестовой заглушкой: в окружении, где делались снимки, Mistral недоступен.
