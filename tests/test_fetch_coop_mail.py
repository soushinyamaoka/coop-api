import json
import shutil
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
