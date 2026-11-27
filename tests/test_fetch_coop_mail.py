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
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: FakeMail(make_email(body)))
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


def test_resolve_order_metadata_delivery_fallback_and_japan_date():
    delivery = "翌週商品配達予定日：９月３０日（水）\n合計金額(本体)：5,000円\n合計金額（税込）：５，４００円\n注文番号：1\n商品名：架空商品\n数量：1点"
    assert fetch.resolve_order_metadata(delivery, "Wed, 30 Sep 2026 16:15:22 +0900") == {
        "order_date": "2026-09-30", "order_date_source": "delivery_schedule",
        "total_amount_excluding_tax": 5000, "total_amount_tax_included": 5400,
    }
    assert fetch.resolve_order_metadata("注文番号：1", "Wed, 30 Sep 2026 23:30:00 -0000")["order_date"] == "2026-10-01"
    assert fetch.resolve_order_metadata("翌週商品配達予定日：2月30日", "Wed, 30 Sep 2026 16:15:22 +0900")["order_date_source"] == "email_date"
    assert fetch.resolve_order_metadata("注文番号：1", "bad date", now=fetch.datetime(2026, 1, 2, 0, 0))["order_date_source"] == "import_time"


def test_api_date_and_amount_fields_include_null_for_legacy(monkeypatch):
    from pathlib import Path
    with patch("dotenv.load_dotenv", return_value=False), patch.object(Path, "mkdir"):
        import coop_api_server as server
    monkeypatch.setattr(server, "verify_token", lambda _authorization: None)
    monkeypatch.setattr(server, "verify_read_token", lambda _authorization: None)
    legacy = _order("2026-09-30", "legacy")
    monkeypatch.setattr(server, "load_latest_order", lambda: legacy)
    monkeypatch.setattr(server, "load_all_orders", lambda: {"orders": [legacy]})
    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    expected = {"order_date_source": None, "total_amount_excluding_tax": None, "total_amount_tax_included": None}
    assert {key: client.get("/api/coop/ingredients").json()[key] for key in expected} == expected
    assert {key: client.get("/api/coop/orders").json()["orders"][0][key] for key in expected} == expected
    current = {**legacy, "order_date_source": "delivery_schedule", "total_amount_excluding_tax": 5000, "total_amount_tax_included": 5400}
    monkeypatch.setattr(server, "load_latest_order", lambda: current)
    monkeypatch.setattr(server, "load_all_orders", lambda: {"orders": [current]})
    current_fields = {key: value for key, value in current.items() if key in expected}
    assert {key: client.get("/api/coop/ingredients").json()[key] for key in current_fields} == current_fields
    assert {key: client.get("/api/coop/orders").json()["orders"][0][key] for key in current_fields} == current_fields


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
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail(emails))
    latest_file = data_dir / "coop_latest.json"
    latest_file.write_text(json.dumps({"sentinel": "keep"}), encoding="utf-8")

    result = fetch.run_coop_mail_import(days_back=365, save=True, backfill=True)

    assert [o["order_date"] for o in result] == ["2026-09-13", "2026-09-20"]
    assert classifier_calls == []  # 過去分取り込みではJevを呼ばない
    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [o["order_date"] for o in history] == ["2026-09-13", "2026-09-20"]
    assert json.loads(latest_file.read_text(encoding="utf-8")) == {"sentinel": "keep"}


def _job_end_status(caplog):
    ends = [r for r in caplog.records if getattr(r, "event", None) == "job_end"]
    assert len(ends) == 1
    return ends[0].event_fields["status"]


class PartiallyFailingMail(MultiFakeMail):
    """指定した番号のメッセージだけ取得に失敗するIMAP。"""

    def __init__(self, raw_emails, failing_ids):
        super().__init__(raw_emails)
        self.failing_ids = failing_ids

    def fetch(self, mail_id, *_args):
        if int(mail_id) in self.failing_ids:
            return "NO", []
        return super().fetch(mail_id, *_args)


def test_backfill_partial_fetch_failure_is_failure_and_rerun_is_idempotent(monkeypatch, tmp_path, caplog):
    data_dir, _ = setup_import(monkeypatch, tmp_path)
    emails = [
        make_dated_email("注文番号：1\n商品名：しょうゆ\n数量：1点", "Sun, 13 Sep 2026 07:00:00 +0900"),
        make_dated_email("注文番号：2\n商品名：みそ\n数量：1点", "Sun, 20 Sep 2026 07:00:00 +0900"),
        make_dated_email("注文番号：3\n商品名：さとう\n数量：1点", "Sun, 27 Sep 2026 07:00:00 +0900"),
    ]
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: PartiallyFailingMail(emails, {2}))

    result = fetch.run_coop_mail_import(days_back=365, save=True, backfill=True)

    # 取得できた分は保存するが、一部取得なのでジョブは失敗として記録する
    assert [o["order_date"] for o in result] == ["2026-09-13", "2026-09-27"]
    assert _job_end_status(caplog) == "failure"
    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [o["order_date"] for o in history] == ["2026-09-13", "2026-09-27"]

    # 失敗が解消した再実行は成功し、既に保存した注文は重複しない
    caplog.clear()
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail(emails))
    fetch.run_coop_mail_import(days_back=365, save=True, backfill=True)
    assert _job_end_status(caplog) == "success"
    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [o["order_date"] for o in history] == ["2026-09-13", "2026-09-20", "2026-09-27"]


