from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import app
from finishcalc.llm import LLMError
from finishcalc.norms import load_norms
from finishcalc.profile import load_profile
from tests.fakes import FakeLLM
from tests.fixtures.synth import SYNTH_PROFILE, make_synthetic_plan

# Streamlit ищет относительный путь рядом с файлом теста, а app.py лежит в корне репозитория.
APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """`load_dotenv()` приложения не должен подтянуть настоящий ключ из .env,
    а демо-папка разработчика не должна влиять на тесты."""
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    monkeypatch.delenv("FINISHCALC_DEMO_DIR", raising=False)


def test_app_starts():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert "1. Загрузка" in [h.value for h in at.header]


def test_app_shows_llm_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FINISHCALC_DEMO_DIR", str(tmp_path))   # пустая демо-папка
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.session_state["llm_error"] = "Mistral: 429 Rate limit exceeded"
    at.run()
    assert not at.exception
    assert any("429" in e.value for e in at.error)


def test_app_shows_four_steps():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert [h.value for h in at.header] == ["1. Загрузка", "2. Профиль", "3. Параметры", "4. Результат"]


# --- помощники приложения ---

def test_uploads_are_saved_under_their_base_name(tmp_path):
    uploaded = SimpleNamespace(name="../../evil.dxf", getvalue=lambda: b"data")
    [path] = app.save_uploads([uploaded], tmp_path / "upload")
    assert path == tmp_path / "upload" / "evil.dxf"
    assert path.read_bytes() == b"data"


def test_norms_survive_the_editor_table():
    norms = load_norms()
    assert app.norms_from_frame(app.norms_frame(norms)) == norms


def test_norms_with_blank_title_are_rejected():
    frame = app.norms_frame(load_norms())
    frame.loc[0, "title"] = "  "
    with pytest.raises(ValueError, match="название"):
        app.norms_from_frame(frame)


def test_floors_are_ordered_basement_first():
    assert sorted(["2", "10", "подвал", "1", "кровля"], key=app.floor_order) == ["подвал", "1", "2", "10", "кровля"]


# --- сквозные проверки на синтетическом плане: ИИ подменён, сеть не нужна ---

SUGGESTED = {"ceiling": ["suspended_ceiling"], "walls": ["plaster_gypsum", "primer", "paint_wd"],
             "floor": ["porcelain_tile"]}


def open_demo(monkeypatch, tmp_path, llm, names=None):
    """Демо-папка с одним синтетическим планом; `make_llm` подменён на `llm`."""
    demo = tmp_path / "demo"
    demo.mkdir()
    make_synthetic_plan(demo / "ПЛАН_синтетический.dxf", names)
    monkeypatch.setenv("FINISHCALC_DEMO_DIR", str(demo))
    monkeypatch.setattr("finishcalc.llm.make_llm", lambda env, cache_dir: llm)
    at = AppTest.from_file(APP, default_timeout=60).run()
    return at.button(key="demo").click().run()


def selectbox(at, label):
    return next(s for s in at.selectbox if s.label == label)


def result_tables(at):
    """Таблицы результата без редактора норм."""
    return [d for d in at.dataframe if d.key != "norms_editor"]


def test_demo_project_reaches_profile_step(monkeypatch, tmp_path):
    llm = FakeLLM({})  # любой вызов ИИ упал бы с KeyError: синтетический профиль определяется правилами
    at = open_demo(monkeypatch, tmp_path, llm)
    assert not at.exception
    assert not at.error
    assert selectbox(at, "Слой контуров помещений").value == "_AREA"
    assert selectbox(at, "Блок-номерок помещения").value == "номерок"
    assert at.session_state["project"]["plan_names"] == ["ПЛАН_синтетический.dxf"]
    assert any("Найдено помещений" in c.value for c in at.caption)
    assert llm.calls == 0


def test_calculation_in_mode_b_reaches_results(monkeypatch, tmp_path):
    llm = FakeLLM({"suggest_finish": SUGGESTED})
    at = open_demo(monkeypatch, tmp_path, llm)
    at.button(key="calculate").click().run()
    assert not at.exception
    assert not at.error
    assert any("предложил ИИ" in w.value for w in at.warning)
    assert any("Откосы" in c.value for c in at.caption)
    assert {"По помещениям", "Материалы"} <= {s.value for s in at.subheader}
    assert len(result_tables(at)) == 3  # по помещениям, материалы, предупреждения
    bundle = at.session_state["result"]
    assert bundle["result"].mode == "B"
    assert bundle["xlsx"].startswith(b"PK")  # xlsx — zip-архив
    assert [name for name, _ in bundle["plans"]] == ["ПЛАН_синтетический.dxf"]
    assert llm.calls == len(bundle["result"].rooms)


