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
