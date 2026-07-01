"""
COOPデリ注文確認メールのパーサー
- 全角→半角変換
- 数量0点の除外
- 食材名の正規化（重量・括弧除去）
- カテゴリ自動分類（食材/調理キット/そのまま/離乳食）
"""

import re
import json
from datetime import datetime


# ============================================================
# 全角→半角変換
# ============================================================
ZEN_DIGITS = "０１２３４５６７８９"
HAN_DIGITS = "0123456789"
ZEN_ALPHA_UPPER = "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
HAN_ALPHA_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ZEN_ALPHA_LOWER = "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
HAN_ALPHA_LOWER = "abcdefghijklmnopqrstuvwxyz"

ZENKAKU_TO_HANKAKU = str.maketrans(
    ZEN_DIGITS + ZEN_ALPHA_UPPER + ZEN_ALPHA_LOWER,
    HAN_DIGITS + HAN_ALPHA_UPPER + HAN_ALPHA_LOWER,
)


def zen_to_han(text: str) -> str:
    """全角数字・英字を半角に変換"""
    return text.translate(ZENKAKU_TO_HANKAKU)


# ============================================================
# カテゴリ分類用キーワード辞書
# ============================================================

# 調理キット判定キーワード
# ※ 汎用的な「セット」は食材詰め合わせにも使われるため含めない
#    ミールキット系は「キット」「おかずセット」「惣菜セット」等の具体的な語で判定する
KIT_KEYWORDS = [
    "キット", "ミールキット", "おかずセット", "惣菜セット",
    "丼の具", "カリー", "カレー",
    "マーボー", "麻婆", "エビマヨ", "エビチリ", "回鍋肉",
    "青椒肉絲", "酢豚", "グラタン", "ドリア", "パスタソース",
    "炒めるだけ", "煮るだけ", "レンジで", "チンして",
]

# 食材セットと判定するキーワード
# 「○○セット」の○○にこれらが含まれていれば食材扱い
_INGREDIENT_SET_KW = [
    # 野菜
    "ピーマン", "なす", "にんじん", "人参", "キャベツ", "玉ねぎ",
    "トマト", "じゃがいも", "大根", "白菜", "ほうれん草", "小松菜",
    "ブロッコリー", "レタス", "きゅうり", "ごぼう", "れんこん",
    "もやし", "水菜", "かぼちゃ", "さつまいも", "長ねぎ", "ネギ",
    # 肉・魚
    "鶏", "豚", "牛", "肉", "魚", "鮭", "サバ", "エビ",
    # 総称
    "野菜", "根菜", "葉物", "旬", "産直",
]

# そのまま食べるもの判定キーワード
READY_TO_EAT_KEYWORDS = [
    "バナナ", "りんご", "みかん", "オレンジ", "いちご", "ぶどう",
    "キウイ", "グレープフルーツ", "梨", "柿", "桃", "メロン",
    "ヨーグルト", "牛乳", "豆乳", "チーズ", "プリン", "ゼリー",
    "パン", "食パン", "ロールパン", "クロワッサン",
    "ハム", "ベーコン", "ウインナー", "ソーセージ",
    "納豆", "漬物", "キムチ", "梅干し",
    "ジュース", "お茶", "コーヒー", "紅茶", "水",
    "アイス", "菓子", "クッキー", "チョコ", "せんべい", "おかき",
]

# 離乳食・幼児食判定キーワード
BABY_FOOD_KEYWORDS = [
    "離乳", "ベビー", "うらごし", "ダノン", "ベビーダノン",
    "幼児", "キッズ", "こども", "子ども", "子供",
    "BF", "ベビーフード",
]

# 調味料・日用品（レシピ食材から除外）
SEASONING_KEYWORDS = [
    "醤油", "しょうゆ", "味噌", "みそ", "塩", "砂糖",
    "みりん", "酢", "ケチャップ", "マヨネーズ", "ソース",
    "ドレッシング", "ポン酢", "めんつゆ", "だし",
    "油", "オリーブオイル", "ごま油",
    "洗剤", "シャンプー", "ティッシュ", "トイレ", "ラップ",
    "ゴミ袋", "歯ブラシ", "歯磨き",
]


def _is_ingredient_set(name: str) -> bool:
    """「セット」を含む商品名が食材の詰め合わせかどうかを判定する"""
    for kw in _INGREDIENT_SET_KW:
        if kw in name:
            return True
    return False


