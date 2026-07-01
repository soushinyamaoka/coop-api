# COOP連携API 仕様書

**ベースURL**: `http://<VPS_IP>:8003`
**認証**: 全エンドポイント（`/` を除く）に `Authorization: Bearer <API_TOKEN>` が必要

---

## GET /

認証不要。サービス情報を返す。

**レスポンス例**
```json
{
  "service": "COOP連携API",
  "version": "1.0.0",
  "endpoints": [...]
}
```

---

## GET /api/coop/ingredients

最新注文の食材リストをカテゴリ分類済みで返す。ユーザーによるカテゴリ修正（学習データ）も反映済み。

**レスポンス**
```json
{
  "order_date": "2026-03-06",
  "parsed_at": "2026-03-06T20:00:00",
  "ingredients": [
    {
      "order_no": "000074",
      "name": "牛バラ肉",
      "original_name": "牛バラ肉の牛丼用（たれ付）250g",
      "quantity": 1,
      "category": "食材",
      "category_source": "auto"
    }
  ],
  "kits": [...],
  "ready_to_eat": [...],
  "baby_food": [...],
  "seasonings": [...],
  "excluded": [
    { "name": "皮付きポテト...", "reason": "数量0点（未注文）" }
  ]
}
```

**カテゴリの種類**

| フィールド | 日本語カテゴリ | 内容 |
|-----------|-------------|------|
| `ingredients` | 食材 | レシピに使える食材 |
| `kits` | 調理キット | ミールキット（タレ・調味料込み） |
| `ready_to_eat` | そのまま | フルーツ・ヨーグルト等 |
| `baby_food` | 離乳食 | 離乳食・幼児食 |
| `seasonings` | 調味料・日用品 | 調味料・洗剤等 |

**`category_source` の値**: `"auto"` （自動分類）または `"user_override"` （ユーザー修正済み）

---

## GET /api/coop/orders

全注文の一覧サマリーを返す（詳細は `/ingredients` で取得）。

**レスポンス**
```json
{
  "last_updated": "2026-03-06T20:00:00",
  "order_count": 3,
  "orders": [
    {
      "order_date": "2026-03-06",
      "email_subject": "eフレンズ注文済メモメール",
      "total_items": 12,
      "ingredient_count": 8,
      "kit_count": 1
    }
  ]
}
```

---

## POST /api/coop/fetch

Gmail IMAPからCOOP注文確認メールを取得してJSONに保存する。通常はcronで自動実行（毎日7時・20時）。

**クエリパラメータ**

| パラメータ | 型 | デフォルト | 説明 |
|----------|---|---------|------|
| `days_back` | int | 14 | 遡る日数（1〜90） |

**レスポンス（成功）**
```json
{
  "status": "success",
  "message": "1件の注文を取得しました",
  "orders": 1
}
```

**レスポンス（メールなし）**
```json
{
  "status": "no_data",
  "message": "COOPからの注文確認メールが見つかりませんでした"
}
```

---

## POST /api/coop/suggest-recipes

選択した食材からレシピを提案する。内部でAI生成API（8001）・Web検索API（8002）に転送。

**リクエスト**
```json
{
  "ingredients": ["鶏もも肉", "キャベツ"],
  "season": "春",
  "genre": "和食",
  "servings": 2,
  "mode": "both"
}
```

| フィールド | 型 | デフォルト | 説明 |
|----------|---|---------|------|
| `ingredients` | string[] | 必須 | 食材名の配列（1個以上） |
| `season` | string | `""` | 春/夏/秋/冬。空なら現在月から自動判定 |
| `genre` | string | `""` | 和食/洋食/中華 等 |
| `servings` | int | `2` | 人数 |
| `mode` | string | `"both"` | `"generate"`（8001のみ）/ `"search"`（8002のみ）/ `"both"` |

**レスポンス**
```json
{
  "ingredients_used": ["鶏もも肉", "キャベツ"],
  "season": "春",
  "recipes": [
    { "name": "...", "source": "ai_generate", ... },
    { "name": "...", "source": "web_search", ... }
  ],
  "total_recipes": 2
}
```

---

## POST /api/coop/meal-plan

選択した食材から最大7日分の献立を作成する。食材を消費管理しながら日ごとにレシピを取得し、食材が尽きた時点で終了する。

**リクエスト**
```json
{
  "ingredients": ["鶏もも肉", "キャベツ", "餃子", "塩鮭"],
  "season": "春",
  "servings": 2,
  "simple_mode": false
}
```

| フィールド | 型 | デフォルト | 説明 |
|----------|---|---------|------|
| `ingredients` | string[] | 必須 | 食材名の配列（1個以上） |
| `season` | string | `""` | 空なら自動判定 |
| `servings` | int | `2` | 人数 |
| `simple_mode` | bool | `false` | `true` で簡単・時短レシピを優先 |

