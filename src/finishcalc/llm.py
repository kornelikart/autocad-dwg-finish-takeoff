"""Клиенты ИИ (Mistral, Ollama), запасной вариант и дисковый кэш.

ИИ только классифицирует и разбирает текст; площади и количества он не считает.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import requests

MISTRAL_MODEL = "ministral-14b-latest"
OLLAMA_MODEL = "qwen3.5"


class LLMError(Exception):
    """Запрос к ИИ не удался (сеть, код ответа, неразборчивое содержимое)."""


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict: ...


def _error_detail(resp) -> str:
    """Текст ошибки из JSON-ответа; пустая строка, если тело не разобрать."""
    try:
        body = resp.json()
    except Exception:
        return ""
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or body)
    return str(body)


def _parse_answer(content, source: str) -> dict:
    """Ответ модели — строка с JSON-объектом."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise LLMError(f"{source}: ответ не является JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise LLMError(f"{source}: ожидался JSON-объект, получено {type(data).__name__}")
    return data


class MistralLLM:
    ATTEMPTS = 3

    def __init__(self, api_key: str, model: str = MISTRAL_MODEL,
                 base_url: str = "https://api.mistral.ai/v1", timeout: float = 60):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"
        failure = "нет ответа"
        for attempt in range(1, self.ATTEMPTS + 1):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                failure = f"сеть: {exc}"
            else:
                code = resp.status_code
                if code == 200:
                    try:
                        content = resp.json()["choices"][0]["message"]["content"]
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise LLMError(f"Mistral: неожиданный формат ответа ({exc!r})") from exc
                    return _parse_answer(content, "Mistral")
                failure = f"HTTP {code}: {_error_detail(resp)}"
                if code != 429 and code < 500:
                    raise LLMError(f"Mistral {failure}")
            if attempt < self.ATTEMPTS:
                time.sleep(2 * attempt)
        raise LLMError(f"Mistral {failure} (попыток: {self.ATTEMPTS})")


class OllamaLLM:
    def __init__(self, model: str = OLLAMA_MODEL, base_url: str = "http://localhost:11434",
                 timeout: float = 300):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema,
            "think": False,
            "stream": False,
            "options": {"temperature": 0},
        }
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise LLMError(f"Ollama: сеть: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"Ollama HTTP {resp.status_code}: {_error_detail(resp)}")
        try:
            content = resp.json()["message"]["content"]
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError(f"Ollama: неожиданный формат ответа ({exc!r})") from exc
        return _parse_answer(content, "Ollama")


class FallbackLLM:
    """Основной клиент; при `LLMError` — запасной."""

    def __init__(self, primary: LLM, fallback: LLM):
        self.primary = primary
        self.fallback = fallback

    def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict:
        try:
            return self.primary.complete_json(system, user, schema, name)
        except LLMError:
            return self.fallback.complete_json(system, user, schema, name)


class CachedLLM:
    """Кэш ответов на диске: повторный запрос с теми же данными не уходит к модели."""

    def __init__(self, inner: LLM, path: Path, model_id: str):
        self.inner = inner
        self.path = Path(path)
        self.model_id = model_id
        self._store: dict = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._store, ensure_ascii=False, indent=1), encoding="utf-8")

    def _key(self, system: str, user: str, schema: dict, name: str) -> str:
        raw = json.dumps([name, self.model_id, system, user, schema],
                         ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def complete_json(self, system: str, user: str, schema: dict, name: str) -> dict:
        key = self._key(system, user, schema, name)
        if key not in self._store:
            self._store[key] = self.inner.complete_json(system, user, schema, name)
            self._save()
        return copy.deepcopy(self._store[key])


def make_llm(env: Mapping[str, str], cache_dir: Path) -> CachedLLM:
    """С ключом `MISTRAL_API_KEY` — Mistral с запасным Ollama, без ключа — только Ollama."""
    cache_file = Path(cache_dir) / "llm.json"
    api_key = (env.get("MISTRAL_API_KEY") or "").strip()
    if api_key:
        inner = FallbackLLM(MistralLLM(api_key), OllamaLLM())
        return CachedLLM(inner, cache_file, model_id=MISTRAL_MODEL)
    return CachedLLM(OllamaLLM(), cache_file, model_id=OLLAMA_MODEL)
