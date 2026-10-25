"""Тестовые подмены ИИ-клиента: не ходят в сеть, считают вызовы."""
from typing import Callable, Union


class FakeLLM:
    """Подмена `LLM`.

    `responses` — либо словарь «имя вызова -> ответ», либо функция
    `(system, user, schema, name) -> dict`. Каждый вызов (в том числе упавший
    с исключением) увеличивает счётчик `calls`.
    """

    def __init__(self, responses: Union[dict, Callable[[str, str, dict, str], dict]]):
        self.responses = responses
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict:
        self.calls += 1
        if callable(self.responses):
            return self.responses(system, user, schema, name)
        return self.responses[name]
