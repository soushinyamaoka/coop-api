# Server Change Notice

record_type: server_change
template_type: full
policy_bundle_version: 2026-09-04.1
notice_id: 20261005-COOPAPI-007
app: coop-api
source_branch: main
source_commit: c94f8a0c3a310727779610d74f01b8a80d228913
production_baseline_commit: 91412f2468e3248d9ae00332058cad8ee0744895
release_commits: b2462cb6165ec837055c8eb71beae09c1d89c56c, 615df1d21f1f1f794d919f86e69bb131704d03f0, c94f8a0c3a310727779610d74f01b8a80d228913
impact_level: L3
status: ready_for_review
created_by: Claude
production_change: required
vps_management_handoff: required
deployment_status: not_started

## 変更概要

COOP注文の日付 `order_date` を、メールの受信日から、注文確認メール本文の「翌週商品配達予定日」に変える。あわせて、本文の「合計金額（本体）」「合計金額（税込）」を注文ごとに保存し、APIで返す。

- `order_date`: 本文の `翌週商品配達予定日：M月D日(曜)` を読む。年は本文に無いため、メール受信日（日本時間）に**最も近い年**で補う。
- `order_date_source`（新規）: `order_date` の決め方。`delivery_schedule`（配達予定日）／`email_date`（受信日。日本時間）／`import_time`（取込時の日付）。
- `total_amount_excluding_tax`・`total_amount_tax_included`（新規）: 合計金額（本体・税込、円）。読めない場合は `null`。
- `GET /api/coop/ingredients` と `GET /api/coop/orders` が、新しい3項目を返す。保存データにこれらが無い旧データは `null`。

## 変更理由

利用者は、注文を「届いた日」で見ている。現状は受信日で記録しているため、利用者の感覚と日付が合わない。金額は、注文の履歴（通知006）で追えるようにする予定の項目。

## server_impact判定

server_impact: approval_required
判定理由: schemaは追加のみだが、永続データ `coop_orders.json`・`coop_latest.json` の `order_date` の**意味が変わる**（受信日→配達予定日）ため、L3。反映前後で、同じ注文でも日付が変わり、履歴に新旧の日付が混在し得る。

## 現在と変更後

| 項目 | 現在（baseline） | 変更後 |
|---|---|---|
| `order_date` | メール受信日（`Date` ヘッダー） | 本文の翌週商品配達予定日。読めない場合は受信日（日本時間）、`Date` も読めない場合は取込日 |
| 年の決め方 | （該当なし） | 本文の日付に年が無いため、受信日（日本時間）との差が最小になる年（前年・同年・翌年から選ぶ） |
| 受信日の扱い | ヘッダーのタイムゾーンのまま日付を取る | 日本時間（+09:00固定）に直した日付 |
| 合計金額 | 保存しない | 本体・税込を保存。注文明細（`注文番号：`）より前の「合計金額」行だけを読む |
| `GET /api/coop/ingredients` | `order_date` ほか | 新3項目を追加（旧データは `null`）。既存項目の名前・形は不変 |
| `GET /api/coop/orders` | 注文の概要 | 各注文に新3項目を追加（旧データは `null`）。並び順は従来どおり `order_date` の新しい順 |
| 重複判定（注文日＋`source_fingerprint`） | 同左 | **変更しない**（下記「Data」の混在に注意） |

日付が決まる結果ごとの対応:

| 状況 | `order_date` | `order_date_source` | 備考 |
|---|---|---|---|
| 本文に配達予定日があり、読めた | 配達予定日 | `delivery_schedule` | 通常 |
| 本文に配達予定日が無い・書式が違う・存在しない日付（例: 2月30日） | 受信日（日本時間） | `email_date` | **ログには出ない**。API応答の `order_date_source` で判別する |
| `Date` ヘッダーが読めない | 取込時の日付（日本時間） | `import_time` | 本文に配達予定日があっても、年を補えないためこの扱い |
| 合計金額の行が無い・読めない | （日付は上記のとおり） | （同左） | 金額だけ `null` |

## 影響対象

