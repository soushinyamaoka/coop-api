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