def _run_main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["fetch_coop_mail.py", *args])
    try:
        fetch.main()
    except SystemExit as exc:
        return exc.code
    return 0


def _three_orders():
    return [
        make_dated_email("注文番号：1\n商品名：しょうゆ\n数量：1点", "Sun, 13 Sep 2026 07:00:00 +0900"),
        make_dated_email("注文番号：2\n商品名：みそ\n数量：1点", "Sun, 20 Sep 2026 07:00:00 +0900"),
        make_dated_email("注文番号：3\n商品名：さとう\n数量：1点", "Sun, 27 Sep 2026 07:00:00 +0900"),
    ]


def test_backfill_cli_exit_code_is_nonzero_on_partial_failure(monkeypatch, tmp_path):
    setup_import(monkeypatch, tmp_path)
    emails = _three_orders()
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: PartiallyFailingMail(emails, {2}))
    assert _run_main(monkeypatch, "--backfill-days", "365") == 1

    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail(emails))
    assert _run_main(monkeypatch, "--backfill-days", "365") == 0


def test_backfill_cli_exit_code_is_nonzero_when_history_cannot_be_saved(monkeypatch, tmp_path):
    data_dir, _ = setup_import(monkeypatch, tmp_path)
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail(_three_orders()))
    (data_dir / "coop_orders.json").write_text("{broken", encoding="utf-8")
    assert _run_main(monkeypatch, "--backfill-days", "365") == 1


def test_backfill_cli_busy_exits_1_but_logs_skipped_and_job_end_success(monkeypatch, tmp_path, caplog):
    """別の取込が実行中の場合、CLIは終了コード1だが、ログは「失敗」ではなく「実行しなかった」を記録する。

    busyは別プロセスとの正常な競合であり、障害ではない（cronの扱いと同じ）。
    - CLIの終了コード: 1（人が実行した過去分取込が行われていないことを、呼び出し元へ知らせる）
    - ログ: import_lock(busy) と import_skipped(reason=locked)、job_end(status=success)。job_failedは出さない
    したがって、終了コード1だけで障害と判断せず、ログのimport_skippedで「未実行」と判別する。
    """
    data_dir, _ = setup_import(monkeypatch, tmp_path)
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail(_three_orders()))
    with fetch._coop_import_lock():
        assert _run_main(monkeypatch, "--backfill-days", "365") == 1

    events = {}
    for record in caplog.records:
        event = getattr(record, "event", None)
        if event:
            events.setdefault(event, []).append(record)
    assert [r.event_fields["result"] for r in events["import_lock"]] == ["busy"]
    assert [r.event_fields["reason"] for r in events["import_skipped"]] == ["locked"]
    assert [r.event_fields["status"] for r in events["job_end"]] == ["success"]
    assert events["job_end"][0].event_fields["job"] == "coop-mail-backfill"
    assert "job_failed" not in events
    assert "external_call_started" not in events  # Gmailへ接続していない
    assert not (data_dir / "coop_orders.json").exists()  # 何も保存していない


def test_backfill_cli_rejects_non_positive_days(monkeypatch, tmp_path):
    setup_import(monkeypatch, tmp_path)
    assert _run_main(monkeypatch, "--backfill-days", "0") == 2


def test_regular_cli_exit_code_is_unchanged_on_failure(monkeypatch, tmp_path):
    """cronの通常取込は、履歴保存に失敗しても従来どおり終了コード0（成否はjob_endで判別する）。"""
    data_dir, _ = setup_import(monkeypatch, tmp_path)
    (data_dir / "coop_orders.json").write_text("{broken", encoding="utf-8")
    assert _run_main(monkeypatch) == 0


def test_regular_import_still_succeeds_when_older_candidate_fails(monkeypatch, tmp_path, caplog):
    """通常取込は最新の注文確認メールが1通取れれば成功（従来の挙動を変えない）。"""
    data_dir, _ = setup_import(monkeypatch, tmp_path)
    emails = [
        make_dated_email("注文番号：1\n商品名：しょうゆ\n数量：1点", "Sun, 13 Sep 2026 07:00:00 +0900"),
        make_dated_email("注文番号：2\n商品名：みそ\n数量：1点", "Sun, 20 Sep 2026 07:00:00 +0900"),
    ]
    # 新しい順に処理される。最新(2番)は取得でき、1番は処理されない。
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: PartiallyFailingMail(emails, {1}))

    fetch.run_coop_mail_import(days_back=14, save=True)

    assert _job_end_status(caplog) == "success"
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-09-20"


def test_unreadable_history_marks_job_failure_but_still_updates_latest(monkeypatch, tmp_path, caplog):
    data_dir, _ = setup_import(monkeypatch, tmp_path)
    history_file = data_dir / "coop_orders.json"
    history_file.write_text("{broken", encoding="utf-8")

    fetch.run_coop_mail_import(save=True)

    assert _job_end_status(caplog) == "failure"
    assert history_file.read_text(encoding="utf-8") == "{broken"  # 既存fileは上書きしない
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-09-27"
    failed = [r for r in caplog.records if getattr(r, "event", None) == "results_save_failed"]
    assert [r.event_fields["reason"] for r in failed] == ["history_unreadable"]


