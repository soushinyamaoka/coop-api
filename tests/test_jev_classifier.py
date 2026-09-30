import asyncio
import json
import shutil
import uuid
from pathlib import Path

import httpx
import pytest

from coop_parser import classify_item, parse_coop_email
import jev_classifier
from jev_classifier import classify_products, classifier_mode


@pytest.fixture
def tmp_path():
    # The managed Windows runner denies enumeration of its user Temp directory.
    path = Path(__file__).resolve().parent / f".jev-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeResponse:
    def __init__(self, payload=None, status=200, invalid_json=False):
        self.payload, self.status, self.invalid_json = payload, status, invalid_json

    def raise_for_status(self):
        if self.status >= 400:
            request = httpx.Request("POST", "https://example.invalid")
            response = httpx.Response(self.status, request=request)
            raise httpx.HTTPStatusError("status", request=request, response=response)

    def json(self):
        if self.invalid_json:
            raise ValueError("invalid json")
        return self.payload


class FakeClient:
    response = None
    calls = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, **kwargs):
        self.calls.append(kwargs)
        return self.response


class TimeoutClient(FakeClient):
    async def post(self, url, **kwargs):
        raise httpx.ReadTimeout("timeout")


class SlowClient(FakeClient):
    async def post(self, url, **kwargs):
        await asyncio.sleep(0.02)
        return self.response


def invoke(tmp_path, names, response, overrides=None, client_factory=FakeClient):
    FakeClient.calls = []
    FakeClient.response = response
    override_path = tmp_path / "overrides.json"
    if overrides is not None:
        override_path.write_text(json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
    return asyncio.run(classify_products(names, api_key="test-key", cache_path=tmp_path / "cache.json",
        overrides_path=override_path, client_factory=client_factory))


def test_confident_answer_adopted_and_cached(tmp_path):
    response = FakeResponse({"model": "jev-latest", "answers": {"category": {
        "type": "choice", "choice": "そのまま", "confidence": 0.8}}})
    decisions, stats = invoke(tmp_path, ["架空の冷凍商品"], response)
    assert decisions["架空の冷凍商品"] == {"choice": "そのまま", "source": "jev", "confidence": 0.8}
    assert stats["request_count"] == 1
    cached, cached_stats = invoke(tmp_path, ["架空の冷凍商品"], response)
    assert cached_stats["cache_hit_count"] == 1
    assert cached["架空の冷凍商品"]["source"] == "jev"


@pytest.mark.parametrize("env", [{}, {"CATEGORY_CLASSIFIER": "keyword"},
                                  {"CATEGORY_CLASSIFIER": "unknown", "TYPESAFE_API_KEY": "test"},
                                  {"CATEGORY_CLASSIFIER": "jev"}])
def test_disabled_or_invalid_flags_resolve_to_keyword_without_http(env):
    assert classifier_mode(env) == ("keyword", None if env.get("CATEGORY_CLASSIFIER", "keyword") != "jev" else "api_key_missing")


def test_jev_flag_requires_key_and_accepts_key():
    assert classifier_mode({"CATEGORY_CLASSIFIER": "jev", "TYPESAFE_API_KEY": "key"}) == ("jev", None)


def test_request_timeout_falls_back(tmp_path):
    decisions, stats = asyncio.run(classify_products(["架空商品"], api_key="key",
        cache_path=tmp_path / "cache.json", client_factory=TimeoutClient))
    assert decisions["架空商品"]["source"] == "keyword"
    assert stats["error_counts"]["timeout"] == 1


def test_budget_exceeded_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(jev_classifier, "RUN_BUDGET", 0)
    response = FakeResponse({"model": "jev-latest", "answers": {"category": {
        "type": "choice", "choice": "食材", "confidence": 1}}})
    decisions, stats = invoke(tmp_path, ["架空商品"], response, client_factory=SlowClient)
    assert decisions["架空商品"]["source"] == "keyword"
    assert stats["error_counts"]["budget_exceeded"] == 1


def test_low_confidence_falls_back_but_response_is_cached(tmp_path):
    response = FakeResponse({"model": "jev-latest", "answers": {"category": {
        "type": "choice", "choice": "そのまま", "confidence": 0.79}}})
    decisions, _ = invoke(tmp_path, ["架空の冷凍商品"], response)
    assert decisions["架空の冷凍商品"]["choice"] == classify_item("架空の冷凍商品")
    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))["架空の冷凍商品"]["confidence"] == 0.79


