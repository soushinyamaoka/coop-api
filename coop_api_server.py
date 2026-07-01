"""
COOP連携APIサーバー（ポート8003）
- GET  /api/coop/ingredients           : 最新の食材リスト取得
- GET  /api/coop/orders                : 全注文一覧
- POST /api/coop/fetch                 : 手動でメール取得トリガー
- POST /api/coop/suggest-recipes       : 選択食材からレシピ提案（8001/8002に転送）
- POST /api/coop/meal-plan             : 献立作成（食材がなくなるまで最大7日）
- POST /api/coop/meals                 : メニュー登録（レシピ省略可）
- GET  /api/coop/meals                 : 登録済みメニュー一覧
- PUT  /api/coop/meals/{name}/recipe   : メニューへのレシピ追加・更新
- DELETE /api/coop/meals/{name}/recipe : メニューからレシピのみ削除
- DELETE /api/coop/meals/{name}        : メニューをレシピごと削除
- PUT  /api/coop/classify              : 商品カテゴリの手動修正（学習用）
"""

import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from coop_parser import parse_coop_email, classify_item
from fetch_coop_mail import fetch_coop_emails, save_results

# ============================================================
# 設定
# ============================================================

load_dotenv()

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8003"))
API_TOKEN = os.getenv("API_TOKEN", "")

# 既存APIサーバーのURL
RECIPE_GENERATE_URL = os.getenv("RECIPE_GENERATE_URL", "http://localhost:8001/api/recipes/generate")
RECIPE_SEARCH_URL = os.getenv("RECIPE_SEARCH_URL", "http://localhost:8002/api/recipes/search")

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# カテゴリ学習データ保存先
CATEGORY_OVERRIDES_FILE = DATA_DIR / "category_overrides.json"

# ユーザー登録メニュー保存先
CUSTOM_MEALS_FILE = DATA_DIR / "custom_meals.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# FastAPI アプリ
# ============================================================

app = FastAPI(
    title="COOP連携API",
    description="COOPデリの注文情報からレシピを提案するAPI",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 認証（シンプルなトークン方式）
# ============================================================

def verify_token(authorization: str = Header(default="")):
    """APIトークンの検証（設定されている場合のみ）"""
    if not API_TOKEN:
        return  # トークン未設定なら認証スキップ
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="認証エラー: 無効なトークンです")


# ============================================================
# カテゴリ学習データの読み書き
# ============================================================