def test_history_write_failure_marks_job_failure_but_still_updates_latest(monkeypatch, tmp_path, caplog):
    data_dir, _ = setup_import(monkeypatch, tmp_path)
    # 履歴fileの位置にdirectoryを置き、読取は成功扱い（空履歴）・書込だけ失敗させる
    (data_dir / "coop_orders.json").mkdir()
    monkeypatch.setattr(fetch, "_load_order_history", lambda _path: [])

    fetch.run_coop_mail_import(save=True)

    assert _job_end_status(caplog) == "failure"
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-09-27"
    failed = [r for r in caplog.records if getattr(r, "event", None) == "results_save_failed"]
    assert [r.event_fields["reason"] for r in failed] == ["history_write_failed"]
    assert "error_class" in failed[0].event_fields


def test_orders_endpoint_returns_history_newest_first_with_items(monkeypatch, tmp_path):
    from pathlib import Path

    with patch("dotenv.load_dotenv", return_value=False), patch.object(Path, "mkdir"):
        import coop_api_server as server

    monkeypatch.setattr(server, "verify_token", lambda _authorization: None)
    monkeypatch.setattr(server, "verify_read_token", lambda _authorization: None)
    monkeypatch.setattr(server, "load_all_orders", lambda: {
        "last_updated": "2026-10-04T07:00:00",
        "orders": [_order("2026-09-27", "a", "しょうゆ"), _order("2026-10-04", "b", "みそ")],
    })
    monkeypatch.setattr(server, "load_category_overrides", lambda: {"みそ": "食材"})
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    summary = client.get("/api/coop/orders").json()
    assert [o["order_date"] for o in summary["orders"]] == ["2026-10-04", "2026-09-27"]
    assert summary["orders"][0]["order_date_source"] is None
    assert summary["orders"][0]["total_amount_excluding_tax"] is None
    assert summary["orders"][0]["total_amount_tax_included"] is None
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
    monkeypatch.setattr(server, "verify_read_token", lambda _authorization: None)
    monkeypatch.setattr(
        server,
        "import_coop_mail",
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


class LoginFailingMail(FakeMail):
    def login(self, *_args):
        raise fetch.imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")


def _server_client(monkeypatch):
    from pathlib import Path

    with patch("dotenv.load_dotenv", return_value=False), patch.object(Path, "mkdir"):
        import coop_api_server as server

    monkeypatch.setattr(server, "verify_token", lambda _authorization: None)
    monkeypatch.setattr(server, "verify_read_token", lambda _authorization: None)
    from fastapi.testclient import TestClient

    return server, TestClient(server.app)


def _fetch_status(data_dir):
    return json.loads((data_dir / "coop_fetch_status.json").read_text(encoding="utf-8"))


def test_login_failure_is_reported_as_error_not_no_data(monkeypatch, tmp_path):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: LoginFailingMail(make_email()))
    _server, client = _server_client(monkeypatch)

    response = client.post("/api/coop/fetch")

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "error"
    assert body["error_kind"] == "auth"
    assert "ログインできませんでした" in body["detail"]
    status = _fetch_status(data_dir)
    assert status["last_result"] == "failure"
    assert status["last_error_kind"] == "auth"
    assert status["consecutive_failures"] == 1
    assert status["last_success_at"] is None


@pytest.mark.parametrize("error, kind", [(OSError("down"), "connection"), (TimeoutError(), "connection"), (ValueError(), "internal")])
def test_connection_and_internal_failures_have_their_kind(monkeypatch, tmp_path, error, kind):
    setup_import(monkeypatch, tmp_path)

    def broken(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", broken)
    outcome = fetch.import_coop_mail(save=True)
    assert (outcome.results, outcome.succeeded, outcome.error_kind) == ([], False, kind)


def test_no_order_mail_is_still_no_data_and_success(monkeypatch, tmp_path):
    data_dir, _calls = setup_import(monkeypatch, tmp_path, body="広告メールです")
    _server, client = _server_client(monkeypatch)

    response = client.post("/api/coop/fetch")

    assert response.status_code == 200
    assert response.json()["status"] == "no_data"
    assert _fetch_status(data_dir)["last_result"] == "success"


def test_fetch_status_counts_failures_and_resets_on_success(monkeypatch, tmp_path):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: LoginFailingMail(make_email()))
    fetch.run_coop_mail_import(save=True, skip_if_unchanged=True)  # 定期取込（cron）
    fetch.run_coop_mail_import(save=True, skip_if_unchanged=True)
    assert _fetch_status(data_dir)["consecutive_failures"] == 2

    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: FakeMail(make_email()))
    fetch.run_coop_mail_import(save=True, skip_if_unchanged=True)
    status = _fetch_status(data_dir)
    assert status["last_result"] == "success"
    assert status["last_error_kind"] is None
    assert status["consecutive_failures"] == 0
    assert status["last_success_at"] == status["last_attempt_at"]