def classify_item(name: str) -> str:
    """
    商品名からカテゴリを推定する
    Returns: "食材" | "調理キット" | "そのまま" | "離乳食" | "調味料" | "日用品"
    """
    # 離乳食（最優先で判定）
    for kw in BABY_FOOD_KEYWORDS:
        if kw in name:
            return "離乳食"

    # 「セット」を含む場合、食材名が含まれていれば食材扱い
    if "セット" in name and _is_ingredient_set(name):
        return "食材"

    # 調理キット
    for kw in KIT_KEYWORDS:
        if kw in name:
            return "調理キット"

    # そのまま食べるもの
    for kw in READY_TO_EAT_KEYWORDS:
        if kw in name:
            return "そのまま"

    # 調味料・日用品
    for kw in SEASONING_KEYWORDS:
        if kw in name:
            return "調味料・日用品"

    # それ以外は食材
    return "食材"


# ============================================================
# 食材名の正規化
# ============================================================

def normalize_ingredient_name(raw_name: str) -> str:
    """
    商品名から食材の核となる名前を抽出する
    例: "牛バラ肉の牛丼用（たれ付）250g（たれ45g含む）" → "牛バラ肉"
    例: "九州のささがきごぼう400g" → "ささがきごぼう"
    例: "国産ブロッコリー1個" → "ブロッコリー"
    """
    name = zen_to_han(raw_name)

    # 「変更：毎週）」等のプレフィックスを除去
    name = re.sub(r'^変更：.*?）', '', name)
    name = re.sub(r'^毎週）', '', name)

    # 「の○○用」パターンを除去（例: 牛バラ肉の牛丼用 → 牛バラ肉）
    name = re.sub(r'の.{1,5}用$', '', name)
    name = re.sub(r'の.{1,5}用', '', name)

    # 括弧とその中身を除去（丸括弧・全角丸括弧）
    name = re.sub(r'[（(][^）)]*[）)]', '', name)

    # 重量・個数表記を除去
    name = re.sub(r'\d+\.?\d*\s*(g|kg|ml|L|個|本|枚|切|袋|パック|束|玉|尾|匹|丁|株)', '', name, flags=re.IGNORECASE)

    # 「×」「x」を含む数量表記を除去（例: 200g×2）
    name = re.sub(r'[×x]\s*\d+', '', name, flags=re.IGNORECASE)

    # 「1/2切」のような分数表記を除去
    name = re.sub(r'\d+／\d+', '', name)
    name = re.sub(r'\d+/\d+', '', name)

    # 残った数字を除去
    name = re.sub(r'\d+', '', name)

    # 産地プレフィックスを除去
    name = re.sub(r'^(国産|産直の|九州の|北海道の|十勝の)', '', name)

    # 「の」で始まる場合除去
    name = re.sub(r'^の', '', name)

    # 「人前」「人分」を除去
    name = re.sub(r'\d*人前', '', name)
    name = re.sub(r'\d*人分', '', name)

    # 末尾の助詞・記号・残留文字を除去
    name = name.rstrip('のをはがで／/〜～　 ')

    # 前後の空白除去
    name = name.strip()

    # 空になった場合は元の商品名を返す
    if not name:
        return raw_name.strip()

    return name


# ============================================================
# メール本文パース
# ============================================================