def load_category_overrides() -> dict:
    """ユーザーが修正したカテゴリ分類を読み込む"""
    if CATEGORY_OVERRIDES_FILE.exists():
        with open(CATEGORY_OVERRIDES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_category_overrides(overrides: dict) -> None:
    """カテゴリ修正データを保存する"""
    with open(CATEGORY_OVERRIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)


def apply_category_overrides(items: list[dict]) -> list[dict]:
    """学習済みカテゴリで上書きする"""
    overrides = load_category_overrides()
    for item in items:
        original_name = item.get("original_name", "")
        if original_name in overrides:
            item["category"] = overrides[original_name]
            item["category_source"] = "user_override"
        else:
            item["category_source"] = "auto"
    return items


# ============================================================
# データ読み込みヘルパー
# ============================================================

def load_latest_order() -> dict | None:
    """最新の注文データを読み込む"""
    latest_file = DATA_DIR / "coop_latest.json"
    if not latest_file.exists():
        return None
    with open(latest_file, "r", encoding="utf-8") as f:
        return json.load(f)


def load_custom_meals() -> dict:
    """ユーザー登録メニューを読み込む"""
    if CUSTOM_MEALS_FILE.exists():
        with open(CUSTOM_MEALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_custom_meals(meals: dict) -> None:
    """ユーザー登録メニューを保存する"""
    with open(CUSTOM_MEALS_FILE, "w", encoding="utf-8") as f:
        json.dump(meals, f, ensure_ascii=False, indent=2)


def load_all_orders() -> dict | None:
    """全注文データを読み込む"""
    all_file = DATA_DIR / "coop_orders.json"
    if not all_file.exists():
        return None
    with open(all_file, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# リクエスト/レスポンスモデル
# ============================================================

class SuggestRecipesRequest(BaseModel):
    ingredients: list[str]  # 選択された食材名のリスト
    season: str = ""        # 季節（春/夏/秋/冬、空なら自動判定）
    genre: str = ""         # ジャンル（和食/洋食/中華 等）
    servings: int = 2       # 人数
    mode: str = "both"      # "generate" | "search" | "both"


class MealPlanRequest(BaseModel):
    ingredients: list[str]  # 選択された食材名のリスト
    season: str = ""        # 季節（春/夏/秋/冬、空なら自動判定）
    servings: int = 2       # 人数
    simple_mode: bool = False  # True: シンプルなレシピを優先


class ClassifyRequest(BaseModel):
    original_name: str      # 元の商品名
    category: str           # 修正後のカテゴリ


class CustomRecipe(BaseModel):
    time: str = ""
    difficulty: str = ""
    calories: str = ""
    ingredients: list[str] = []
    steps: list[str] = []
    tips: list[str] = []


class RegisterMealRequest(BaseModel):
    name: str               # メニュー名
    recipe: CustomRecipe | None = None  # レシピ（省略可）


# ============================================================
# エンドポイント
# ============================================================

@app.get("/")
def root():
    return {
        "service": "COOP連携API",
        "version": "1.0.0",
        "endpoints": [
            "GET  /api/coop/ingredients",
            "GET  /api/coop/orders",
            "POST /api/coop/fetch",
            "POST /api/coop/suggest-recipes",
            "POST /api/coop/meal-plan",
            "POST /api/coop/meals",
            "GET  /api/coop/meals",
            "PUT  /api/coop/meals/{name}/recipe",
            "DELETE /api/coop/meals/{name}/recipe",
            "DELETE /api/coop/meals/{name}",
            "PUT  /api/coop/classify",
        ],
    }


@app.get("/api/coop/ingredients")
def get_ingredients(authorization: str = Header(default="")):
    """
    最新の注文から食材リストを返す
    カテゴリ別に分類済み、学習データによる上書き適用済み
    """
    verify_token(authorization)

    order = load_latest_order()
    if not order:
        raise HTTPException(status_code=404, detail="注文データがありません。先に /api/coop/fetch でメールを取得してください")

    # カテゴリ学習を適用
    all_items = (
        order.get("ingredients", [])
        + order.get("kits", [])
        + order.get("ready_to_eat", [])
        + order.get("baby_food", [])
        + order.get("seasonings", [])
    )
    all_items = apply_category_overrides(all_items)

    # カテゴリ別に再分類
    categorized = {
        "ingredients": [],
        "kits": [],
        "ready_to_eat": [],
        "baby_food": [],
        "seasonings": [],
    }

    category_map = {
        "食材": "ingredients",
        "調理キット": "kits",
        "そのまま": "ready_to_eat",
        "離乳食": "baby_food",
        "調味料・日用品": "seasonings",
    }

    for item in all_items:
        key = category_map.get(item["category"], "ingredients")
        categorized[key].append(item)

    return {
        "order_date": order.get("order_date", ""),
        "parsed_at": order.get("parsed_at", ""),
        **categorized,
        "excluded": order.get("excluded", []),
    }


@app.get("/api/coop/orders")
def get_orders(authorization: str = Header(default="")):
    """全注文一覧を返す"""
    verify_token(authorization)

    data = load_all_orders()
    if not data:
        raise HTTPException(status_code=404, detail="注文データがありません")

    # 概要のみ返す（詳細は /ingredients で取得）
    summaries = []
    for order in data.get("orders", []):
        summaries.append({
            "order_date": order.get("order_date", ""),
            "email_subject": order.get("email_subject", ""),
            "total_items": order.get("total_items", 0),
            "ingredient_count": len(order.get("ingredients", [])),
            "kit_count": len(order.get("kits", [])),
        })

    return {
        "last_updated": data.get("last_updated", ""),
        "order_count": len(summaries),
        "orders": summaries,
    }


@app.post("/api/coop/fetch")
def fetch_emails(
    days_back: int = Query(default=14, ge=1, le=90),
    authorization: str = Header(default=""),
):
    """
    メールを手動取得する（通常はcronで自動実行）
    """
    verify_token(authorization)

    logger.info(f"メール手動取得: 過去{days_back}日分")
    results = fetch_coop_emails(days_back=days_back)

    if results:
        save_results(results)
        return {
            "status": "success",
            "message": f"{len(results)}件の注文を取得しました",
            "orders": len(results),
        }
    else:
        return {
            "status": "no_data",
            "message": "COOPからの注文確認メールが見つかりませんでした",
        }


@app.post("/api/coop/suggest-recipes")
async def suggest_recipes(
    request: SuggestRecipesRequest,
    authorization: str = Header(default=""),
):
    """
    選択された食材からレシピを提案する
    既存の8001（AI生成）/ 8002（Web検索）に内部転送
    """
    verify_token(authorization)

    if not request.ingredients:
        raise HTTPException(status_code=400, detail="食材を1つ以上選択してください")

    # 季節を自動判定
    season = request.season
    if not season:
        month = datetime.now().month
        if month in (3, 4, 5):
            season = "春"
        elif month in (6, 7, 8):
            season = "夏"
        elif month in (9, 10, 11):
            season = "秋"
        else:
            season = "冬"

    # 食材をフリーテキストとして結合
    ingredients_text = "、".join(request.ingredients)

    results = {"ingredients_used": request.ingredients, "season": season, "recipes": []}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # AI生成レシピ（8001）
        if request.mode in ("generate", "both"):
            try:
                payload = {
                    "season": season,
                    "genre": request.genre or "",
                    "servings": request.servings,
                    "freetext": ingredients_text,
                }
                resp = await client.post(RECIPE_GENERATE_URL, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    recipes = data if isinstance(data, list) else data.get("recipes", [])
                    for r in recipes:
                        r["source"] = "ai_generate"
                    results["recipes"].extend(recipes)
                    logger.info(f"AI生成: {len(recipes)}件のレシピ取得")
            except Exception as e:
                logger.warning(f"AI生成APIエラー: {e}")
                results["generate_error"] = str(e)

        # Web検索レシピ（8002）
        if request.mode in ("search", "both"):
            try:
                payload = {
                    "season": season,
                    "category": request.genre or "",
                    "freetext": ingredients_text,
                }
                resp = await client.post(RECIPE_SEARCH_URL, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    recipes = data if isinstance(data, list) else data.get("recipes", [])
                    for r in recipes:
                        r["source"] = "web_search"
                    results["recipes"].extend(recipes)
                    logger.info(f"Web検索: {len(recipes)}件のレシピ取得")
            except Exception as e:
                logger.warning(f"Web検索APIエラー: {e}")
                results["search_error"] = str(e)

    results["total_recipes"] = len(results["recipes"])
    return results


# ============================================================
# 献立作成ヘルパー
# ============================================================

# --- 食材の在庫・消費テーブル（全てグラム統一） ---
# (キーワードリスト, 初期在庫g, 1食使用量g（2人前基準）)
# キーワードは先にマッチしたものが優先されるため、具体的なものを先に置く
_STOCK_TABLE: list[tuple[list[str], int, int]] = [
    # 肉類（1パック相当）
    (["鶏もも"], 300, 200),
    (["鶏むね"], 300, 200),
    (["豚バラ"], 300, 200),
    (["豚ロース"], 300, 200),
    (["ひき肉"], 300, 150),
    (["ハム"], 200, 80),
    (["ベーコン"], 150, 60),
    (["ソーセージ", "ウインナー"], 200, 100),
    (["肉", "牛"], 300, 200),
    # 魚介類
    (["鮭", "サバ", "さば", "アジ", "あじ"], 300, 150),
    (["ブリ", "ぶり", "タラ", "たら"], 300, 150),
    (["エビ", "えび"], 250, 120),
    (["イカ", "いか"], 250, 120),
    # 卵（6個パック ≈ 360g）
    (["卵", "たまご"], 360, 120),
    # 大型野菜（丸ごと1個）
    (["キャベツ"], 1000, 250),
    (["白菜"], 1500, 300),
    (["大根"], 800, 200),
    (["レタス"], 500, 150),
    (["かぼちゃ"], 500, 200),
    # 葉物・袋野菜
    (["ほうれん草", "小松菜", "水菜"], 200, 100),
    (["もやし"], 250, 125),
    (["ブロッコリー"], 300, 150),
    # 個数で買う野菜（グラム換算）
    (["じゃがいも"], 600, 200),     # 3個×200g
    (["玉ねぎ"], 600, 200),         # 3個×200g
    (["にんじん", "人参"], 450, 150),  # 3本×150g
    (["トマト"], 400, 150),          # 2個×200g
    (["なす"], 300, 100),            # 3本×100g
    (["ピーマン"], 200, 80),         # 5個×40g
    (["さつまいも"], 500, 200),
    # パック・丁もの
    (["豆腐"], 400, 200),            # 1丁≈400g
    (["こんにゃく"], 250, 125),
    (["しめじ", "えのき", "エリンギ", "まいたけ", "きのこ"], 200, 100),
]
_DEFAULT_STOCK_G = 300
_DEFAULT_USAGE_G = 150

# 残量がこの値未満になったら「使い切った」とみなす
_MIN_USABLE_G = 30

# そのまま1食の献立になる食材のキーワード（レシピ不要）
# 調理キット・フライ・冷凍食品・焼き魚など
_READY_MEAL_KW = [
    # 調理キット系（「セット」は食材詰め合わせにも使われるため除外）
    "キット", "ミールキット", "おかずセット", "惣菜セット",
    "丼の具", "カリー", "カレー",
    "マーボー", "麻婆", "エビマヨ", "エビチリ", "回鍋肉",
    "青椒肉絲", "酢豚", "グラタン", "ドリア", "パスタソース",
    "炒めるだけ", "煮るだけ", "レンジで", "チンして",
    # フライ・揚げ物
    "フライ", "コロッケ", "唐揚げ", "からあげ", "カツ",
    "天ぷら", "メンチ", "ナゲット", "竜田揚げ",
    # 冷凍食品
    "冷凍", "チャーハン", "ピラフ", "焼きおにぎり",
    "餃子", "ぎょうざ", "シュウマイ", "焼売",
    "ピザ", "ラザニア",
    # 焼き魚・干物（焼くだけ）
    "干物", "ひもの", "開き", "味噌漬", "粕漬", "西京漬",
    "塩鮭", "塩さば", "塩焼き",
    # 加工肉・レトルト
    "ハンバーグ", "ミートボール", "肉団子",
    "レトルト", "缶詰",
]


def _is_ready_meal(name: str) -> bool:
    """そのまま1食の献立になる食材かどうかを判定する"""
    for kw in _READY_MEAL_KW:
        if kw in name:
            return True
    return False


# 食材名に含まれる分量表記 → 倍率へのマッピング
# 例: "1/2切" → 0.5, "1/4切" → 0.25
_FRACTION_PATTERN = re.compile(r"(\d+)/(\d+)\s*切")
# 例: "半玉", "半分"
_HALF_KEYWORDS = ["半玉", "半分", "ハーフ"]


def _detect_quantity_ratio(name: str) -> float:
    """食材名から分量の倍率を検出する（例: 1/2切 → 0.5）"""
    # "1/2切", "1/4切" 等の分数表記
    m = _FRACTION_PATTERN.search(name)
    if m:
        numer, denom = int(m.group(1)), int(m.group(2))
        if denom != 0:
            return numer / denom
    # "半玉", "半分" 等
    for kw in _HALF_KEYWORDS:
        if kw in name:
            return 0.5
    return 1.0


def _lookup_stock_info(name: str) -> tuple[int, int]:
    """食材名から (初期在庫g, 1食使用量g) を返す（分量表記を考慮）"""
    ratio = _detect_quantity_ratio(name)
    for keywords, total_g, usage_g in _STOCK_TABLE:
        for kw in keywords:
            if kw in name:
                return (int(total_g * ratio), usage_g)
    return (int(_DEFAULT_STOCK_G * ratio), _DEFAULT_USAGE_G)


def _init_stock(ingredients: list[str]) -> dict[str, dict]:
    """食材リストから初期在庫を構築する（全てグラム）"""
    stock = {}
    for name in ingredients:
        ready = _is_ready_meal(name)
        total_g, usage_g = _lookup_stock_info(name)
        if ready:
            # ready_meal は1回で使い切る（1食分=全量）
            total_g = max(total_g, usage_g)
            usage_g = total_g
        stock[name] = {
            "remaining": total_g,
            "usage_per_meal": usage_g,
            "ready_meal": ready,
        }
    return stock


def _select_day_ingredients(stock: dict, max_items: int = 3) -> list[str]:
    """残量のある通常食材から今日使うものを選ぶ（残量が多い順）"""
    available = [
        (name, info) for name, info in stock.items()
        if info["remaining"] >= _MIN_USABLE_G and not info.get("ready_meal")
    ]
    if not available:
        return []
    available.sort(key=lambda x: x[1]["remaining"], reverse=True)
    return [name for name, _ in available[:max_items]]


def _pick_ready_meal(stock: dict) -> str | None:
    """未使用の ready_meal 食材を1つ選ぶ"""
    for name, info in stock.items():
        if info.get("ready_meal") and info["remaining"] >= _MIN_USABLE_G:
            return name
    return None


def _can_make_meal(stock: dict) -> bool:
    """まだ献立が作れるだけの食材が残っているか（通常食材 or ready_meal）"""
    return any(
        info["remaining"] >= _MIN_USABLE_G for info in stock.values()
    )


def _has_cookable_ingredients(stock: dict) -> bool:
    """通常食材（レシピが必要なもの）がまだ残っているか"""
    return any(
        info["remaining"] >= _MIN_USABLE_G and not info.get("ready_meal")
        for info in stock.values()
    )


def normalize_ai_recipe(data: dict) -> dict:
    """8001のレスポンスをrecipe形式に正規化する"""
    return {
        "name": data.get("name", data.get("title", "レシピ")),
        "time": data.get("time", data.get("cooking_time", "30分")),
        "difficulty": data.get("difficulty", "普通"),
        "calories": data.get("calories", ""),
        "ingredients": data.get("ingredients", []),
        "steps": data.get("steps", data.get("instructions", [])),
        "tips": data.get("tips", data.get("advice", [])),
    }


def normalize_web_recipe(data: dict) -> dict:
    """8002のレスポンスをweb_recipe形式に正規化する"""
    return {
        "name": data.get("name", data.get("title", "レシピ")),
        "url": data.get("url", data.get("link", None)),
        "source": data.get("source", data.get("site_name", "")),
        "description": data.get("description", data.get("summary", "")),
    }


async def _fetch_day_recipes(
    client: httpx.AsyncClient,
    day_ingredients: list[str],
    season: str,
    servings: int,
    simple_mode: bool = False,
    used_recipe_names: set[str] | None = None,
) -> dict:
    """
    1日分のAI生成+Web検索レシピを取得する（重複回避）
    1) まずAIレシピを取得
    2) AIレシピ名を使ってWeb検索（同じ料理の別レシピを探す）
    """
    ingredients_text = "、".join(day_ingredients)
    if simple_mode:
        ingredients_text += "（簡単・時短レシピ希望）"

    # --- Phase 1: AIレシピ取得 ---
    ai_result = None
    exclude_hint = ""
    if used_recipe_names:
        exclude_hint = "（次の料理以外で: " + "、".join(used_recipe_names) + "）"
    try:
        payload = {
            "season": season,
            "genre": "簡単" if simple_mode else "",
            "servings": servings,
            "freetext": ingredients_text + exclude_hint,
        }
        resp = await client.post(RECIPE_GENERATE_URL, json=payload)
        if resp.status_code == 200:
            data = resp.json()
            recipes = data if isinstance(data, list) else data.get("recipes", [])
            if recipes and used_recipe_names:
                for r in recipes:
                    name = r.get("name", r.get("title", ""))
                    if name not in used_recipe_names:
                        ai_result = normalize_ai_recipe(r)
                        break
                if ai_result is None:
                    ai_result = normalize_ai_recipe(recipes[0])
            elif recipes:
                ai_result = normalize_ai_recipe(recipes[0])
    except Exception as e:
        logger.warning(f"AI生成APIエラー（献立）: {e}")

    # --- Phase 2: Web検索（複数クエリで重複回避） ---
    web_result = None

    # 検索クエリの候補を複数用意（順番に試す）
    ai_name = ai_result.get("name", "") if ai_result else ""
    search_queries = []
    if ai_name:
        search_queries.append(ai_name + " レシピ")
        search_queries.append(ai_name + " " + ingredients_text)
    search_queries.append(ingredients_text + " レシピ")
    # 食材を個別に検索（食材が複数ある場合）
    for ing in day_ingredients:
        search_queries.append(ing + " レシピ 簡単" if simple_mode else ing + " レシピ")

    for query in search_queries:
        if web_result is not None:
            break
        try:
            payload = {
                "season": season,
                "category": "簡単" if simple_mode else "",
                "freetext": query,
            }
            resp = await client.post(RECIPE_SEARCH_URL, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                recipes = data if isinstance(data, list) else data.get("recipes", [])
                for r in recipes:
                    name = r.get("name", r.get("title", ""))
                    if not used_recipe_names or name not in used_recipe_names:
                        web_result = normalize_web_recipe(r)
                        break
        except Exception as e:
            logger.warning(f"Web検索APIエラー（献立）: {e}")

    return {"recipe": ai_result, "web_recipe": web_result}


@app.post("/api/coop/meal-plan")
async def create_meal_plan(
    request: MealPlanRequest,
    authorization: str = Header(default=""),
):
    """
    選択された食材から献立を作成する（食材がなくなるまで最大7日分）
    各日についてAI生成(8001)とWeb検索(8002)のレシピを取得し、
    食材の残量を追跡しながら日ごとに順次処理する
    """
    verify_token(authorization)

    if not request.ingredients:
        raise HTTPException(status_code=400, detail="食材を1つ以上選択してください")

    # 季節を自動判定
    season = request.season
    if not season:
        month = datetime.now().month
        if month in (3, 4, 5):
            season = "春"
        elif month in (6, 7, 8):
            season = "夏"
        elif month in (9, 10, 11):
            season = "秋"
        else:
            season = "冬"

    # 食材の在庫を初期化
    stock = _init_stock(request.ingredients)
    used_ever: set[str] = set()          # 一度でも使った食材
    used_recipe_names: set[str] = set()  # AI・Web両方の既出レシピ名
    plan = []
    scale = request.servings / 2.0

    # 登録済みメニューを読み込む（食材名との照合に使用）
    custom_meals = load_custom_meals()

    async with httpx.AsyncClient(timeout=60.0) as client:
        for day_num in range(1, 8):
            if not _can_make_meal(stock):
                break

            # --- ready_meal と通常食材を交互に配置 ---
            # 通常食材がある日はレシピ取得、合間にready_mealを挟む
            ready_meal_name = None
            day_ingredients = []

            if _has_cookable_ingredients(stock):
                # 通常食材でレシピを作る日
                day_ingredients = _select_day_ingredients(stock, max_items=3)
                if not day_ingredients:
                    break
            else:
                # 通常食材が尽きた → ready_meal のみ
                ready_meal_name = _pick_ready_meal(stock)
                if not ready_meal_name:
                    break

            # 偶数日かつready_mealが残っていれば、ready_mealの日にする
            if (day_num % 2 == 0
                    and not ready_meal_name
                    and _pick_ready_meal(stock)):
                ready_meal_name = _pick_ready_meal(stock)
                day_ingredients = []

            if ready_meal_name:
                # --- ready_meal の日（レシピ不要） ---
                used_ever.add(ready_meal_name)
                info = stock[ready_meal_name]
                usage = info["remaining"]  # 全量使い切り
                info["remaining"] = 0

                # 残り食材一覧
                remaining = []
                for name, inf in stock.items():
                    if inf["remaining"] >= _MIN_USABLE_G:
                        remaining.append(f"{name} {inf['remaining']}g")

                plan.append({
                    "day": day_num,
                    "label": f"{day_num}日目",
                    "recipe": {
                        "name": ready_meal_name,
                        "time": "10分以内",
                        "difficulty": "簡単",
                        "calories": "",
                        "ingredients": [ready_meal_name],
                        "steps": ["パッケージの表示に従って調理してください"],
                        "tips": ["調理キット・冷凍食品等のためレシピ不要です"],
                    },
                    "web_recipe": None,
                    "used_ingredients": [f"{ready_meal_name} {usage}g"],
                    "remaining_ingredients": remaining,
                })
            else:
                # --- 通常食材の日 ---
                # 食材名と登録済みメニューを照合（完全一致または部分一致）
                matched_meal = None
                for ing in day_ingredients:
                    if ing in custom_meals:
                        matched_meal = custom_meals[ing]
                        break
                    # 部分一致（例: "焼き魚（サバ）" → "焼き魚" に一致）
                    for meal_name, meal in custom_meals.items():
                        if meal_name in ing or ing in meal_name:
                            matched_meal = meal
                            break
                    if matched_meal:
                        break

                if matched_meal:
                    # 登録済みメニューを使用（レシピAPIは呼ばない）
                    custom_recipe = matched_meal.get("recipe")
                    if custom_recipe:
                        recipe_obj = {
                            "name": matched_meal["name"],
                            "time": custom_recipe.get("time", ""),
                            "difficulty": custom_recipe.get("difficulty", ""),
                            "calories": custom_recipe.get("calories", ""),
                            "ingredients": custom_recipe.get("ingredients", []),
                            "steps": custom_recipe.get("steps", []),
                            "tips": custom_recipe.get("tips", []),
                        }
                    else:
                        recipe_obj = {
                            "name": matched_meal["name"],
                            "time": "", "difficulty": "", "calories": "",
                            "ingredients": [], "steps": [], "tips": [],
                        }
                    result = {"recipe": recipe_obj, "web_recipe": None}
                    used_recipe_names.add(matched_meal["name"])
                else:
                    # 登録なし → APIに問い合わせ
                    result = await _fetch_day_recipes(
                        client, day_ingredients, season,
                        request.servings, request.simple_mode, used_recipe_names,
                    )

                if isinstance(result, Exception):
                    logger.error(f"Day {day_num} レシピ取得エラー: {result}")
                    result = {"recipe": None, "web_recipe": None}

                # レシピ名を記録（重複回避用）
                ai = result.get("recipe")
                if ai and ai.get("name"):
                    used_recipe_names.add(ai["name"])
                web = result.get("web_recipe")
                if web and web.get("name"):
                    used_recipe_names.add(web["name"])

                # 食材の消費計算
                day_used = []
                for name in day_ingredients:
                    used_ever.add(name)
                    info = stock[name]
                    usage = min(
                        int(info["usage_per_meal"] * scale),
                        info["remaining"],
                    )
                    info["remaining"] = max(0, info["remaining"] - usage)
                    day_used.append(f"{name} {usage}g")

                # 残り食材一覧
                remaining = []
                for name, inf in stock.items():
                    if inf["remaining"] >= _MIN_USABLE_G:
                        remaining.append(f"{name} {inf['remaining']}g")

                plan.append({
                    "day": day_num,
                    "label": f"{day_num}日目",
                    "recipe": result.get("recipe"),
                    "web_recipe": result.get("web_recipe"),
                    "used_ingredients": day_used,
                    "remaining_ingredients": remaining,
                })

    # 一度も使われなかった食材
    unused = [name for name in request.ingredients if name not in used_ever]

    return {
        "plan": plan,
        "unused_ingredients": unused,
    }


@app.post("/api/coop/meals")
def register_meal(
    request: RegisterMealRequest,
    authorization: str = Header(default=""),
):
    """
    メニューを登録する。レシピは省略可（後から追加・削除できる）。
    同名のメニューが既に存在する場合は上書き。
    """
    verify_token(authorization)

    if not request.name.strip():
        raise HTTPException(status_code=400, detail="メニュー名を入力してください")

    meals = load_custom_meals()
    meals[request.name] = {
        "name": request.name,
        "recipe": request.recipe.model_dump() if request.recipe else None,
        "registered_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_custom_meals(meals)

    return {
        "status": "success",
        "message": f"'{request.name}' を登録しました",
        "meal": meals[request.name],
    }


@app.get("/api/coop/meals")
def get_meals(authorization: str = Header(default="")):
    """登録済みメニューの一覧を返す"""
    verify_token(authorization)

    meals = load_custom_meals()
    return {
        "count": len(meals),
        "meals": list(meals.values()),
    }


@app.put("/api/coop/meals/{name}/recipe")
def update_meal_recipe(
    name: str,
    recipe: CustomRecipe,
    authorization: str = Header(default=""),
):
    """登録済みメニューにレシピを追加・更新する"""
    verify_token(authorization)

    meals = load_custom_meals()
    if name not in meals:
        raise HTTPException(status_code=404, detail=f"メニュー '{name}' が見つかりません")

    meals[name]["recipe"] = recipe.model_dump()
    save_custom_meals(meals)

    return {
        "status": "success",
        "message": f"'{name}' のレシピを更新しました",
        "meal": meals[name],
    }


@app.delete("/api/coop/meals/{name}/recipe")
def delete_meal_recipe(
    name: str,
    authorization: str = Header(default=""),
):
    """登録済みメニューのレシピだけを削除する（メニュー名は残す）"""
    verify_token(authorization)

    meals = load_custom_meals()
    if name not in meals:
        raise HTTPException(status_code=404, detail=f"メニュー '{name}' が見つかりません")

    meals[name]["recipe"] = None
    save_custom_meals(meals)

    return {
        "status": "success",
        "message": f"'{name}' のレシピを削除しました（メニューは残っています）",
    }


@app.delete("/api/coop/meals/{name}")
def delete_meal(
    name: str,
    authorization: str = Header(default=""),
):
    """登録済みメニューをレシピごと削除する"""
    verify_token(authorization)

    meals = load_custom_meals()
    if name not in meals:
        raise HTTPException(status_code=404, detail=f"メニュー '{name}' が見つかりません")

    del meals[name]
    save_custom_meals(meals)

    return {
        "status": "success",
        "message": f"'{name}' を削除しました",
    }


@app.put("/api/coop/classify")
def update_classification(
    request: ClassifyRequest,
    authorization: str = Header(default=""),
):
    """
    商品カテゴリを手動修正する（学習データとして保存）
    """
    verify_token(authorization)

    valid_categories = {"食材", "調理キット", "そのまま", "離乳食", "調味料・日用品"}
    if request.category not in valid_categories:
        raise HTTPException(
            status_code=400,
            detail=f"無効なカテゴリです。有効な値: {valid_categories}",
        )

    overrides = load_category_overrides()
    overrides[request.original_name] = request.category
    save_category_overrides(overrides)

    return {
        "status": "success",
        "message": f"'{request.original_name}' を '{request.category}' に変更しました",
        "total_overrides": len(overrides),
    }


# ============================================================
# サーバー起動
# ============================================================

if __name__ == "__main__":
    import uvicorn
    logger.info(f"COOP連携APIサーバー起動: {API_HOST}:{API_PORT}")
    uvicorn.run(app, host=API_HOST, port=API_PORT)