def test_backfill_and_busy_do_not_record_fetch_status(monkeypatch, tmp_path):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    fetch.run_coop_mail_import(days_back=30, save=True, backfill=True)
    assert not (data_dir / "coop_fetch_status.json").exists()

    with fetch._coop_import_lock():
        assert fetch.run_coop_mail_import(save=True, skip_if_unchanged=True) == []
    assert not (data_dir / "coop_fetch_status.json").exists()


def test_fetch_status_write_failure_does_not_change_result(monkeypatch, tmp_path, caplog):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    real_replace = fetch.os.replace

    def replace(src, dst):
        if str(dst).endswith("coop_fetch_status.json"):
            raise OSError("read-only")
        return real_replace(src, dst)

    monkeypatch.setattr(fetch.os, "replace", replace)

    outcome = fetch.import_coop_mail(save=True)

    assert outcome.succeeded and len(outcome.results) == 1
    failed = [r for r in caplog.records if getattr(r, "event", None) == "results_save_failed"]
    assert [r.event_fields["target"] for r in failed] == ["coop_fetch_status"]
    assert not list(data_dir.glob(".coop_fetch_status.json.*.tmp"))


def test_ingredients_returns_fetch_status_or_null(monkeypatch, tmp_path):
    server, client = _server_client(monkeypatch)
    monkeypatch.setattr(server, "load_latest_order", lambda: _order("2026-11-18", "a"))
    monkeypatch.setattr(server, "load_fetch_status", lambda: None)
    assert client.get("/api/coop/ingredients").json()["fetch_status"] is None

    recorded = {"last_attempt_at": "2026-11-18T07:00:00+09:00", "last_result": "failure", "last_error_kind": "auth",
                "last_success_at": "2026-10-10T07:00:00+09:00", "consecutive_failures": 78}
    monkeypatch.setattr(server, "load_fetch_status", lambda: recorded)
    assert client.get("/api/coop/ingredients").json()["fetch_status"] == recorded


def test_older_order_does_not_replace_latest_but_is_added_to_history(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    fetch.save_results([_order("2026-11-18", "new")])
    fetch.save_results([_order("2026-10-21", "old")])  # 古い注文メールを受け取り直した

    latest = json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))
    assert latest["order_date"] == "2026-11-18"
    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [o["order_date"] for o in history] == ["2026-10-21", "2026-11-18"]

    fetch.save_results([_order("2026-11-18", "changed")])  # 同じ週の注文を変更したメールは最新にする
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["source_fingerprint"] == "changed"


def test_cron_skips_older_order_mail_without_classifying(monkeypatch, tmp_path):
    body = "翌週商品配達予定日：１０月２１日（水）\n注文番号：1\n商品名：しょうゆ\n数量：1点"
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path, body=body)
    latest_file = data_dir / "coop_latest.json"
    latest_file.write_text(json.dumps({"order_date": "2026-11-18", "source_fingerprint": "new"}), encoding="utf-8")
    before = latest_file.read_bytes()

    assert fetch.run_coop_mail_import(save=True, skip_if_unchanged=True) == []
    assert classifier_calls == []
    assert latest_file.read_bytes() == before
    assert _fetch_status(data_dir)["last_result"] == "success"


# ─── 通知008の差し戻し対応（保存失敗・古い再送メール・IMAPの切断） ───────────────


def _order_mail(number, name, date):
    return make_dated_email(f"注文番号：{number}\n商品名：{name}\n数量：1点", date)


A_DATE = "Wed, 11 Nov 2026 07:00:00 +0900"
B_DATE = "Wed, 18 Nov 2026 07:00:00 +0900"


def _save_latest_from(data_dir, raw_email):
    """そのメールを取り込んだ状態（最新注文）を作る。"""
    import email as email_lib

    msg = email_lib.message_from_bytes(raw_email)
    body = fetch.get_email_body(msg)
    order = {"source_fingerprint": source_fingerprint(body), **fetch.resolve_order_metadata(body, msg["Date"])}
    (data_dir / "coop_latest.json").write_text(json.dumps(order), encoding="utf-8")
    return order


@pytest.mark.parametrize("skip_if_unchanged", [True, False])
def test_resent_old_order_does_not_hide_newer_order(monkeypatch, tmp_path, skip_if_unchanged):
    """最新注文Aの後に新しい注文Bが届き、その後にAが再送されても、Bを取り込む（定期・手動とも）。"""
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    a = _order_mail(1, "しょうゆ", A_DATE)
    b = _order_mail(2, "みそ", B_DATE)
    _save_latest_from(data_dir, a)
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([a, b, a]))

    result = fetch.run_coop_mail_import(save=True, skip_if_unchanged=skip_if_unchanged)

    assert [o["order_date"] for o in result] == ["2026-11-18"]
    assert classifier_calls == [["みそ"]]
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-11-18"


