import json
import subprocess
import sys
import shutil
import threading
import uuid
from email.message import EmailMessage

import pytest
from unittest.mock import patch

# Importing this legacy module normally loads .env; tests disable that call.
with patch("dotenv.load_dotenv", return_value=False):
    import fetch_coop_mail as fetch
import jev_classifier
from coop_parser import source_fingerprint


BODY = "注文番号：1\n商品名：しょうゆ\n数量：1点"


@pytest.fixture
def tmp_path():
    path = __import__("pathlib").Path(__file__).resolve().parent / f".fetch-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class FakeMail:
    def __init__(self, raw_email):
        self.raw_email = raw_email

    def login(self, *_args):
        pass

    def select(self, *_args, **_kwargs):
        return "OK", []

    def search(self, *_args):
        return "OK", [b"1"]

    def fetch(self, *_args):
        return "OK", [(b"1", self.raw_email)]

    def logout(self):
        pass


def make_email(body=BODY):
    msg = EmailMessage()
    msg["Subject"] = "COOP注文確認"
    msg["From"] = "example@example.invalid"
    msg["Date"] = "Sun, 27 Sep 2026 07:00:00 +0900"
    msg.set_content(body)
    return msg.as_bytes()


def setup_import(monkeypatch, tmp_path, body=BODY):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    monkeypatch.setattr(fetch, "GMAIL_ADDRESS", "placeholder")
    monkeypatch.setattr(fetch, "GMAIL_APP_PASSWORD", "placeholder")
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args: FakeMail(make_email(body)))
    monkeypatch.setattr(jev_classifier, "classifier_mode", lambda: ("jev", None))
    calls = []

    def classify(names, **_kwargs):
        calls.append(names)
        return {name: {"choice": "調味料・日用品", "source": "jev"} for name in names}, {}

    monkeypatch.setattr(jev_classifier, "classify_products_sync", classify)
    monkeypatch.setenv("TYPESAFE_API_KEY", "placeholder")
    return data_dir, calls


def test_unchanged_cron_skips_classifier_and_save(monkeypatch, tmp_path):
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    original_latest = {"source_fingerprint": source_fingerprint(BODY), "sentinel": "unchanged"}
    latest_file = data_dir / "coop_latest.json"
    latest_file.write_text(json.dumps(original_latest), encoding="utf-8")
    before = latest_file.read_bytes()
    monkeypatch.setattr(fetch, "save_results", lambda _results: (_ for _ in ()).throw(AssertionError("saved")))

    assert fetch.run_coop_mail_import(save=True, skip_if_unchanged=True) == []
    assert classifier_calls == []
    assert latest_file.read_bytes() == before
    assert not (data_dir / "coop_orders.json").exists()


@pytest.mark.parametrize("previous", [None, "broken", "missing_fingerprint", "different"])
def test_unavailable_or_different_fingerprint_continues(monkeypatch, tmp_path, previous):
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    latest_file = data_dir / "coop_latest.json"
    if previous == "broken":
        latest_file.write_text("{broken", encoding="utf-8")
    elif previous == "missing_fingerprint":
        latest_file.write_text("{}", encoding="utf-8")
    elif previous == "different":
        latest_file.write_text(json.dumps({"source_fingerprint": "other"}), encoding="utf-8")
    saved = []
    monkeypatch.setattr(fetch, "save_results", lambda results: saved.extend(results))

    result = fetch.run_coop_mail_import(save=True, skip_if_unchanged=True)
    assert classifier_calls == [["しょうゆ"]]
    assert len(result) == 1
    assert len(saved) == 1


def test_default_manual_import_does_not_skip_matching_fingerprint(monkeypatch, tmp_path):
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    (data_dir / "coop_latest.json").write_text(
        json.dumps({"source_fingerprint": source_fingerprint(BODY)}), encoding="utf-8"
    )
    saved = []
    monkeypatch.setattr(fetch, "save_results", lambda results: saved.extend(results))

    result = fetch.run_coop_mail_import(save=True)
    assert classifier_calls == [["しょうゆ"]]
    assert len(result) == len(saved) == 1


def test_jev_name_is_stripped_and_missing_decision_uses_keyword(monkeypatch, tmp_path):
    body = "注文番号：1\n商品名： サラダ油 \n数量：1点"
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path, body)

    def classify(_names, **_kwargs):
        classifier_calls.append(["called"])
        return {}, {}

    monkeypatch.setattr(jev_classifier, "classify_products_sync", classify)
    result, success = fetch._fetch_coop_emails()
    assert success
    assert classifier_calls == [["called"]]
    assert result[0]["seasonings"][0]["category"] == "調味料・日用品"


