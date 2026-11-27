"""参照専用トークン（API_READ_TOKEN）のテスト。注文の参照の2つだけで使え、ほかの操作は403。"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

with patch("dotenv.load_dotenv", return_value=False), patch.object(Path, "mkdir"):
    import coop_api_server as server

FULL = "full-token-for-test"
READ = "read-token-for-test"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "API_TOKEN", FULL)
    monkeypatch.setattr(server, "API_READ_TOKEN", READ)
    # データの読み書きは触らない
    monkeypatch.setattr(server, "load_latest_order", lambda: {"order_date": "2026-11-25", "ingredients": []})
    monkeypatch.setattr(server, "load_fetch_status", lambda: None)
    monkeypatch.setattr(server, "load_all_orders", lambda: {"last_updated": "", "orders": []})
    monkeypatch.setattr(server, "load_category_overrides", lambda: {})
    monkeypatch.setattr(server, "load_custom_meals", lambda: {})

    def must_not_fetch(**_kwargs):
        raise AssertionError("取り込みを動かした")

    monkeypatch.setattr(server, "import_coop_mail", must_not_fetch)
    monkeypatch.setattr(server, "save_category_overrides", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("保存した")))
    return TestClient(server.app)


@pytest.mark.parametrize("path", ["/api/coop/ingredients", "/api/coop/orders", "/api/coop/orders?include_items=true"])
@pytest.mark.parametrize("token", [FULL, READ])
def test_order_reads_accept_full_and_read_tokens(client, path, token):
    assert client.get(path, headers=bearer(token)).status_code == 200


@pytest.mark.parametrize("method, path, body", [
    ("post", "/api/coop/fetch", None),
    ("put", "/api/coop/classify", {"original_name": "しょうゆ", "category": "食材"}),
    ("get", "/api/coop/meals", None),
    ("delete", "/api/coop/meals/x", None),
    ("delete", "/api/coop/meals/x/recipe", None),
])
def test_read_token_cannot_use_other_operations(client, method, path, body):
    kwargs = {"headers": bearer(READ)}
    if body is not None:
        kwargs["json"] = body
    res = getattr(client, method)(path, **kwargs)
    assert res.status_code == 403
    assert res.json()["detail"] == "参照専用のトークンでは使えない操作です"


@pytest.mark.parametrize("headers", [{}, bearer("wrong"), bearer(""), {"Authorization": FULL}, {"Authorization": READ}])
def test_wrong_or_missing_token_is_401(client, headers):
    assert client.get("/api/coop/ingredients", headers=headers).status_code == 401
    assert client.get("/api/coop/meals", headers=headers).status_code == 401


def test_read_token_unset_cannot_be_matched_by_empty_bearer(client, monkeypatch):
    monkeypatch.setattr(server, "API_READ_TOKEN", "")
    assert client.get("/api/coop/ingredients", headers=bearer("")).status_code == 401
    assert client.get("/api/coop/ingredients", headers=bearer(FULL)).status_code == 200


def test_only_order_reads_use_read_token():
    """参照専用トークンを受け付けるのは、注文の参照の2つだけ（ほかのAPIを足しても広がらない）"""
    lines = Path(server.__file__).read_text(encoding="utf-8").splitlines()
    functions = []
    for number, line in enumerate(lines):
        if line.strip() == "verify_read_token(authorization)":
            definition = next(l for l in reversed(lines[:number]) if l.startswith("def "))
            functions.append(re.match(r"def (\w+)", definition).group(1))
    assert sorted(functions) == ["get_ingredients", "get_orders"]


# ─── 通知009の差し戻し: 全権トークンの欠落・同値を起動時に拒否する ───────────────


@pytest.mark.parametrize("api_token, read_token, reason", [
    ("", "", "api_token_missing"),
    ("", READ, "api_token_missing"),
    (FULL, FULL, "read_token_same_as_api_token"),
])
def test_startup_rejects_bad_token_config(api_token, read_token, reason):
    with pytest.raises(server.TokenConfigError) as exc:
        server.check_token_config(api_token, read_token)
    assert str(exc.value) == reason


@pytest.mark.parametrize("read_token", ["", READ])
def test_startup_accepts_valid_token_config(read_token):
    server.check_token_config(FULL, read_token)  # 参照専用トークンは任意


@pytest.mark.parametrize("api_token, read_token", [("", ""), (FULL, FULL)])
def test_app_does_not_start_with_bad_token_config(monkeypatch, caplog, api_token, read_token):
    monkeypatch.setattr(server, "API_TOKEN", api_token)
    monkeypatch.setattr(server, "API_READ_TOKEN", read_token)
    with pytest.raises(server.TokenConfigError):
        with TestClient(server.app):  # lifespan（startup）を動かす
            pass
    events = [r for r in caplog.records if getattr(r, "event", None) == "startup_configuration_invalid"]
    assert events and events[0].event_fields["reason"] in {"api_token_missing", "read_token_same_as_api_token"}
    assert not any(getattr(r, "event", None) == "startup" for r in caplog.records)
    # 値はログに出さない
    text = " ".join(str(getattr(r, "event_fields", "")) for r in caplog.records)
    assert FULL not in text


def test_app_starts_with_valid_token_config(monkeypatch):
    monkeypatch.setattr(server, "API_TOKEN", FULL)
    monkeypatch.setattr(server, "API_READ_TOKEN", READ)
    with TestClient(server.app) as c:
        assert c.get("/").status_code == 200


def test_main_exits_3_without_api_token():
    """python coop_api_server.py でも、全権トークンが無ければ待ち受けを始めずに終了コード3で終わる"""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k not in ("API_TOKEN", "API_READ_TOKEN")}
    env["API_PORT"] = "0"
    code = (
        "import dotenv, runpy; dotenv.load_dotenv=lambda *a, **k: False; "
        "runpy.run_path('coop_api_server.py', run_name='__main__')"
    )
    child = subprocess.run([sys.executable, "-c", code], cwd=Path(server.__file__).resolve().parent,
                           env=env, capture_output=True, text=True, timeout=30)
    assert child.returncode == 3
    assert "startup_configuration_invalid" in child.stderr and "api_token_missing" in child.stderr


def test_no_auth_skip_when_api_token_empty(client, monkeypatch):
    """全権トークンが空でも、認証を飛ばさない（従来は認証なしで通っていた）"""
    monkeypatch.setattr(server, "API_TOKEN", "")
    monkeypatch.setattr(server, "API_READ_TOKEN", "")
    assert client.get("/api/coop/ingredients").status_code == 401
    assert client.get("/api/coop/meals", headers=bearer("")).status_code == 401