@pytest.mark.parametrize("skip_if_unchanged", [True, False])
def test_older_week_resend_after_latest_is_skipped_without_classifying(monkeypatch, tmp_path, skip_if_unchanged, caplog):
    """最新注文Bの後に前の週の注文Aが再送されたとき、Aは分類も保存もしない。手動ではBを取り込み直す。"""
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    a = _order_mail(1, "しょうゆ", A_DATE)
    b = _order_mail(2, "みそ", B_DATE)
    _save_latest_from(data_dir, b)
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([b, a]))

    result = fetch.run_coop_mail_import(save=True, skip_if_unchanged=skip_if_unchanged)

    skipped = [r.event_fields.get("reason") for r in caplog.records if getattr(r, "event", None) == "email_candidate_skipped"]
    assert "older_than_latest" in skipped
    if skip_if_unchanged:
        assert result == [] and classifier_calls == []
    else:
        assert [o["order_date"] for o in result] == ["2026-11-18"]
        assert classifier_calls == [["みそ"]]
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-11-18"
    assert _job_end_status(caplog) == "success"
    assert _fetch_status(data_dir)["last_result"] == "success"


def test_only_older_order_mail_is_no_data_for_manual_fetch(monkeypatch, tmp_path):
    """最新注文より前の週の注文メールしか無いとき、手動取得は no_data（古い注文で最新を上書きしない）。"""
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    latest_file = data_dir / "coop_latest.json"
    latest_file.write_text(json.dumps({"order_date": "2026-11-25", "source_fingerprint": "newest"}), encoding="utf-8")
    before = latest_file.read_bytes()
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([_order_mail(1, "しょうゆ", A_DATE)]))
    _server, client = _server_client(monkeypatch)

    response = client.post("/api/coop/fetch")

    assert response.status_code == 200
    assert response.json()["status"] == "no_data"
    assert classifier_calls == []
    assert latest_file.read_bytes() == before


def test_same_week_version_received_before_latest_is_not_imported(monkeypatch, tmp_path):
    """同じ週の注文を変更したとき、変更前の版（最新注文より前に届いたもの）で最新注文を戻さない。"""
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    before_change = _order_mail(1, "しょうゆ", B_DATE)
    after_change = _order_mail(1, "みそ", "Wed, 18 Nov 2026 09:00:00 +0900")
    saved = _save_latest_from(data_dir, after_change)
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([before_change, after_change]))

    assert fetch.run_coop_mail_import(save=True, skip_if_unchanged=True) == []
    assert classifier_calls == []
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8")) == saved


def test_same_week_change_received_after_latest_is_imported(monkeypatch, tmp_path):
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    before_change = _order_mail(1, "しょうゆ", B_DATE)
    after_change = _order_mail(1, "みそ", "Wed, 18 Nov 2026 09:00:00 +0900")
    _save_latest_from(data_dir, before_change)
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([before_change, after_change]))

    result = fetch.run_coop_mail_import(save=True, skip_if_unchanged=True)

    assert classifier_calls == [["みそ"]]
    assert len(result) == 1
    latest = json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))
    assert latest["source_fingerprint"] == result[0]["source_fingerprint"]


def test_history_save_failure_returns_502_save_and_records_failure(monkeypatch, tmp_path):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    (data_dir / "coop_orders.json").write_text("{broken", encoding="utf-8")
    _server, client = _server_client(monkeypatch)

    response = client.post("/api/coop/fetch")

    assert response.status_code == 502
    assert response.json()["status"] == "error"
    assert response.json()["error_kind"] == "save"
    status = _fetch_status(data_dir)
    assert (status["last_result"], status["last_error_kind"], status["consecutive_failures"]) == ("failure", "save", 1)
    assert status["last_success_at"] is None


def test_latest_write_failure_keeps_previous_latest_and_returns_502_save(monkeypatch, tmp_path, caplog):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    latest_file = data_dir / "coop_latest.json"
    latest_file.write_text(json.dumps({"order_date": "2026-09-20", "source_fingerprint": "old"}), encoding="utf-8")
    before = latest_file.read_bytes()
    real_replace = fetch.os.replace

    def replace(src, dst):
        if str(dst).endswith("coop_latest.json"):
            raise PermissionError("denied")
        return real_replace(src, dst)

    monkeypatch.setattr(fetch.os, "replace", replace)
    _server, client = _server_client(monkeypatch)

    response = client.post("/api/coop/fetch")

    assert response.status_code == 502
    assert response.json()["error_kind"] == "save"
    assert latest_file.read_bytes() == before  # 元の最新注文はそのまま
    assert not list(data_dir.glob(".*.tmp"))  # 一時ファイルは残さない
    assert _fetch_status(data_dir)["last_error_kind"] == "save"
    failed = [r.event_fields for r in caplog.records if getattr(r, "event", None) == "results_save_failed"]
    assert [(f["target"], f["reason"]) for f in failed] == [("coop_latest", "latest_write_failed")]
    # 履歴は保存できている（前回の最新注文が履歴に無かったので一緒に入る。再実行しても重複しない）
    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [o["order_date"] for o in history] == ["2026-09-20", "2026-09-27"]