def test_jev_decision_matches_stripped_name(monkeypatch, tmp_path):
    body = "注文番号：1\n商品名：  架空品  \n数量：1点"
    _data_dir, classifier_calls = setup_import(monkeypatch, tmp_path, body)
    result, success = fetch._fetch_coop_emails()
    assert success
    assert classifier_calls == [["架空品"]]
    assert result[0]["seasonings"][0]["classifier"] == "jev"


def _child_lock_attempt(data_dir, *, exit_after_lock=False):
    code = (
        "import os, sys, dotenv\n"
        "dotenv.load_dotenv=lambda *a, **k: False\n"
        "from pathlib import Path\n"
        "from fetch_coop_mail import _coop_import_lock, ImportBusyError\n"
        "import fetch_coop_mail as f\n"
        "f.DATA_DIR=Path(sys.argv[1])\n"
        "try:\n"
        " with _coop_import_lock():\n"
        "  os._exit(0) if len(sys.argv)>2 else print('acquired')\n"
        "except ImportBusyError:\n print('busy')\n"
    )
    args = [sys.executable, "-c", code, str(data_dir)]
    if exit_after_lock:
        args.append("crash")
    return subprocess.run(args, capture_output=True, text=True, timeout=10)


def test_file_lock_blocks_other_process_and_releases(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "DATA_DIR", tmp_path / "data")
    with fetch._coop_import_lock():
        result = _child_lock_attempt(fetch.DATA_DIR)
        assert result.returncode == 0
        assert result.stdout.strip() == "busy"
    result = _child_lock_attempt(fetch.DATA_DIR)
    assert result.returncode == 0
    assert result.stdout.strip() == "acquired"


def test_file_lock_blocks_another_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "DATA_DIR", tmp_path / "data")
    outcomes = []
    with fetch._coop_import_lock():
        def attempt():
            try:
                with fetch._coop_import_lock():
                    outcomes.append("acquired")
            except fetch.ImportBusyError:
                outcomes.append("busy")

        thread = threading.Thread(target=attempt)
        thread.start()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert outcomes == ["busy"]


def test_abnormal_process_exit_releases_lock_file(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "DATA_DIR", tmp_path / "data")
    result = _child_lock_attempt(fetch.DATA_DIR, exit_after_lock=True)
    assert result.returncode == 0
    assert (fetch.DATA_DIR / ".coop_import.lock").exists()
    result = _child_lock_attempt(fetch.DATA_DIR)
    assert result.returncode == 0
    assert result.stdout.strip() == "acquired"


