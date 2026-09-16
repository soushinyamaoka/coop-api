import pytest

from coop_parser import (
    SAMPLE_EMAIL,
    _is_ingredient_set,
    classify_item,
    normalize_ingredient_name,
    parse_coop_email,
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
        ("カレーパン", "調理キット"),
        ("鶏もも肉", "食材"),
        ("台所用洗剤", "調味料・日用品"),
    ],
)
def test_classify_item_priority_and_fallback(name: str, expected: str) -> None:
    assert classify_item(name) == expected


# These cases lock in current substring-based behavior; they are not necessarily
# the intended product classification and should only be changed after impact review.
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("水菜", "そのまま"),
        ("カレールー", "調理キット"),
        ("牛肉のおかずセット", "食材"),
    ],
)
def test_classify_item_preserves_questionable_current_behavior(
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