def test_history_write_failure_keeps_previous_history_file(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    fetch.save_results([_order("2026-09-20", "a")])
    history_file = data_dir / "coop_orders.json"
    before = history_file.read_bytes()
    real_dump = fetch.json.dump

    def broken_dump(data, f, **kwargs):
        if isinstance(data, dict) and "orders" in data:
            f.write('{"orders": [')  # 途中まで書いてから失敗する
            raise OSError("disk full")
        return real_dump(data, f, **kwargs)

    monkeypatch.setattr(fetch.json, "dump", broken_dump)
    assert fetch.save_results([_order("2026-09-27", "b")]) is False
    assert history_file.read_bytes() == before
    assert not list(data_dir.glob(".*.tmp"))


def test_unexpected_save_error_returns_502_internal_and_records_failure(monkeypatch, tmp_path):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    monkeypatch.setattr(fetch, "save_results", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bug")))
    _server, client = _server_client(monkeypatch)

    response = client.post("/api/coop/fetch")

    assert response.status_code == 502
    assert response.json()["error_kind"] == "internal"
    assert _fetch_status(data_dir)["last_error_kind"] == "internal"


class AbortingMail(MultiFakeMail):
    """指定した操作で接続が切れるIMAP。"""

    def __init__(self, raw_emails, abort_on, abort_after=0):
        super().__init__(raw_emails)
        self.abort_on = abort_on
        self.abort_after = abort_after
        self.fetch_count = 0

    def search(self, *args):
        if self.abort_on == "search":
            raise fetch.imaplib.IMAP4.abort("socket error: EOF")
        return super().search(*args)

    def fetch(self, mail_id, *args):
        self.fetch_count += 1
        if self.abort_on == "fetch" and self.fetch_count > self.abort_after:
            raise fetch.imaplib.IMAP4.abort("socket error: EOF")
        return super().fetch(mail_id, *args)

    def logout(self):
        raise fetch.imaplib.IMAP4.abort("socket error: EOF")


class ResetDuringFetchMail(MultiFakeMail):
    def fetch(self, *_args):
        raise ConnectionResetError("reset by peer")


class ErrorAfterLoginMail(MultiFakeMail):
    def search(self, *_args):
        raise fetch.imaplib.IMAP4.error("SEARCH command error: BAD")


@pytest.mark.parametrize("mail_factory", [
    lambda emails: AbortingMail(emails, "search"),
    lambda emails: AbortingMail(emails, "fetch"),
    lambda emails: ResetDuringFetchMail(emails),
    lambda emails: ErrorAfterLoginMail(emails),
], ids=["abort_on_search", "abort_on_fetch", "reset_on_fetch", "error_after_login"])
def test_disconnect_after_login_is_connection_not_auth(monkeypatch, tmp_path, mail_factory):
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    emails = _three_orders()
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: mail_factory(emails))
    _server, client = _server_client(monkeypatch)

    response = client.post("/api/coop/fetch")

    assert response.status_code == 502
    assert response.json()["error_kind"] == "connection"
    assert _fetch_status(data_dir)["last_error_kind"] == "connection"
    assert classifier_calls == []
    assert not (data_dir / "coop_latest.json").exists()


def test_disconnect_during_login_is_connection(monkeypatch, tmp_path):
    setup_import(monkeypatch, tmp_path)

    class LoginAbortMail(FakeMail):
        def login(self, *_args):
            raise fetch.imaplib.IMAP4.abort("socket error: EOF")

    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: LoginAbortMail(make_email()))
    assert fetch.import_coop_mail(save=True).error_kind == "connection"


def test_backfill_disconnect_keeps_fetched_orders_and_fails(monkeypatch, tmp_path, caplog):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    emails = _three_orders()
    # 新しい順に処理する。1通目（9/27）を取得した後で切断される
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: AbortingMail(emails, "fetch", abort_after=1))

    result = fetch.run_coop_mail_import(days_back=365, save=True, backfill=True)

    assert [o["order_date"] for o in result] == ["2026-09-27"]
    assert _job_end_status(caplog) == "failure"
    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [o["order_date"] for o in history] == ["2026-09-27"]


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX mode checks are unavailable")
def test_atomic_save_keeps_existing_file_mode(monkeypatch, tmp_path):
    import os

    data_dir = tmp_path / "data"
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    fetch.save_results([_order("2026-09-20", "a")])
    for name in ("coop_orders.json", "coop_latest.json"):
        os.chmod(data_dir / name, 0o640)
    old_umask = os.umask(0o022)
    try:
        fetch.save_results([_order("2026-09-27", "b")])
    finally:
        os.umask(old_umask)
    for name in ("coop_orders.json", "coop_latest.json"):
        assert (data_dir / name).stat().st_mode & 0o777 == 0o640
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-09-27"


# ─── 通知008の再レビュー対応（排他・履歴の再試行・同日旧版・IMAPの初期化・状態ファイルの権限） ───