def test_cron_skips_when_lock_is_held(monkeypatch, tmp_path, caplog):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    monkeypatch.setattr(fetch, "_fetch_coop_emails", lambda **_kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(fetch, "save_results", lambda _results: (_ for _ in ()).throw(AssertionError()))
    with fetch._coop_import_lock():
        assert fetch.run_coop_mail_import(save=True, skip_if_unchanged=True) == []
    skipped = [record for record in caplog.records if getattr(record, "event", None) == "import_skipped"]
    job_ends = [record for record in caplog.records if getattr(record, "event", None) == "job_end"]
    assert len(skipped) == 1
    assert skipped[0].event_fields == {"reason": "locked"}
    assert len(job_ends) == 1
    assert job_ends[0].event_fields["status"] == "success"


def test_manual_fetch_busy_does_not_log_job_failed(monkeypatch, tmp_path, caplog):
    """busyはcron/手動が競合しただけの正常系であり、job_failedと矛盾するjob_end(success)を
    併記してはならない。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    monkeypatch.setattr(fetch, "_fetch_coop_emails", lambda **_kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(fetch, "save_results", lambda _results: (_ for _ in ()).throw(AssertionError()))
    with fetch._coop_import_lock():
        with pytest.raises(fetch.ImportBusyError):
            fetch.run_coop_mail_import(save=True, skip_if_unchanged=False)
    failed = [record for record in caplog.records if getattr(record, "event", None) == "job_failed"]
    job_ends = [record for record in caplog.records if getattr(record, "event", None) == "job_end"]
    assert failed == []
    assert len(job_ends) == 1
    assert job_ends[0].event_fields["status"] == "success"


def _order(order_date, fingerprint, name="しょうゆ"):
    return {
        "order_date": order_date,
        "source_fingerprint": fingerprint,
        "total_items": 1,
        "ingredients": [],
        "kits": [],
        "ready_to_eat": [],
        "baby_food": [],
        "seasonings": [{"order_no": "1", "name": name, "original_name": name, "quantity": 1, "category": "調味料・日用品"}],
        "excluded": [],
    }


def test_save_results_appends_history_without_duplicates(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)

    fetch.save_results([_order("2026-09-20", "a")])
    fetch.save_results([_order("2026-09-27", "b")])
    fetch.save_results([_order("2026-09-27", "b")])  # 同じメールの再取込
    fetch.save_results([_order("2026-10-04", "a")])  # 内容が同じでも別週の注文は残す

    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [(o["order_date"], o["source_fingerprint"]) for o in history] == [
        ("2026-09-20", "a"), ("2026-09-27", "b"), ("2026-10-04", "a"),
    ]
    latest = json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))
    assert latest["order_date"] == "2026-10-04"


def test_save_results_keeps_unreadable_history(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    all_file = data_dir / "coop_orders.json"
    all_file.write_text("{broken", encoding="utf-8")

    fetch.save_results([_order("2026-10-04", "a")])

    assert all_file.read_text(encoding="utf-8") == "{broken"
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-10-04"


class MultiFakeMail(FakeMail):
    def __init__(self, raw_emails):
        self.raw_emails = raw_emails

    def search(self, *_args):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.raw_emails)))]

    def fetch(self, mail_id, *_args):
        return "OK", [(mail_id, self.raw_emails[int(mail_id) - 1])]


def make_dated_email(body, date):
    msg = EmailMessage()
    msg["Subject"] = "COOP注文確認"
    msg["From"] = "example@example.invalid"
    msg["Date"] = date
    msg.set_content(body)
    return msg.as_bytes()


def test_backfill_imports_all_orders_into_history_only(monkeypatch, tmp_path):
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    emails = [
        make_dated_email("注文番号：1\n商品名：しょうゆ\n数量：1点", "Sun, 13 Sep 2026 07:00:00 +0900"),
        make_dated_email("広告メールです", "Mon, 14 Sep 2026 07:00:00 +0900"),
        make_dated_email("注文番号：2\n商品名：みそ\n数量：1点", "Sun, 20 Sep 2026 07:00:00 +0900"),
    ]
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args: MultiFakeMail(emails))
    latest_file = data_dir / "coop_latest.json"
    latest_file.write_text(json.dumps({"sentinel": "keep"}), encoding="utf-8")

    result = fetch.run_coop_mail_import(days_back=365, save=True, backfill=True)

    assert [o["order_date"] for o in result] == ["2026-09-13", "2026-09-20"]
    assert classifier_calls == []  # 過去分取り込みではJevを呼ばない
    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [o["order_date"] for o in history] == ["2026-09-13", "2026-09-20"]
    assert json.loads(latest_file.read_text(encoding="utf-8")) == {"sentinel": "keep"}


def test_orders_endpoint_returns_history_newest_first_with_items(monkeypatch, tmp_path):
    from pathlib import Path

    with patch("dotenv.load_dotenv", return_value=False), patch.object(Path, "mkdir"):
        import coop_api_server as server

    monkeypatch.setattr(server, "verify_token", lambda _authorization: None)
    monkeypatch.setattr(server, "load_all_orders", lambda: {
        "last_updated": "2026-10-04T07:00:00",
        "orders": [_order("2026-09-27", "a", "しょうゆ"), _order("2026-10-04", "b", "みそ")],
    })
    monkeypatch.setattr(server, "load_category_overrides", lambda: {"みそ": "食材"})
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    summary = client.get("/api/coop/orders").json()
    assert [o["order_date"] for o in summary["orders"]] == ["2026-10-04", "2026-09-27"]
    assert "items" not in summary["orders"][0]

    detail = client.get("/api/coop/orders?include_items=true").json()
    assert detail["orders"][0]["items"] == [
        {"name": "みそ", "original_name": "みそ", "quantity": 1, "category": "食材"},
    ]


def test_fetch_endpoint_returns_busy_without_running_import(monkeypatch):
    from pathlib import Path

    with patch("dotenv.load_dotenv", return_value=False), patch.object(Path, "mkdir"):
        import coop_api_server as server

    monkeypatch.setattr(server, "verify_token", lambda _authorization: None)
    monkeypatch.setattr(
        server,
        "run_coop_mail_import",
        lambda **_kwargs: (_ for _ in ()).throw(fetch.ImportBusyError()),
    )
    from fastapi.testclient import TestClient

    response = TestClient(server.app).post("/api/coop/fetch")
    assert response.status_code == 200
    assert response.json() == {
        "status": "busy",
        "message": "別の取込処理を実行中です。しばらくしてから再度お試しください。",
        "orders": 0,
    }
