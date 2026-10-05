from datetime import date

import pytest

from coop_parser import (
    SAMPLE_EMAIL,
    _is_ingredient_set,
    classify_item,
    extract_delivery_schedule_date,
    extract_order_amounts,
    normalize_ingredient_name,
    parse_coop_email,
    source_fingerprint,
    extract_order_rows,
    zen_to_han,
)


def test_zen_to_han_converts_full_width_alphanumerics() -> None:
    assert zen_to_han("Ａｚ０９") == "Az09"


def test_zen_to_han_preserves_characters_outside_the_translation_table() -> None:
    assert zen_to_han("かな！／") == "かな！／"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("旬の野菜セット", True),
        ("牛肉セット", True),
        ("おかずセット", False),
        ("スイーツセット", False),
    ],
)
def test_is_ingredient_set(name: str, expected: bool) -> None:
    assert _is_ingredient_set(name) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ベビーダノン ヨーグルト", "離乳食"),
        ("野菜セット", "食材"),
        ("おかずセット", "調理キット"),
        ("カレーパン", "そのまま"),
        ("鶏もも肉", "食材"),
        ("台所用洗剤", "調味料・日用品"),
    ],
)
def test_classify_item_priority_and_fallback(name: str, expected: str) -> None:
    assert classify_item(name) == expected


# These cases verify the intended classifications after resolving substring conflicts.
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("水菜", "食材"),
        ("カレールー", "食材"),
        ("牛肉のおかずセット", "調理キット"),
    ],
)
def test_classify_item_handles_intended_substring_behavior(
    name: str, expected: str
) -> None:
    assert classify_item(name) == expected


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("変更：毎週）きぬ豆腐２００ｇ×２", "きぬ豆腐"),
        ("毎週）たまご６個", "たまご"),
        ("牛バラ肉の牛丼用（たれ付）２５０ｇ", "牛バラ肉"),
        ("キャベツ（カット）１／２切", "キャベツ"),
        ("豚肉２００ｇx２", "豚肉"),
        ("商品１２３", "商品"),
        ("国産ブロッコリー１個", "ブロッコリー"),
        ("産直のたまご６個", "たまご"),
        ("九州のささがきごぼう４００ｇ", "ささがきごぼう"),
        ("北海道の玉ねぎ３個", "玉ねぎ"),
        ("十勝のじゃがいも１ｋｇ", "じゃがいも"),
        ("のりんご２個", "りんご"),
        ("ミールキット２人前", "ミールキット"),
        ("スープ３人分", "スープ"),
        ("たまねぎの／ ", "たまねぎ"),
        ("１００ｇ", "１００ｇ"),
    ],
)
def test_normalize_ingredient_name(raw_name: str, expected: str) -> None:
    assert normalize_ingredient_name(raw_name) == expected


def test_parse_coop_email_extracts_sample_orders() -> None:
    result = parse_coop_email(SAMPLE_EMAIL)

    assert result["total_items"] == 12
    assert result["excluded_count"] == 1

    items = {
        item["order_no"]: item
        for key in ("ingredients", "kits", "ready_to_eat", "baby_food", "seasonings")
        for item in result[key]
    }
    assert set(items) == {
        "000074",
        "000198",
        "000352",
        "000363",
        "000364",
        "000501",
        "000550",
        "002214",
        "009025",
        "282057",
        "283231",
        "283500",
    }
    assert all(item["quantity"] == 1 for item in items.values())
    assert [len(result[key]) for key in (
        "ingredients",
        "kits",
        "ready_to_eat",
        "baby_food",
        "seasonings",
    )] == [8, 1, 2, 1, 0]
    assert items["000074"] == {
        "order_no": "000074",
        "name": "牛バラ肉",
        "original_name": "牛バラ肉の牛丼用（たれ付）250g（たれ45g含む）",
        "quantity": 1,
        "category": "食材",
        "classifier": "keyword",
    }
    assert items["283500"]["name"] == "クンパッポンカリーキット"
    assert items["283500"]["quantity"] == 1
    assert items["283500"]["category"] == "調理キット"
    assert result["excluded"] == [
        {
            "order_no": "002075",
            "name": "皮付きポテト　十勝めむろマチルダ種使用500g",
            "reason": "数量0点（未注文）",
        }
    ]
    assert result["source_fingerprint"] == source_fingerprint(SAMPLE_EMAIL)


def test_fingerprint_ignores_row_order_and_surrounding_name_whitespace() -> None:
    first = "注文番号：1\n商品名：  しょうゆ  \n数量：1点\n注文番号：2\n商品名：架空野菜\n数量：0点"
    second = "Subject: another date\n注文番号：2\n商品名：架空野菜\n数量：0点\n注文番号：1\n商品名：しょうゆ\n数量：1点"
    assert source_fingerprint(first) == source_fingerprint(second)
    assert len(extract_order_rows(first)) == 2


@pytest.mark.parametrize("changed", [
    "注文番号：1\n商品名：しょうゆ\n数量：2点",
    "注文番号：1\n商品名：みそ\n数量：1点",
    "注文番号：1\n商品名：しょうゆ\n数量：0点",
])
def test_fingerprint_changes_with_order_content(changed: str) -> None:
    original = "注文番号：1\n商品名：しょうゆ\n数量：1点"
    assert source_fingerprint(original) != source_fingerprint(changed)


def test_delivery_date_and_amounts_from_synthetic_header():
    body = """ご注文が完了しております。
９月５回Ｄ週 のご注文内容
注文締切日時：9月25日(金) 2:00
翌週商品配達予定日：９月３０日（水）
商品合計数：2点
合計金額（本体）：5000円
合計金額（税込）：5,400円
注文番号：123
商品名：合計金額を含む架空商品
数量：1点"""
    assert extract_delivery_schedule_date(body, date(2026, 9, 30)) == date(2026, 9, 30)
    assert extract_order_amounts(body) == (5000, 5400)
    assert extract_delivery_schedule_date("翌週商品配達予定日：9月30日", date(2026, 9, 30)) == date(2026, 9, 30)


@pytest.mark.parametrize(("received", "md", "expected"), [
    (date(2026, 12, 28), "1月4日", date(2027, 1, 4)),
    (date(2027, 1, 2), "12月30日", date(2026, 12, 30)),
    (date(2026, 10, 1), "9月30日", date(2026, 9, 30)),
])
def test_delivery_date_selects_nearest_year(received, md, expected):
    assert extract_delivery_schedule_date(f"翌週商品配達予定日：{md}", received) == expected


def test_invalid_delivery_date_and_missing_amounts_are_unreadable():
    assert extract_delivery_schedule_date("翌週商品配達予定日：2月30日", date(2026, 2, 1)) is None
    assert extract_order_amounts("注文番号：1\n商品名：合計金額 123円\n数量：1点") == (None, None)