@pytest.mark.parametrize("cause", ["success", "internal"])
def test_fetch_status_is_recorded_while_import_lock_is_held(monkeypatch, tmp_path, cause):
    """取込状況の読み書きは、取込と同じ lock の中で行う（想定外の例外のときも）。"""
    _data_dir, _calls = setup_import(monkeypatch, tmp_path)
    if cause == "internal":
        monkeypatch.setattr(fetch, "save_results", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("bug")))
    held = []
    real_record = fetch.record_fetch_status

    def record(*args, **kwargs):
        try:
            with fetch._coop_import_lock():
                held.append(False)
        except fetch.ImportBusyError:
            held.append(True)
        return real_record(*args, **kwargs)

    monkeypatch.setattr(fetch, "record_fetch_status", record)
    if cause == "internal":
        with pytest.raises(RuntimeError):
            fetch.import_coop_mail(save=True)
    else:
        fetch.import_coop_mail(save=True)
    assert held == [True]


def test_order_missing_from_history_is_retried_on_next_import(monkeypatch, tmp_path, caplog):
    """履歴の保存に失敗して最新注文だけ保存された注文は、次の取込（変化なし）で履歴に入れ直す。"""
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    history_file = data_dir / "coop_orders.json"
    history_file.write_text("{broken", encoding="utf-8")
    assert fetch.import_coop_mail(save=True, skip_if_unchanged=True).error_kind == "save"
    assert _fetch_status(data_dir)["last_error_kind"] == "save"

    history_file.unlink()  # 原因が解消した
    classifier_calls.clear()
    outcome = fetch.import_coop_mail(save=True, skip_if_unchanged=True)

    assert outcome.succeeded and outcome.results == []
    assert classifier_calls == []  # 変化なしなので分類はしない
    history = json.loads(history_file.read_text(encoding="utf-8"))["orders"]
    assert [o["order_date"] for o in history] == ["2026-09-27"]
    assert _fetch_status(data_dir)["last_result"] == "success"
    assert any(getattr(r, "event", None) == "history_retry" for r in caplog.records)

    caplog.clear()
    fetch.import_coop_mail(save=True, skip_if_unchanged=True)  # 入った後は何もしない
    assert not any(getattr(r, "event", None) == "history_retry" for r in caplog.records)
    assert len(json.loads(history_file.read_text(encoding="utf-8"))["orders"]) == 1


def test_history_retry_failure_is_save_failure(monkeypatch, tmp_path):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    (data_dir / "coop_orders.json").write_text("{broken", encoding="utf-8")
    fetch.import_coop_mail(save=True, skip_if_unchanged=True)

    outcome = fetch.import_coop_mail(save=True, skip_if_unchanged=True)  # まだ履歴が壊れたまま

    assert (outcome.succeeded, outcome.error_kind) == (False, "save")
    assert _fetch_status(data_dir)["consecutive_failures"] == 2


def test_manual_no_data_also_retries_history(monkeypatch, tmp_path):
    data_dir, _calls = setup_import(monkeypatch, tmp_path, body="広告メールです")
    (data_dir / "coop_latest.json").write_text(json.dumps(_order("2026-11-18", "x")), encoding="utf-8")
    _server, client = _server_client(monkeypatch)

    response = client.post("/api/coop/fetch")

    assert response.status_code == 200 and response.json()["status"] == "no_data"
    history = json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]
    assert [o["source_fingerprint"] for o in history] == ["x"]


@pytest.mark.parametrize("arrivals", ["resent_after_latest", "only_old_version"])
def test_same_day_old_version_never_replaces_latest(monkeypatch, tmp_path, arrivals):
    """同じ週の変更前の版は、最新注文の後に再送されても・最新注文のメールが見つからなくても、最新注文を戻さない。"""
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    before_change = _order_mail(1, "しょうゆ", B_DATE)
    after_change = _order_mail(1, "みそ", "Wed, 18 Nov 2026 09:00:00 +0900")
    saved = _save_latest_from(data_dir, after_change)
    saved["email_date"] = "Wed, 18 Nov 2026 09:00:00 +0900"
    (data_dir / "coop_latest.json").write_text(json.dumps(saved), encoding="utf-8")
    mails = [after_change, before_change] if arrivals == "resent_after_latest" else [before_change]
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail(mails))

    assert fetch.run_coop_mail_import(save=True, skip_if_unchanged=True) == []
    assert classifier_calls == []
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8")) == saved


def test_same_day_newer_version_is_imported_even_if_latest_mail_is_missing(monkeypatch, tmp_path):
    data_dir, classifier_calls = setup_import(monkeypatch, tmp_path)
    before_change = _order_mail(1, "しょうゆ", B_DATE)
    after_change = _order_mail(1, "みそ", "Wed, 18 Nov 2026 09:00:00 +0900")
    saved = _save_latest_from(data_dir, before_change)
    saved["email_date"] = B_DATE
    (data_dir / "coop_latest.json").write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([after_change]))

    result = fetch.run_coop_mail_import(save=True, skip_if_unchanged=True)

    assert classifier_calls == [["みそ"]] and len(result) == 1


def test_imap_connection_uses_timeout(monkeypatch, tmp_path):
    setup_import(monkeypatch, tmp_path)
    seen = {}

    def connect(*args, **kwargs):
        seen.update(kwargs)
        return FakeMail(make_email())

    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", connect)
    fetch.import_coop_mail(save=False)
    assert seen.get("timeout") == fetch.IMAP_TIMEOUT_SECONDS


