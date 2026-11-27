# COOP連携API 仕様書

**ベースURL**: `http://<VPS_IP>:8003`
**認証**: 全エンドポイント（`/` を除く）に `Authorization: Bearer <トークン>` が必要

| トークン | 使えるAPI |
|---|---|
| `API_TOKEN`（全権。アプリ用） | すべて |
| `API_READ_TOKEN`（参照専用。meal-api 用） | `GET /api/coop/ingredients`・`GET /api/coop/orders` だけ。ほかは **403**「参照専用のトークンでは使えない操作です」 |

- 合わないトークン・無いときは 401。`API_READ_TOKEN` が未設定なら参照専用トークンは使えない（空のトークンは一致しない）
- トークンは時間差で推測されない比べ方（`hmac.compare_digest`）で照合する
- **起動時の確認**: `API_TOKEN` が無い（空）、または `API_READ_TOKEN` が `API_TOKEN` と同じ値なら、起動しない（ログ `startup_configuration_invalid`、`reason` は `api_token_missing`・`read_token_same_as_api_token`。値は出さない。`python coop_api_server.py` では終了コード3）。以前の「`API_TOKEN` が未設定なら認証を行わない」動きは無くした

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
  "order_date_source": "delivery_schedule",
  "total_amount_excluding_tax": 5000,
  "total_amount_tax_included": 5400,
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
  ],
  "fetch_status": {
    "last_attempt_at": "2026-11-18T07:00:00+09:00",
    "last_result": "failure",
    "last_error_kind": "auth",
    "last_success_at": "2026-10-10T07:00:00+09:00",
    "consecutive_failures": 3
  }
}
```

`fetch_status` は、メールの通常取込（定期取込・`POST /api/coop/fetch`）の直近の結果。`data/coop_fetch_status.json` に取込のたびに記録する。まだ記録が無い場合は `null`。過去分の取り込み（`--backfill-days`）と、別の取込が実行中で実行しなかった場合（busy）は記録しない。

最新注文（`coop_latest.json`）がまだ無い場合は HTTP 404（`detail`: 注文データがありません）を返し、`fetch_status` は含まれない。このとき取込が失敗し続けていても、アプリはCOOPタブに警告を出せない（「一覧の読み込みに失敗しました: 注文データが見つかりません。」と表示する）。失敗の理由は、🔄（`POST /api/coop/fetch` の502）とログで確かめる。

| 項目 | 内容 |
|---|---|
| `last_attempt_at` | 直近の取込の日時（日本時間） |
| `last_result` | `success` / `failure`。注文確認メールが無かっただけの場合は `success` |
| `last_error_kind` | 失敗の種類（成功時は `null`）。下の表を参照 |
| `last_success_at` | 最後に成功した日時。一度も成功していなければ `null` |
| `consecutive_failures` | 連続して失敗した回数（成功で0に戻る） |

| `error_kind` | 内容 |
|---|---|
| `auth` | Gmailにログインできない（アプリパスワードの失効など） |
| `connection` | Gmailに接続できない・応答がない |
| `message` | 注文確認メールの取得・読み取りに失敗した |
| `configuration` | サーバーにGmailの設定が無い |
| `save` | 注文データを保存できなかった |
| `internal` | その他のエラー |

`order_date` は注文確認メール本文の「翌週商品配達予定日」を優先し、読めない場合はメール受信日時を日本時間（+09:00）にした日付、Dateヘッダーも読めない場合は取込日。`order_date_source` はそれぞれ `delivery_schedule`、`email_date`、`import_time`。金額は本文の明細より前から抽出し、読めない場合は `null`。旧保存データでは新3項目は `null`。

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

注文履歴を新しい順に返す。取込ごとに `data/coop_orders.json` へ追記され（同じ注文日・`source_fingerprint` の重複は除外）、過去の注文が残る。

**クエリパラメータ**

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|----------|------|
| `include_items` | bool | false | true なら注文ごとの商品一覧（`items`）を含める。カテゴリは学習データ適用後の値 |

**レスポンス**
```json
{
  "last_updated": "2026-03-06T20:00:00",
  "order_count": 3,
  "orders": [
    {
      "order_date": "2026-03-06",
      "order_date_source": "delivery_schedule",
      "total_amount_excluding_tax": 5000,
      "total_amount_tax_included": 5400,
      "source_fingerprint": "3f2a…",
      "email_subject": "eフレンズ注文済メモメール",
      "total_items": 12,
      "ingredient_count": 8,
      "kit_count": 1,
      "items": [
        { "name": "豚小間切", "original_name": "お米育ち豚小間切200g", "quantity": 1, "category": "食材" }
      ]
    }
  ]
}
```

`items` は `include_items=true` の時だけ含まれる。日付・金額の新項目は旧保存データでは `null`。

**過去分の取り込み（手動実行）**: `python fetch_coop_mail.py --backfill-days 365` で、指定日数内の注文確認メールを全件取り込み注文履歴にだけ追記する（`coop_latest.json` は更新しない、分類はキーワード判定のみでJevを呼ばない、ログの job 名は `coop-mail-backfill`）。取込が完了しなかった場合は終了コード1。一部取得失敗・履歴保存失敗・取得処理の異常終了は `job_end` の `status=failure`（取得済みの分は保存され、再実行しても重複しない）。別の取込が実行中の場合も終了コード1だが障害ではなく、ログは `import_skipped`（`reason=locked`）と `job_end` の `status=success` になる（未実行。終了コードだけで障害と判断しない）。production での実行は VPS管理側の手順に従う。

---

## POST /api/coop/fetch

Gmail IMAPからCOOP注文確認メールを取得してJSONに保存する。通常は scheduled worker（`fetch_coop_mail.py`）が `/etc/cron.d/coop-api`（実行ユーザー `coop-api`）で毎日 07:00 / 20:00 JST に自動実行するため、手動呼び出しは不要（旧deployユーザーのcrontabからは移行済み。※07:00 / 20:00 の自然実行の最終確認は未完了）。

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

**レスポンス（取得・保存に失敗、HTTP 502）**: Gmailにログインできない・接続が切れたなど、取得そのものに失敗した場合と、取得した注文を保存できなかった場合（`error_kind=save`。取得できた注文があっても成功にしない）。`no_data`（メールが無かった）とは区別する。`detail` はエラーレスポンス共通形式と同じく利用者向けのメッセージ、`error_kind` は `GET /api/coop/ingredients` の `fetch_status` と同じ値。
```json
{
  "status": "error",
  "error_kind": "auth",
  "detail": "COOPのメール（Gmail）にログインできませんでした。アプリパスワードが無効になっている可能性があります。"
}
```

`error_kind` の分け方: `auth` はログインの段階で認証に失敗したときだけ。接続・最初のあいさつ（IMAPの初期化）の失敗、ログインの後で接続が切れた（IMAPの `abort`・ソケットの切断）・応答が異常だった場合は `connection`。IMAPの接続・応答は60秒で打ち切る（`connection`）。

**取り込む注文の選び方（通常取込）**: 新しく届いた順に最大5通を見て、次のように決める（分類（Jev）は取り込むと決めた1通だけ）。
- 保存済みの最新注文より前の注文日（配達予定日）の注文（古い注文メールの再送など）は取り込まず、次の候補を見る。最新注文は古い注文に戻らない。
- 最新注文と同じメールは、定期取込では取り込まず、次の候補を見る（その後に届いた新しい注文を見落とさない）。手動取得では、より新しい注文が無ければ最新注文を取り込み直す。
- 最新注文と同じ週の別の版（注文の変更）は、メールの日時（Date）が最新注文より後のときだけ取り込む。変更前の版は、後から再送されても、最新注文のメールが見つからなくても取り込まない。メールの日時が読めない場合は、最新注文と同じメールより前に届いたものを変更前の版とみなす。
- 古い注文しか無かった場合は `no_data`。古い注文を履歴に足すときは、過去分の取り込み（`--backfill-days`）を使う。
- 履歴の再試行: 前回、最新注文だけ保存できて注文履歴に入らなかった注文は、次の取込で履歴に足す。
  - 新しい注文が無かったとき（定期取込の「変化なし」・手動の `no_data`）は、最新注文が履歴に無ければ足す。
  - 新しい注文を取り込むときは、最新注文を置き換える前に、いまの最新注文が履歴に無ければ新しい注文と一緒に足す。
  - 履歴を保存できず、いまの最新注文が履歴に入ったと確かめられない間は、最新注文を新しい注文に進めない（前回分を失わないため。502 `error_kind=save`）。新しい注文は、履歴を保存できるようになった次の取込で、前回分と一緒に保存する。
  - 足せなければ 502（`error_kind=save`）。

**保存**: 注文履歴（`coop_orders.json`）・最新注文（`coop_latest.json`）・取込状況（`coop_fetch_status.json`）は、一時ファイル（`.<名前>.<ランダム>.tmp`）に書いてから置き換える。書き込みに失敗しても元のファイルは残り、502（`error_kind=save`）を返して `fetch_status` に失敗を記録する。既存のファイルの mode は引き継ぎ、新しく作るときは 0640（umask に左右されない）。取込・保存・取込状況の記録は、定期取込と手動取得で共通の lock の中で行う。

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
| 502 | メールの取得に失敗した（`POST /api/coop/fetch`。`status`・`error_kind` も返す） |
