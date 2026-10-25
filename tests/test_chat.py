import pytest

from finishcalc.chat import NO_DATA, answer, context
from finishcalc.llm import LLMError
from finishcalc.norms import load_norms
from tests.fakes import FakeLLM


def test_answer_comes_from_llm_with_result_tables(small_result):
    seen = {}

    def reply(system, user, schema, name):
        seen.update(system=system, user=user, schema=schema, name=name)
        assert "Керамогранит" in user and "Т1" in user
        return {"answer": "54,4 м²"}

    assert answer("Сколько керамогранита?", small_result, FakeLLM(reply)) == "54,4 м²"
    assert seen["name"] == "chat"
    assert seen["schema"]["required"] == ["answer"]
    assert NO_DATA in seen["system"]
    assert seen["user"].endswith("Вопрос: Сколько керамогранита?")


def test_context_lists_rooms_and_floor_types(small_result):
    text = context(small_result)
    assert "Т1;Санузел;1;7;20,0;" in text
    assert "Площадь пола по типам:\nТип пола;Площадь, м²\n7;20,0" in text
    assert "Материалы по этажам" not in text


def test_context_with_norms_has_materials_per_floor(small_result):
    text = context(small_result, load_norms())
    tile = next(m for m in small_result.materials if m.material == "Керамогранит")
    # Одно помещение на 1-м этаже: итог этажа равен итогу здания, посчитан `calc`, а не ИИ.
    assert f"1;Керамогранит;м²;{str(tile.quantity).replace('.', ',')}" in text


def test_empty_answer_is_an_error(small_result):
    with pytest.raises(LLMError):
        answer("Что-нибудь", small_result, FakeLLM({"chat": {"answer": "  "}}))