def parse_coop_email(body: str) -> dict:
    """
    COOPデリの注文確認メール本文をパースして構造化データを返す
    
    Returns:
        {
            "parsed_at": "2026-03-07T12:00:00",
            "total_items": 10,
            "ingredients": [...],      # 食材（レシピに使える）
            "kits": [...],             # 調理キット
            "ready_to_eat": [...],     # そのまま食べるもの
            "baby_food": [...],        # 離乳食・幼児食
            "seasonings": [...],       # 調味料・日用品
            "excluded": [...]          # 数量0点で除外されたもの
        }
    """
    # 全角→半角変換
    converted = zen_to_han(body)

    # 商品ブロックを正規表現で抽出
    pattern = r'注文番号：(\d+)\s*\n\s*商品名：(.+?)\s*\n\s*数量：(\d+)点'
    matches = re.finditer(pattern, converted)

    ingredients = []
    kits = []
    ready_to_eat = []
    baby_food = []
    seasonings = []
    excluded = []

    for match in matches:
        order_no, raw_name, qty_str = match.groups()
        qty = int(qty_str)

        # 数量0点は未注文 → 除外リストに入れる
        if qty == 0:
            excluded.append({
                "order_no": order_no,
                "name": raw_name.strip(),
                "reason": "数量0点（未注文）",
            })
            continue

        # カテゴリ分類
        category = classify_item(raw_name)

        # 食材名の正規化
        normalized = normalize_ingredient_name(raw_name)

        item = {
            "order_no": order_no,
            "name": normalized,
            "original_name": raw_name.strip(),
            "quantity": qty,
            "category": category,
        }

        # カテゴリ別に振り分け
        if category == "食材":
            ingredients.append(item)
        elif category == "調理キット":
            kits.append(item)
        elif category == "そのまま":
            ready_to_eat.append(item)
        elif category == "離乳食":
            baby_food.append(item)
        elif category == "調味料・日用品":
            seasonings.append(item)

    total = len(ingredients) + len(kits) + len(ready_to_eat) + len(baby_food) + len(seasonings)

    return {
        "parsed_at": datetime.now().isoformat(timespec="seconds"),
        "total_items": total,
        "excluded_count": len(excluded),
        "ingredients": ingredients,
        "kits": kits,
        "ready_to_eat": ready_to_eat,
        "baby_food": baby_food,
        "seasonings": seasonings,
        "excluded": excluded,
    }


# ============================================================
# テスト用：サンプルメールでの動作確認
# ============================================================

SAMPLE_EMAIL = """
【◆◆通常注文◆◆】
注文番号：000074
商品名：牛バラ肉の牛丼用（たれ付）２５０ｇ（たれ４５ｇ含む）
数量：1点
注文番号：000198
商品名：九州のささがきごぼう４００ｇ
数量：1点
注文番号：000352
商品名：ミニトマト１３０ｇ（サイズ込）
数量：1点
注文番号：000363
商品名：国産ブロッコリー１個
数量：1点
注文番号：000364
商品名：キャベツ（カット）１／２切
数量：1点
注文番号：000501
商品名：バナナ（フィリピン産）４〜５本
数量：1点
注文番号：000550
商品名：森永ビヒダスヨーグルト４００ｇ
数量：1点

【◆◆自動注文◆◆】
注文番号：002214
商品名：お米育ち豚小間切２００ｇ
数量：1点
注文番号：009025
商品名：九州のうらごしほうれん草１２０ｇ（８個入）
数量：1点
注文番号：002075
商品名：皮付きポテト　十勝めむろマチルダ種使用５００ｇ
数量：0点

【◆◆ほぺたん忘れず注文◆◆】
注文番号：282057
商品名：変更：毎週）きぬ豆腐ダブルパック２００ｇ×２
数量：1点
注文番号：283231
商品名：変更：毎週）産直のはぐくむたまご６個（ＭＳ～ＬＬ込）
数量：1点
注文番号：283500
商品名：クンパッポンカリーキット２人前
数量：1点
"""


def main():
    """サンプルメールでパーサーのテスト"""
    result = parse_coop_email(SAMPLE_EMAIL)

    print("=" * 60)
    print("COOP注文メール パース結果")
    print("=" * 60)
    print(f"パース日時: {result['parsed_at']}")
    print(f"注文商品数: {result['total_items']}件")
    print(f"除外商品数: {result['excluded_count']}件")
    print()

    print("🥩 食材（レシピに使える）:")
    for item in result["ingredients"]:
        print(f"  ✅ {item['name']}（元: {item['original_name']}）× {item['quantity']}")
    print()

    print("🍱 調理キット:")
    for item in result["kits"]:
        print(f"  📦 {item['name']}（元: {item['original_name']}）× {item['quantity']}")
    print()

    print("🍌 そのまま食べるもの:")
    for item in result["ready_to_eat"]:
        print(f"  🍽️ {item['name']}（元: {item['original_name']}）× {item['quantity']}")
    print()

    print("👶 離乳食・幼児食:")
    for item in result["baby_food"]:
        print(f"  🍼 {item['name']}（元: {item['original_name']}）× {item['quantity']}")
    print()

    print("🧂 調味料・日用品:")
    for item in result["seasonings"]:
        print(f"  🫙 {item['name']}（元: {item['original_name']}）× {item['quantity']}")
    print()

    print("❌ 除外された商品:")
    for item in result["excluded"]:
        print(f"  ⛔ {item['name']}（{item['reason']}）")
    print()

    # JSON出力も確認
    print("=" * 60)
    print("JSON出力:")
    print("=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