@pytest.mark.parametrize("response", [
    FakeResponse(status=401), FakeResponse(status=429), FakeResponse(status=529),
    FakeResponse(invalid_json=True), FakeResponse({"answers": {"category": {"type": "choice", "choice": "未知", "confidence": 1}}}),
])
def test_api_errors_and_invalid_responses_fall_back_without_cache(tmp_path, response):
    decisions, _ = invoke(tmp_path, ["架空の食品"], response)
    assert decisions["架空の食品"]["source"] == "keyword"
    cache_file = tmp_path / "cache.json"
    assert not cache_file.exists() or "架空の食品" not in json.loads(cache_file.read_text(encoding="utf-8"))


def test_overrides_and_cache_prevent_requests_and_broken_cache_recovers(tmp_path):
    cache_file = tmp_path / "cache.json"
    cache_file.write_text("broken", encoding="utf-8")
    response = FakeResponse({"model": "jev-latest", "answers": {"category": {
        "type": "choice", "choice": "食材", "confidence": 0.99}}})
    decisions, _ = invoke(tmp_path, ["手動修正商品", "架空商品"], response, {"手動修正商品": "調理キット"})
    assert decisions["手動修正商品"]["source"] == "keyword"
    assert len(FakeClient.calls) == 1
    decisions, stats = invoke(tmp_path, ["手動修正商品", "架空商品"], response, {"手動修正商品": "調理キット"})
    assert stats["cache_hit_count"] == 1
    assert len(FakeClient.calls) == 0


def test_parse_adds_classifier_fields_and_excludes_non_products():
    body = "注文番号：123\n商品名：架空応募\n数量：1点\n注文番号：124\n商品名：架空商品\n数量：1点"
    choices = {"架空応募": {"choice": "対象外", "source": "jev", "confidence": 0.91},
               "架空商品": {"choice": "食材", "source": "jev", "confidence": 0.88}}
    result = parse_coop_email(body, classifier=choices.get)
    assert result["total_items"] == 1
    assert result["excluded_count"] == 1
    assert result["excluded"][0]["reason"] == "商品ではない（応募・エントリー等）"
    assert result["ingredients"][0]["classifier"] == "jev"
    assert result["ingredients"][0]["classifier_confidence"] == 0.88


@pytest.mark.parametrize(("name", "expected"), [
    ("油揚げ", "食材"), ("サラダ油", "調味料・日用品"), ("ごま油", "調味料・日用品"),
    ("えびドリア", "食材"),
])
def test_requested_keyword_corrections(name, expected):
    assert classify_item(name) == expected


def test_cache_temporary_file_uses_dedicated_directory_and_cleans_up(tmp_path, monkeypatch):
    cache_file = tmp_path / "data" / "category_jev_cache.json"
    cache_file.parent.mkdir()
    original_replace = jev_classifier.os.replace
    observed = {}

    def inspect_replace(source, target):
        observed["source"] = Path(source)
        observed["target"] = Path(target)
        original_replace(source, target)

    monkeypatch.setattr(jev_classifier.os, "replace", inspect_replace)
    jev_classifier._save_cache(cache_file, {"合成商品": {"choice": "食材"}})
    assert observed["source"].parent == cache_file.parent / ".jev_cache_tmp"
    assert observed["target"] == cache_file
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"合成商品": {"choice": "食材"}}
    assert list((cache_file.parent / ".jev_cache_tmp").iterdir()) == []


def test_cache_replace_failure_cleans_temporary_file(tmp_path, monkeypatch):
    cache_file = tmp_path / "data" / "category_jev_cache.json"
    cache_file.parent.mkdir()
    cache_file.write_text('{"old":{}}', encoding="utf-8")

    def fail_replace(*args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(jev_classifier.os, "replace", fail_replace)
    jev_classifier._save_cache(cache_file, {"new": {}})
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"old": {}}
    assert list((cache_file.parent / ".jev_cache_tmp").iterdir()) == []


def test_cache_save_cleans_up_and_propagates_keyboard_interrupt(tmp_path, monkeypatch):
    cache_file = tmp_path / "data" / "category_jev_cache.json"
    cache_file.parent.mkdir()
    cache_file.write_text('{"old":{}}', encoding="utf-8")

    def interrupt_dump(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(jev_classifier.json, "dump", interrupt_dump)
    with pytest.raises(KeyboardInterrupt):
        jev_classifier._save_cache(cache_file, {"new": {}})
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"old": {}}
    assert list((cache_file.parent / ".jev_cache_tmp").iterdir()) == []