**レスポンス**
```json
{
  "plan": [
    {
      "day": 1,
      "label": "1日目",
      "recipe": {
        "name": "鶏もも肉の照り焼き",
        "time": "30分",
        "difficulty": "普通",
        "calories": "450kcal",
        "ingredients": ["鶏もも肉 200g", "..."],
        "steps": ["..."],
        "tips": ["..."]
      },
      "web_recipe": {
        "name": "基本の照り焼きチキン",
        "url": "https://...",
        "source": "クラシル",
        "description": "..."
      },
      "used_ingredients": ["鶏もも肉 200g", "キャベツ 250g"],
      "remaining_ingredients": ["キャベツ 750g", "餃子 300g", "塩鮭 300g"]
    },
    {
      "day": 2,
      "label": "2日目",
      "recipe": {
        "name": "餃子",
        "time": "10分以内",
        "difficulty": "簡単",
        "calories": "",
        "ingredients": ["餃子"],
        "steps": ["パッケージの表示に従って調理してください"],
        "tips": ["調理キット・冷凍食品等のためレシピ不要です"]
      },
      "web_recipe": null,
      "used_ingredients": ["餃子 300g"],
      "remaining_ingredients": ["鶏もも肉 100g", "キャベツ 750g", "塩鮭 300g"]
    }
  ],
  "unused_ingredients": []
}
```

**献立作成の仕様**

- 日数は1〜7日（食材が尽きたら終了）
- 偶数日にready_meal（調理キット・冷凍食品・干物等）を自動配置
- ready_mealの日は `web_recipe: null`、stepsは固定文言
- `"1/2切"` `"半玉"` 等の分量表記を自動検出し初期在庫に反映
- レシピの重複を回避（AI・Web両方）

**食材の初期在庫の目安**

| 種類 | 代表例 | 初期在庫 | 1食使用量 |
|------|--------|---------|---------|
| 大型野菜 | キャベツ | 1000g | 250g（約4日分） |
| 根菜 | じゃがいも | 600g | 200g（約3日分） |
| 肉類 | 鶏もも肉 | 300g | 200g（約1〜2日分） |
| 卵 | 卵 | 360g | 120g（約3日分） |
| 不明食材 | （上記以外） | 300g | 150g（約2日分） |

---

## POST /api/coop/meals

メニューを登録する。レシピは省略可能で、後から追加・削除できる。同名のメニューが既に存在する場合は上書き。

献立作成（`/api/coop/meal-plan`）時に食材名と一致するメニューが登録されていれば、レシピAPIを呼ばずに登録内容を使用する。

**リクエスト**
```json
{ "name": "焼き魚" }
```
```json
{
  "name": "肉じゃが",
  "recipe": {
    "time": "40分",
    "difficulty": "普通",
    "calories": "400kcal",
    "ingredients": ["鶏もも肉 200g", "じゃがいも 2個"],
    "steps": ["材料を切る", "炒めてから煮込む"],
    "tips": ["じゃがいもは大きめに切ると煮崩れしにくい"]
  }
}
```

| フィールド | 型 | 必須 | 説明 |
|----------|---|-----|------|
| `name` | string | 必須 | メニュー名 |
| `recipe` | object | 省略可 | レシピ内容（省略時は `null`） |

**レスポンス**
```json
{
  "status": "success",
  "message": "'焼き魚' を登録しました",
  "meal": {
    "name": "焼き魚",
    "recipe": null,
    "registered_at": "2026-04-01T12:00:00"
  }
}
```

---

## GET /api/coop/meals

登録済みメニューの一覧を返す。

**レスポンス**
```json
{
  "count": 2,
  "meals": [
    { "name": "焼き魚", "recipe": null, "registered_at": "2026-04-01T12:00:00" },
    { "name": "肉じゃが", "recipe": { "steps": [...], ... }, "registered_at": "2026-04-01T12:10:00" }
  ]
}
```

---

## PUT /api/coop/meals/{name}/recipe

登録済みメニューにレシピを追加・更新する。

**パスパラメータ**: `name` = 登録済みメニュー名

**リクエスト**
```json
{
  "time": "40分",
  "difficulty": "普通",
  "calories": "",
  "ingredients": ["鶏もも肉 200g"],
  "steps": ["材料を切る", "煮込む"],
  "tips": []
}
```

**レスポンス**
```json
{
  "status": "success",
  "message": "'焼き魚' のレシピを更新しました",
  "meal": { ... }
}
```

---

## DELETE /api/coop/meals/{name}/recipe

登録済みメニューのレシピだけを削除する。メニュー名は残る。

**パスパラメータ**: `name` = 登録済みメニュー名

**レスポンス**
```json
{
  "status": "success",
  "message": "'焼き魚' のレシピを削除しました（メニューは残っています）"
}
```

---

## DELETE /api/coop/meals/{name}

登録済みメニューをレシピごと削除する。

**パスパラメータ**: `name` = 登録済みメニュー名

**レスポンス**
```json
{
  "status": "success",
  "message": "'焼き魚' を削除しました"
}
```

---

## PUT /api/coop/classify

商品カテゴリを手動修正して学習データに保存する。次回以降の `/ingredients` 取得時に自動適用される。

**リクエスト**
```json
{
  "original_name": "ピーマン・なすセット",
  "category": "食材"
}
```

`category` の有効な値: `"食材"` / `"調理キット"` / `"そのまま"` / `"離乳食"` / `"調味料・日用品"`

**レスポンス**
```json
{
  "status": "success",
  "message": "'ピーマン・なすセット' を '食材' に変更しました",
  "total_overrides": 3
}
```

---

## エラーレスポンス共通形式

```json
{ "detail": "エラーメッセージ" }
```

| ステータス | 状況 |
|----------|------|
| 401 | トークンが無効 |
| 400 | リクエストパラメータが不正 |
| 404 | 注文データが存在しない |
