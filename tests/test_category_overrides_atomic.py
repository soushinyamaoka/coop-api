"""Synthetic regression tests for atomic category override updates."""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

# Importing the API module normally loads the developer's .env; keep these tests
# isolated from local credentials and configuration.
import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
import coop_api_server as api


@pytest.fixture
def isolated_data(monkeypatch):
    root = Path(__file__).resolve().parent / f".category-test-{uuid.uuid4().hex}"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(api, "DATA_DIR", data_dir)
    monkeypatch.setattr(api, "CATEGORY_OVERRIDES_FILE", data_dir / "category_overrides.json")
    monkeypatch.setattr(api, "CATEGORY_OVERRIDES_LOCK", data_dir / ".category_overrides.lock")
    monkeypatch.setattr(api, "CATEGORY_OVERRIDES_TMP_DIR", data_dir / ".category_overrides_tmp")
    monkeypatch.setattr(api, "_CATEGORY_THREAD_LOCK", threading.Lock())
    try:
        yield data_dir
    finally:
        shutil.rmtree(root, ignore_errors=True)


def put(name, category):
    return api.update_classification(
        api.ClassifyRequest(original_name=name, category=category), authorization=""
    )


def test_concurrent_distinct_puts_preserve_both_and_same_name_last_write_wins(isolated_data):
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: put(f"合成商品{i}", "食材"), range(8)))
    assert len(api.load_category_overrides()) == 8

    put("同名の合成商品", "食材")
    put("同名の合成商品", "調理キット")
    assert api.load_category_overrides()["同名の合成商品"] == "調理キット"


def test_concurrent_distinct_puts_from_separate_processes_preserve_both(isolated_data):
    child_code = (
        "import dotenv; dotenv.load_dotenv=lambda *a, **k: False; "
        "import coop_api_server as api; from pathlib import Path; "
        f"api.DATA_DIR=Path({str(isolated_data)!r}); "
        "api.CATEGORY_OVERRIDES_FILE=api.DATA_DIR/'category_overrides.json'; "
        "api.CATEGORY_OVERRIDES_LOCK=api.DATA_DIR/'.category_overrides.lock'; "
        "api.CATEGORY_OVERRIDES_TMP_DIR=api.DATA_DIR/'.category_overrides_tmp'; "
        "api.update_classification(api.ClassifyRequest(original_name=ITEM, category='食材'), authorization='')"
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-c", child_code.replace("ITEM", repr(name))],
            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        for name in ("別プロセス商品A", "別プロセス商品B")
    ]
    for child in children:
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, f"child failed: {stdout} {stderr}"
    assert api.load_category_overrides() == {
        "別プロセス商品A": "食材", "別プロセス商品B": "食材"
    }


def test_lock_timeout_returns_503_without_writing(isolated_data):
    api._CATEGORY_THREAD_LOCK.acquire()
    try:
        with pytest.raises(api.HTTPException) as caught:
            put("合成商品", "食材")
    finally:
        api._CATEGORY_THREAD_LOCK.release()
    assert caught.value.status_code == 503
    assert caught.value.detail == "カテゴリ学習データが更新中です。時間をおいて再試行してください"
    assert not api.CATEGORY_OVERRIDES_FILE.exists()


def test_lock_deadline_boundary_times_out_without_negative_sleep(isolated_data, monkeypatch):
    import errno
    import types
    import sys

    win_lock = types.ModuleType("msvcrt")
    win_lock.LK_NBLCK = 1
    win_lock.LK_UNLCK = 2

    def always_busy(_fd, mode, _length):
        if mode == win_lock.LK_NBLCK:
            raise OSError(errno.EACCES, "synthetic lock contention")

    win_lock.locking = always_busy
    monkeypatch.setattr(api.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", win_lock)
    clock_values = iter([10.0, 10.999, 11.001])
    sleeps = []

    monkeypatch.setattr(api.time, "monotonic", lambda: next(clock_values))

    def checked_sleep(duration):
        assert duration >= 0
        sleeps.append(duration)

    monkeypatch.setattr(api.time, "sleep", checked_sleep)
    with pytest.raises(TimeoutError):
        with api._category_overrides_lock(timeout=1.0):
            pytest.fail("lock unexpectedly acquired")
    assert sleeps == [pytest.approx(0.001)]


def test_readers_see_complete_old_or_new_json_during_replace(isolated_data, monkeypatch):
    api.CATEGORY_OVERRIDES_FILE.write_text('{"old":"食材"}', encoding="utf-8")
    before_replace = threading.Event()
    continue_replace = threading.Event()
    real_replace = api.os.replace

    def paused_replace(source, target):
        before_replace.set()
        assert continue_replace.wait(3)
        real_replace(source, target)

    monkeypatch.setattr(api.os, "replace", paused_replace)
    writer = threading.Thread(target=api.save_category_overrides, args=({"new": "調理キット"},))
    writer.start()
    assert before_replace.wait(3)
    assert api.load_category_overrides() == {"old": "食材"}
    continue_replace.set()
    writer.join(3)
    assert not writer.is_alive()
    assert api.load_category_overrides() == {"new": "調理キット"}


@pytest.mark.parametrize("failure", [OSError("synthetic write failure"), ValueError("synthetic serialization failure")])
def test_failed_save_keeps_old_file_and_cleans_temporary_file(isolated_data, monkeypatch, failure):
    api.CATEGORY_OVERRIDES_FILE.write_text('{"old":"食材"}', encoding="utf-8")

    def fail_dump(*args, **kwargs):
        raise failure

    monkeypatch.setattr(api.json, "dump", fail_dump)
    with pytest.raises(type(failure)):
        api.save_category_overrides({"new": "調理キット"})
    assert json.loads(api.CATEGORY_OVERRIDES_FILE.read_text(encoding="utf-8")) == {"old": "食材"}
    assert list(api.CATEGORY_OVERRIDES_TMP_DIR.iterdir()) == []


def test_lock_is_released_after_exception(isolated_data):
    with pytest.raises(RuntimeError):
        with api._category_overrides_lock():
            raise RuntimeError("synthetic interruption")
    with api._category_overrides_lock(timeout=0.1):
        pass


def test_process_exit_releases_lock(isolated_data):
    code = (
        "import os, dotenv; dotenv.load_dotenv=lambda *a, **k: False; "
        "import coop_api_server as api; from pathlib import Path; "
        f"api.DATA_DIR=Path({str(isolated_data)!r}); "
        "api.CATEGORY_OVERRIDES_LOCK=api.DATA_DIR/'.category_overrides.lock'; "
        "ctx=api._category_overrides_lock(); ctx.__enter__(); "
        "print('locked', flush=True); os._exit(9)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "locked"
    assert child.wait(timeout=5) == 9
    with api._category_overrides_lock(timeout=0.2):
        pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and mode checks are unavailable")
def test_saved_file_mode_and_group_match_data_directory(isolated_data):
    api.save_category_overrides({"合成商品": "食材"})
    saved = api.CATEGORY_OVERRIDES_FILE.stat()
    assert saved.st_mode & 0o777 == 0o640
    assert saved.st_gid == isolated_data.stat().st_gid
    assert list(api.CATEGORY_OVERRIDES_TMP_DIR.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership and mode checks are unavailable")
def test_lock_creation_mode_and_group_match_data_directory(isolated_data):
    with api._category_overrides_lock():
        assert api.CATEGORY_OVERRIDES_LOCK.exists()
    lock = api.CATEGORY_OVERRIDES_LOCK.stat()
    assert lock.st_mode & 0o777 == 0o640
    assert lock.st_gid == isolated_data.stat().st_gid