- service/container: `coop-api.service`。設定変更なし。要restart
- URL/port/health: 変更なし。認証・bind・公開面は不変。追加は応答の項目のみ
- cron/timer/worker: 時刻・定義・lockは変更なし。`job_start`/`job_end` の成否判定・終了コードも変更なし
- dependency: 変更なし（`requirements.txt` は不変。標準ライブラリの `datetime` を追加で使うのみ。`zoneinfo` は使わない＝tzdata不要）
- data/DB/volume: 新しいfile・directoryは作らない。既存の `coop_orders.json`・`coop_latest.json` の各注文に、新しい3項目が増える
- log/monitoring: 追加・削除なし。商品名・金額・本文はログに出さない
- 配布対象: `coop_parser.py`、`coop_api_server.py`、`fetch_coop_mail.py`（`deploy-files.txt` の5file中、変更は3file）。`jev_classifier.py`・`requirements.txt`・`.env`・`data/`・test・通知書は配布対象外・不変

## production変更

- 必要性: あり
- 想定作業: VPS管理側の別承認後に、3fileを既存の共通deploy経路で反映し、service再起動と動作確認を行う
- downtime: brief-restart想定
- maintenance window: VPS管理側で決定
- 反映後の確認（案）: 次のcron取込または新しい注文メールの後に、`GET /api/coop/orders` の最新の注文で `order_date_source` が `delivery_schedule` になり、`order_date` が本文の配達予定日と一致すること。金額が本文の合計と一致すること。確認に商品名・金額の実値をログ・記録へ貼らない

## 利用者への影響

- user_maintenance_impact: none
- 対象利用者・機能: 家族2名のアプリ利用。restart中の数秒だけCOOP APIが使えない可能性がある。
- 見え方の変化: アプリ（`20261005-013` で対応済み、配信は本反映の後）が、日付を「お届け予定日」、金額を「合計 N円（税込）」と表示する。**アプリを更新していない端末**は、日付が配達予定日になるが見出しは「注文日」のまま、金額は表示されない（壊れない）。
- 反映後も、**既存の最新注文は、次の新しい注文または手動取得まで旧形式のまま**（下記「Data」）。その間、アプリは「注文日」の見出し・受信日・金額なしで表示する。

## env・secret contract

- 変更: なし
- 変数名・secret種類のみ: 変更なし
- provisioning/rotation: なし

## Data・migration・backup

- schema/format変更: 項目の追加のみ（`order_date_source`、`total_amount_excluding_tax`、`total_amount_tax_included`）。既存項目の名前・型は不変。**`order_date` の意味が、受信日から配達予定日に変わる**。
- migration: **しない**。既存データの日付は書き換えない。利用者（app owner）は、過去の注文データ（注文履歴）を、2026-10-05に消去済みと報告している。**この消去の状況（`coop_orders.json`・`coop_latest.json` の現在の中身）は、アプリ側から確認できていない**ため、反映前にVPS管理側で確認をお願いしたい。
- 新旧の日付が混在する場合の挙動:
  1. 重複判定は「注文日＋内容の指紋」のまま。同じメールでも、旧規則（受信日）で保存済みのエントリと、新規則（配達予定日）で取り込むエントリは、日付が違うため**別の注文として2件並ぶ**。
  2. 反映後、既存の最新注文は、内容が変わらない限り再取込されない（cronは「同一注文」を検出して取込をskipする）。そのため、**次の新しい注文メールが来るか、手動取得（`POST /api/coop/fetch`）をするまで、旧形式のまま**。手動取得をすると、その注文は新規則で取り込まれ、旧エントリが履歴に残っていれば**重複して並ぶ**。
  3. これらは、反映前に注文履歴（`coop_orders.json`）の旧エントリが無い状態にしておけば、起きない。
- 同じ配達予定日の注文メールは、内容が変わるたびに別のエントリになる（従来からの挙動。注文を変更するたびに確認メールが届くため）。日付が配達予定日になることで、同じ日付のエントリが複数並ぶことが、従来より目に付く。
- backup対象: `coop_orders.json`・`coop_latest.json` は既存のbackup対象（`data/` ごと）。新しいfile・一時file・directoryは追加しないため、backup除外条件に変更はない
- restore確認: 本noticeでは未実施。反映前の `coop_orders.json` のbackup確認を前提とする
- backward compatibility: endpointと認証は維持。追加項目のみ。旧アプリは新しい項目を無視する。新アプリは、項目が無い旧APIでも、従来どおり動作する（見出しは「注文日」、金額は非表示）