@pytest.mark.parametrize("error", [
    fetch.imaplib.IMAP4.error("unexpected greeting"),
    fetch.imaplib.IMAP4.abort("socket error: EOF"),
    __import__("socket").timeout("timed out"),
    ConnectionRefusedError(),
], ids=["greeting_error", "greeting_abort", "timeout", "refused"])
def test_imap_initialization_failure_is_connection_not_auth(monkeypatch, tmp_path, error):
    data_dir, _calls = setup_import(monkeypatch, tmp_path)

    def connect(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", connect)
    outcome = fetch.import_coop_mail(save=True)
    assert (outcome.succeeded, outcome.error_kind) == (False, "connection")
    assert _fetch_status(data_dir)["last_error_kind"] == "connection"


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX mode checks are unavailable")
def test_new_data_files_are_created_0640_regardless_of_umask(monkeypatch, tmp_path):
    import os

    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    old_umask = os.umask(0o002)
    try:
        fetch.import_coop_mail(save=True)
    finally:
        os.umask(old_umask)
    for name in ("coop_fetch_status.json", "coop_orders.json", "coop_latest.json"):
        assert (data_dir / name).stat().st_mode & 0o777 == 0o640, name

    os.chmod(data_dir / "coop_fetch_status.json", 0o600)  # 既存の mode は引き継ぐ
    fetch.import_coop_mail(save=True)
    assert (data_dir / "coop_fetch_status.json").stat().st_mode & 0o777 == 0o600
    assert not list(data_dir.glob(".*.tmp"))



# ─── 通知008の再々レビュー対応（新着があっても前回分の履歴を復旧する） ───


def _history_dates(data_dir):
    return [o["order_date"] for o in json.loads((data_dir / "coop_orders.json").read_text(encoding="utf-8"))["orders"]]


@pytest.mark.parametrize("skip_if_unchanged", [True, False])
def test_previous_order_is_recovered_into_history_when_next_import_has_new_order(monkeypatch, tmp_path, skip_if_unchanged):
    """注文Aの履歴保存だけ失敗した後、次の取込で新しい注文Bが届いても、AとBの両方が履歴に入る。"""
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    a = _order_mail(1, "しょうゆ", A_DATE)
    b = _order_mail(2, "みそ", B_DATE)
    history_file = data_dir / "coop_orders.json"
    history_file.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([a]))
    assert fetch.import_coop_mail(save=True, skip_if_unchanged=skip_if_unchanged).error_kind == "save"
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-11-11"

    history_file.unlink()  # 原因が解消した
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([a, b]))
    outcome = fetch.import_coop_mail(save=True, skip_if_unchanged=skip_if_unchanged)

    assert outcome.succeeded
    assert _history_dates(data_dir) == ["2026-11-11", "2026-11-18"]
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-11-18"
    assert _fetch_status(data_dir)["last_result"] == "success"


def test_latest_is_not_advanced_while_previous_order_cannot_enter_history(monkeypatch, tmp_path, caplog):
    """履歴が保存できないまま新しい注文Bが届いたら、Aを失わないよう最新注文はAのままにし、復旧後にAとBを保存する。"""
    data_dir, _calls = setup_import(monkeypatch, tmp_path)
    a = _order_mail(1, "しょうゆ", A_DATE)
    b = _order_mail(2, "みそ", B_DATE)
    history_file = data_dir / "coop_orders.json"
    history_file.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([a]))
    fetch.import_coop_mail(save=True, skip_if_unchanged=True)
    latest_a = (data_dir / "coop_latest.json").read_bytes()

    monkeypatch.setattr(fetch.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: MultiFakeMail([a, b]))
    _server, client = _server_client(monkeypatch)
    response = client.post("/api/coop/fetch")  # まだ履歴が壊れたまま

    assert response.status_code == 502 and response.json()["error_kind"] == "save"
    assert (data_dir / "coop_latest.json").read_bytes() == latest_a
    assert history_file.read_text(encoding="utf-8") == "{broken"
    skipped = [r.event_fields for r in caplog.records if getattr(r, "event", None) == "results_save_skipped"]
    assert {"target": "coop_latest", "reason": "history_pending"} in skipped
    assert _fetch_status(data_dir)["consecutive_failures"] == 2

    history_file.unlink()
    assert fetch.import_coop_mail(save=True, skip_if_unchanged=True).succeeded
    assert _history_dates(data_dir) == ["2026-11-11", "2026-11-18"]
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-11-18"


def test_latest_still_advances_when_previous_order_is_already_in_history(monkeypatch, tmp_path):
    """前回分が履歴にあるなら、履歴の保存に失敗しても最新注文は従来どおり新しい注文にする。"""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    fetch.save_results([_order("2026-11-11", "a")])
    real_write = fetch._write_json_atomic

    def write(path, data):
        if path.name == "coop_orders.json":
            raise OSError("disk full")
        return real_write(path, data)

    monkeypatch.setattr(fetch, "_write_json_atomic", write)
    assert fetch.save_results([_order("2026-11-18", "b")]) is False
    assert json.loads((data_dir / "coop_latest.json").read_text(encoding="utf-8"))["order_date"] == "2026-11-18"
    assert _history_dates(data_dir) == ["2026-11-11"]
