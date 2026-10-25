import json

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

def test_fallback_answer_not_cached(tmp_path):
    def fail(*a): raise L.LLMError("429")
    fallback = FakeLLM({"t": {"ok": 1}})
    path = tmp_path / "llm.json"
    cached = L.CachedLLM(L.FallbackLLM(FakeLLM(fail), fallback), path, model_id="m")
    assert cached.complete_json("s", "u", {}, "t") == {"ok": 1}
    assert not path.exists() or json.loads(path.read_text(encoding="utf-8")) == {}
    assert cached.complete_json("s", "u", {}, "t") == {"ok": 1}
    assert fallback.calls == 2

def test_primary_answer_cached_through_fallback_wrapper(tmp_path):
    primary, fallback = FakeLLM({"t": {"ok": 1}}), FakeLLM({"t": {"ok": 2}})
    cached = L.CachedLLM(L.FallbackLLM(primary, fallback), tmp_path / "llm.json", model_id="m")
    assert cached.complete_json("s", "u", {}, "t") == {"ok": 1}
    assert cached.complete_json("s", "u", {}, "t") == {"ok": 1}
    assert primary.calls == 1 and fallback.calls == 0