## Deploy・rollback

- deploy前提: 通知006（注文履歴化）が反映済みであること。baselineは006のsource `91412f2`（VPS管理の報告で反映済み。稼働hashの照合は未確認）
- deploy手順の変更: なし（共通deploy経路・`USE_VENV=yes`）
- rollback方法（artifact）: 旧artifactの3fileを再配置しservice再起動
- **rollbackに関する注意（data）**: rollbackしても、保存済みのエントリの新しい3項目は残る（旧コードは無視するだけ）。ただし旧コードは、受信日で `order_date` を付ける。そのため、rollback後に同じメールが再取込されると、新規則で保存済みのエントリ（配達予定日）と、旧規則の新エントリ（受信日）が、**別の注文として2件並ぶ**。rollback後に過去分取込（`--backfill-days`）を実行する場合は、この点に注意する。
- rollback不能条件: なし（data側の書き換えが無いため、artifact rollbackだけで戻る）

## Health・テスト

- health contract変更: なし
- 実施テスト（ローカル、合成データのみ）: `pytest`（102 passed、POSIX所有検証2 skipped）。追加した主なテスト:
  - 配達予定日の読み取り（全角数字・全角コロン・曜日あり/なし）、年の補い（年末年始をまたぐ両方向、配達日の後に届くメール）、存在しない日付（受信日へ代用）
  - 受信日の日本時間への変換（UTC `-0000` で深夜に届く場合に、日本時間の翌日になること）
  - 合計金額の読み取り（桁区切り・全角・半角括弧・欠落）、商品名に「合計金額」を含む行を金額として拾わないこと
  - APIの新項目（旧データは `null`）、既存のサンプルメールの既存項目が変わらないこと
- 実際のメールでの確認（ローカル、1通）: app ownerから受領した実際の注文確認メールのファイルを、新しい読み取り処理に通し、配達予定日・本体・税込の金額が本文の値と一致することを確認した。受信日を変えても（配達日の後、配達日の前、UTCで深夜）、同じ配達予定日になること、配達予定日の行が無い本文では受信日（日本時間）になることも確認した。**メール本文・金額・番号は、リポジトリ・通知書・記録・ログに残していない**（確認後に一時ファイルを削除。テストの金額は架空の値）。
- 結果: ローカル検証成功。ただしWindows実行環境のため、POSIXでの確認は未実施
- 未実施テストと理由:
  - production相当のファイル権限・実際のcron取込: production未反映のため
  - 実Gmailからの取込: 実メールへの接続は行っていないため
  - コープ側がメール本文の書式を変えた場合: 配達予定日が読めず受信日に代用される（上の表）。この代用はログに出ないため、気づくには `order_date_source` を見る必要がある

## Log・監視

- log量/形式/保存先変更: なし
- 新しいalert条件: なし。配達予定日が読めず `email_date` に代用されたことを検知する条件は、現状ない（API応答の `order_date_source` で確認できるのみ）。監視に加えるかは、VPS管理側で判断
- secret/個人情報対策: 商品名・金額・メール本文・件名・送信者をlogへ出さない（既存規約のまま）

## 提出前セルフチェック

通知006で全項目を実施済みのため、今回は変更範囲に絞って確認した。

- source `c94f8a0`（コード変更）と、その後の通知書commitは、実remoteの `main` へpushする（push後にlocal HEADと実remoteの一致を確認して提出する）。
- baseline `91412f2` から `source_commit` までの全3commitを `git rev-list` で取得し、`release_commits` に記載した。`91412f2..c94f8a0` のops以外の差分は、配布対象の `coop_parser.py`・`coop_api_server.py`・`fetch_coop_mail.py` と、文書・test（`API_SPEC.md`・`CLAUDE.md`・`tests/`）。`jev_classifier.py`・`requirements.txt`・`deploy-files.txt` は変更なし。依存・認証・bind・cron・env変数名・lock・ジョブの成否判定は不変。
- 配布物に `.env`・`data/`・一時file・実メールの内容が入らないこと、通知書とコミットに実際の金額・番号・URLが無いことを確認した。
- 前回の指摘（成否・未実行の分岐の説明、baseline欄はSHA単体、release_commitsは全commit）を踏まえ、日付と金額の決まり方を、結果ごとの表にした。
- VPS管理側の `review_notice_preflight.ps1` は、アプリ側のPowerShell 5.1では実行できない（通知006と同じ）。VPS管理側での実行をお願いしたい。

