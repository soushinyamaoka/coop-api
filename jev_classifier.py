"""Optional TypeSafe/Jev product classifier. Importing this module has no side effects."""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx

from coop_parser import classify_item

LOGGER = logging.getLogger(__name__)
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
CATEGORIES = {"食材", "調理キット", "そのまま", "離乳食", "調味料・日用品", "対象外"}
THRESHOLD = 0.8
REQUEST_TIMEOUT = 10.0
RUN_BUDGET = 15.0
MAX_CONCURRENCY = 8

QUESTION = {
    "type": "choice",
    "instructions": "生協の宅配で注文した商品名 product_name が、家庭の食材在庫アプリでどの区分に入るかを選んでください。商品名には産地・ブランド・規格・内容量や注文メモが含まれることがあります。",
    "criteria": {
        "食材": {"what": "家庭で調理して料理に使う素材。生鮮の野菜・肉・魚・卵・豆腐・油揚げ、冷凍の下処理済み素材、米・麺・パスタなど", "not_for": "味付けまで済んで温めるだけで一品になるもの", "examples": ["水菜", "豚こま切れ肉", "冷凍ブロッコリー", "木綿豆腐", "うどん"]},
        "調理キット": {"what": "ソースやタレが同梱され、少しの調理で特定の一品が完成するもの", "not_for": "料理全体の味付けに使う調味料単体、温めるだけの完成品", "examples": ["エビチリセット", "麻婆豆腐の素", "牛丼の具"]},
        "そのまま": {"what": "調理せずそのまま、または温める・焼くだけで食べられるもの。果物、乳製品、パン、お菓子、飲料、惣菜、冷凍食品", "examples": ["バナナ", "ヨーグルト", "カレーパン", "冷凍たこ焼き"]},
        "離乳食": {"what": "乳幼児・子ども向けに作られた食品", "examples": ["うらごしかぼちゃ", "ベビーダノン"]},
        "調味料・日用品": {"what": "調味料・香辛料・油・カレールー、または食品以外の日用品", "examples": ["しょうゆ", "カレールー", "サラダ油", "キッチンペーパー"]},
        "対象外": {"what": "食品や日用品の商品ではないもの。キャンペーン応募、クイズ申し込み、手続きなど"},
    },
}


def classifier_mode(environ=None) -> tuple[str, str | None]:
    """Resolve feature flags without loading dotenv or performing I/O."""
    values = os.environ if environ is None else environ
    if values.get("CATEGORY_CLASSIFIER", "keyword").strip().lower() != "jev":
        return "keyword", None
    if not values.get("TYPESAFE_API_KEY", "").strip():
        return "keyword", "api_key_missing"
    return "jev", None


def _load_cache(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return {k: v for k, v in loaded.items() if isinstance(k, str) and isinstance(v, dict)}
    except (OSError, ValueError, TypeError):
        LOGGER.warning("Jev classification cache could not be read")
    return {}


def _save_cache(path: Path, cache: dict) -> None:
    temp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temp_name = temp.name
            json.dump(cache, temp, ensure_ascii=False, indent=2)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, path)
    except OSError:
        LOGGER.warning("Jev classification cache could not be written")
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _valid_cached(value: dict) -> bool:
    return (value.get("choice") in CATEGORIES and
            isinstance(value.get("confidence"), (int, float)) and
            0 <= value["confidence"] <= 1)


async def classify_products(names: list[str], *, api_key: str, cache_path: Path,
                            overrides_path: Path | None = None,
                            client_factory=httpx.AsyncClient) -> tuple[dict, dict]:
    """Classify unique raw names, returning per-name decisions and safe counters."""
    unique_names = list(dict.fromkeys(names))
    cache = _load_cache(cache_path)
    overrides = set()
    if overrides_path:
        try:
            raw = json.loads(overrides_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                overrides = set(raw)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            LOGGER.warning("Category overrides could not be read for Jev filtering")

    decisions = {}
    pending = []
    stats = {"request_count": 0, "cache_hit_count": 0, "adopted_count": 0,
             "fallback_count": 0, "error_counts": {}}
    for name in unique_names:
        if name in overrides:
            decisions[name] = {"choice": classify_item(name), "source": "keyword"}
        elif _valid_cached(cache.get(name, {})):
            stats["cache_hit_count"] += 1
            decisions[name] = cache[name]
        else:
            pending.append(name)

    counts_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def request_one(client, name):
        async with semaphore:
            stats["request_count"] += 1
            try:
                response = await client.post(ENDPOINT, headers={"Authorization": f"Bearer {api_key}"},
                    json={"state": {"product_name": name}, "model": "jev-latest", "questions": {"category": QUESTION}})
                response.raise_for_status()
                payload = response.json()
                answer = payload["answers"]["category"]
                choice = answer["choice"]
                confidence = answer["confidence"]
                if answer.get("type") != "choice" or choice not in CATEGORIES or isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                    raise ValueError("invalid_response")
                return name, {"choice": choice, "confidence": float(confidence),
                    "model": str(payload.get("model", "jev-latest")),
                    "classified_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            except httpx.HTTPStatusError as exc:
                return name, {"error": f"http_{exc.response.status_code}"}
            except httpx.TimeoutException:
                return name, {"error": "timeout"}
            except (ValueError, KeyError, TypeError):
                return name, {"error": "invalid_response"}
            except httpx.HTTPError:
                return name, {"error": "http_error"}
            except Exception:
                return name, {"error": "internal_error"}

    if pending:
        timeout = httpx.Timeout(REQUEST_TIMEOUT)
        try:
            async with client_factory(timeout=timeout) as client:
                tasks = [asyncio.create_task(request_one(client, name)) for name in pending]
                done, still_running = await asyncio.wait(tasks, timeout=RUN_BUDGET)
                results = [task.result() for task in done if not task.cancelled() and task.exception() is None]
                for task in still_running:
                    task.cancel()
                if still_running:
                    stats["error_counts"]["budget_exceeded"] = len(still_running)
                for name, outcome in results:
                    if "error" in outcome:
                        kind = outcome["error"]
                        stats["error_counts"][kind] = stats["error_counts"].get(kind, 0) + 1
                        continue
                    cache[name] = outcome
                    decisions[name] = outcome
        except Exception:
            stats["error_counts"]["client_error"] = len(pending)
        _save_cache(cache_path, cache)

    for name in unique_names:
        result = decisions.get(name)
        if result and result.get("confidence", 0) >= THRESHOLD:
            stats["adopted_count"] += 1
            decisions[name] = {"choice": result["choice"], "source": "jev", "confidence": result["confidence"]}
        else:
            stats["fallback_count"] += 1
            decisions[name] = {"choice": classify_item(name), "source": "keyword"}
    return decisions, stats


def classify_products_sync(*args, **kwargs):
    """Sync entry point used by the existing synchronous mail worker."""
    return asyncio.run(classify_products(*args, **kwargs))