def test_empty_result_cells_are_blank_not_none(monkeypatch, tmp_path):
    at = open_demo(monkeypatch, tmp_path, FakeLLM({"suggest_finish": SUGGESTED}))
    at.button(key="calculate").click().run()
    rooms, _, issues = result_tables(at)
    assert set(rooms.value["Наименование"]) == {""}  # у синтетических помещений нет экспликации
    assert "" in set(issues.value["Помещение"])  # предупреждения по контурам без номера


def test_question_about_result_is_answered(monkeypatch, tmp_path):
    llm = FakeLLM({"suggest_finish": SUGGESTED, "chat": {"answer": "Керамогранит: 64,1 м²"}})
    at = open_demo(monkeypatch, tmp_path, llm)
    at.button(key="calculate").click().run()
    at.chat_input(key="chat_question").set_value("Сколько керамогранита?").run()
    assert not at.exception
    assert [m.markdown[0].value for m in at.chat_message] == ["Сколько керамогранита?", "Керамогранит: 64,1 м²"]


def test_chat_llm_error_is_shown_not_raised(monkeypatch, tmp_path):
    def reply(system, user, schema, name):
        if name == "chat":
            raise LLMError("Mistral HTTP 429: Rate limit exceeded")
        return SUGGESTED

    at = open_demo(monkeypatch, tmp_path, FakeLLM(reply))
    at.button(key="calculate").click().run()
    at.chat_input(key="chat_question").set_value("Сколько керамогранита?").run()
    assert not at.exception
    assert any("429" in e.value for e in at.error)
    assert len(at.chat_message) == 0
    assert result_tables(at)  # результат на экране остаётся


def test_result_is_hidden_after_parameters_change(monkeypatch, tmp_path):
    at = open_demo(monkeypatch, tmp_path, FakeLLM({"suggest_finish": SUGGESTED}))
    at.button(key="calculate").click().run()
    at.number_input(key="height_default").set_value(3.5).run()
    assert not at.exception
    assert any("ещё раз" in w.value for w in at.warning)
    assert result_tables(at) == []


def test_llm_error_during_calculation_is_shown_not_raised(monkeypatch, tmp_path):
    def refuse(system, user, schema, name):
        raise LLMError("Mistral HTTP 429: Rate limit exceeded")

    at = open_demo(monkeypatch, tmp_path, FakeLLM(refuse))
    at.button(key="calculate").click().run()
    assert not at.exception
    assert any("429" in e.value for e in at.error)
    assert at.session_state["result"] is None


def test_profile_roles_missing_from_rules_are_asked_from_llm(monkeypatch, tmp_path):
    renamed = {"_AREA": "layer_one"}  # слой контуров правилами не найти
    llm = FakeLLM({"profile": {"room_contour_layer": "layer_one"}})
    at = open_demo(monkeypatch, tmp_path, llm, renamed)
    assert not at.exception
    assert not at.error
    assert selectbox(at, "Слой контуров помещений").value == "layer_one"
    assert llm.calls == 1


def test_profile_error_leaves_manual_choice(monkeypatch, tmp_path):
    renamed = {"_AREA": "layer_one"}
    at = open_demo(monkeypatch, tmp_path, FakeLLM({"profile": {"room_contour_layer": "none"}}), renamed)
    assert not at.exception
    assert any("Слой контуров помещений" in e.value for e in at.error)
    assert selectbox(at, "Слой контуров помещений").value is None
    assert any("Выберите значения" in w.value for w in at.warning)
    selectbox(at, "Слой контуров помещений").select("layer_one").run()
    assert not at.exception
    assert any("Найдено помещений" in c.value for c in at.caption)


def test_saved_profile_can_be_taken_again(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # профиль и нормы пишутся в .cache рядом с запуском
    at = open_demo(monkeypatch, tmp_path, FakeLLM({}))
    at.button(key="save_profile").click().run()
    assert load_profile(tmp_path / ".cache" / "profile.json") == SYNTH_PROFILE
    selectbox(at, "Слой контуров помещений").select("0").run()
    at.button(key="load_profile").click().run()
    assert not at.exception
    assert selectbox(at, "Слой контуров помещений").value == "_AREA"


def test_saved_norms_are_read_back(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    at = open_demo(monkeypatch, tmp_path, FakeLLM({}))
    at.button(key="save_norms").click().run()
    assert load_norms(tmp_path / ".cache" / "norms.yaml") == load_norms()
    at = AppTest.from_file(APP, default_timeout=60).run()  # новая сессия читает сохранённый файл
    assert not at.exception
    assert not at.warning