## 残存注文の扱い（app owner決定・2026-10-05）

VPS管理のレビューで、本番に**旧形式（受信日で保存）の注文が1件残っている**こと、同じメールの再取込で重複するリスクが指摘された（技術受理済み、本番反映は本件が決まるまで保留）。app ownerは、**コードは変えず、反映前にVPS管理側で残存注文を除去する方針**（案A）に決定した。本noticeのsource・release_commitsは変更しない。

反映手順の案（実施の可否・順序・方法はVPS管理側が決める。本番データの作業はVPS管理側の承認と実施による）:

1. 反映前に、`coop_orders.json` と `coop_latest.json` のbackupを取り、読み取れることを確認する。
2. `coop_orders.json` の旧形式のエントリ1件を除去する（ファイルは有効なJSON `orders` 配列を保つ。除去後の件数は0）。**`coop_latest.json` は、この時点では触らない**（アプリが現在の最新注文を表示し続けるため）。
3. 通知007のartifactを反映し、service再起動と、health・認証境界を確認する。
4. 反映後、**1回、手動取得（`POST /api/coop/fetch`）を実行する**。cronは、最新注文と同じ内容の注文を「同一注文」として取込を飛ばすため、手動取得をしないと、最新注文は次の新しい注文メールまで旧形式のまま残る。手動取得は、同一注文でも再取込するため、最新注文が新形式になり、履歴に新形式の1件が追加される（旧エントリを除去済みなので重複しない）。
5. 確認: `GET /api/coop/orders` の最新の注文で `order_date_source` が `delivery_schedule`、`order_date` が本文の配達予定日と一致すること。`order_date_source` が `email_date` の場合は、配達予定日が読めていない（書式の違い等）。実際の日付・金額・商品名を、ログ・記録へ貼らない。

注意:
- 手順4の手動取得は、既定で直近14日のメールを探す。対象の注文メールの受信（2026-09-30）から14日を過ぎる場合は、`days_back` を大きくする（API上限90日）。
- 手順2を省くと、手順4で、同じ注文が旧日付と新日付の2件並ぶ。
- 履歴が空になっても、アプリの「過去の注文」は「注文履歴はまだありません」と表示するだけで、壊れない。
- 恒久的な重複防止（重複判定を注文日に依存させない変更）は、今回は行わない。日付の基準を再び変える場合は、改めて検討する。

## 未解決事項

1. **production baselineの稼働hashが未確認**（※VPS管理側が照合を完了したと報告済み）。`production_deployments.yaml` の `coop-api` は `f26c211`（2026-08-30）のまま更新されていない。通知006はVPS管理がproduction反映したと報告を受けたが、反映したsourceの稼働hashはこちらでは確認できていない。`production_baseline_commit` は006の `source_commit` とした。確定はVPS管理側でお願いしたい。
2. **注文履歴・最新注文の現在の中身**（上記「Data」）の確認と、反映前のbackup確認。
3. メール本文の書式変更時の代用（`email_date`）を検知する方法（監視に加えるか）の判断。
4. アプリ（`20261005-013` で実装済み）の端末配信は、本noticeのproduction反映・確認の後に、app ownerが別途判断する。

## 希望時期

VPS管理レビュー後に別途調整。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- ユーザーへの案内: 提出前セルフチェックまで完了（preflightのみVPS管理側）
- VPS管理チャットへ渡すローカル絶対path: C:\work\PRG\HomeTools\meal-planner\api\coop-api\ops\server-change-notices\20261005-COOPAPI-007-summary.md

## Approval

- app owner: 実装提出（初回）
- VPS management review: 未実施
- production approval: 未実施
- related task_id: 20261005-014（Codex実装、Claudeがレビュー・commit）
