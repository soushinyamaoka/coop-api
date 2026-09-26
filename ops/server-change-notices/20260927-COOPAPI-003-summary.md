# Server Change Notice

record_type: server_change

template_type: full

policy_bundle_version: 2026-09-04.1

notice_id: 20260927-COOPAPI-003

app: coop-api

source_branch: main

source_commit: 03f65b2aa421a76d31833a25aa871a205d1e1896

production_baseline_commit: f26c2119ac5b3677916e5e4afe02242565a6da4f

release_commits: 03f65b2aa421a76d31833a25aa871a205d1e1896, 116fbf5e993bfd6b1e9292036d17e7766d6cda3d, e5efee4d995c60154fcdfb680565bbf75c8aab10, 00890f1e761f0c38f89bf511200e93816e7c51b0, 9cda9fe9c0b57f1b400f94cfc2962f337cc1dd7b, aa164021830355807c9592d41d9b9bfcdd3c641b, ba76f9744aa528d8e23421c1d3612d454223fa00, 84494fe963c7bca963de1bbfc73834fec79ba308, 74e76dd77bfaf801136b4bd23cf84f3bbae7df3f

impact_level: L3

status: ready_for_review

created_by: Codex

production_change: required

vps_management_handoff: required

deployment_status: not_started

## 変更概要

フィーチャーフラグで既定無効のJev商品分類を追加し、既存キーワード分類をフォールバックにする。分類結果への `classifier` / `classifier_confidence` の追加、Jev結果キャッシュファイルの導入、対象外行の既存excluded配列への追加、キーワード誤分類2件の修正を行う。task 20260927-002 では、cron取込時に前回と同じ注文内容なら分類・Jev問い合わせ・保存を省略し、保存データへ `source_fingerprint` を追加する。また、Jev判定照合漏れ時の既定分類をキーワード判定へ修正する。task 20260927-003 では、配布一覧に遅延importで必須となる `jev_classifier.py` を含め、runtime/data・配布経路の記述を実装と照合し、deploy前backupと復元条件を明確化した。

## 変更理由

任意でより精度の高い分類を選べるようにしつつ、既定のキーワード分類とオフライン動作を維持する。誤分類された油揚げとドリアの扱いを修正する。定期取込では同一注文の再分類と再書込を避け、実際の注文内容が変わった場合だけ通常処理する。

## server_impact判定

server_impact: approval_required

判定理由: cron workerと同期 `/api/coop/fetch` の共通取込処理に分類と外部API呼び出しを追加する。既定無効だが、利用者が明示的に有効化すると商品名を api.typesafe.ai へ送信し、15秒予算内で最大8並列・1要求10秒timeoutで問い合わせる。永続JSONの分類意味が変わり得るためL3とし、VPS管理レビュー、data backup/rollbackおよびproduction反映の個別承認が必要。task 20260927-002 ではさらにcronの同一注文時の成功終了・保存省略と、注文JSONへの指紋項目追加があるため、定期jobの成功判定と永続data互換性をVPS管理側で確認する。

## 現在と変更後

| 項目 | 現在 | 変更後 |
|---|---|---|
| 分類処理 | キーワード分類のみ | 既定はキーワード。CATEGORY_CLASSIFIER=jevかつTYPESAFE_API_KEY設定時はJevを選択可能 |
| 定期取込 | 毎回分類してJSON保存 | 注文行が前回と同じなら分類・Jev・保存を省略し、成功終了 |
| 注文JSON | 分類・数量等を保存 | 既存fieldに `source_fingerprint` を追加 |
| Jev照合fallback | 未照合時に一律「食材」 | 商品名の空白を整え、未照合時はキーワード分類 |
| 外部接続 | 分類時の外部接続なし | Jev有効時に商品名ごとにapi.typesafe.aiへ要求。低信頼・失敗はキーワードへfallback |
| 保存データ | 注文JSONにcategory | classifierと採用時のconfidenceを追加。別ファイルに商品名と判定結果のみをcache |
| 対象外行 | 0点注文のみexcluded | 高信頼の対象外行も既存excludedへ追加 |
| 配布artifact | `deploy-files.txt`の一覧に `jev_classifier.py` がない | 関数内遅延importを含むアプリ内ローカルmoduleを全て一覧に含める。`.env` と `data/` は転送対象外 |

## 影響対象

- service/container: coop-api serviceおよび定期workerのアプリコード。service定義変更なし。配布候補のPythonファイル一覧へ`jev_classifier.py`を追加。
- URL/port/health: 変更なし。
- cron/timer/worker: schedule変更なし。workerは同一注文時に分類・Jev問い合わせ・保存を省略し、注文内容変更時のみ通常処理する。`job_end` は成功として記録する。
- dependency: 新規依存なし。既存httpxを使用。
- data/DB/volume: data/category_jev_cache.jsonを新規作成。coop_latest.json / coop_orders.jsonに追加項目と分類値が保存される。`source_fingerprint` は正規化・整列した注文番号、商品名、数量のSHA-256で、既存データは移行不要。同一注文判定は既存latestのfield欠損・破損時に通常処理へfallbackする。
- log/monitoring: Jev件数、cache hit、採用、fallback、エラー種別の集計ログと、同一注文時の `import_skipped` イベントを追加。商品名、指紋、API key、応答本文は出力しない。
- runtime contract: `ops/runtime-contract.yaml` をschema v1へ更新（env_vars、persistent_paths、jobs、dependencies、deployの各項目を反映）。

## production変更

- 必要性: あり
- 想定作業: VPS管理レビュー後、承認されたartifactを配置し、必要なenv provisioning、service/worker反映、healthおよび注文取込を検証する。今回の作業では実施していない。
- downtime: service再起動の際に短時間のAPI停止可能性。実際の時間はVPS管理側のdeploy計画で決定。
- maintenance window: VPS管理側reviewで要否を決定。

## 利用者への影響

- user_maintenance_impact: possible
- 対象利用者・機能: 商品カテゴリ、献立候補および定期/手動メール取込結果。Jev既定無効のため、通常の未設定環境ではJev外部送信なし。
- 通知方法: downtimeの有無をVPS管理側がdeploy計画で判断。

## env・secret contract

- 変更: あり
- 変数名・secret種類のみ: CATEGORY_CLASSIFIER、TYPESAFE_API_KEY（TypeSafe API credential）
- provisioning/rotation: Jevを有効化する場合のみVPS管理レビュー後にprovisioning。値はこのnoticeに記録しない。`ops/runtime-contract.yaml`の`config.note`に、現時点でprovisioningしない方針を明記した。

## Data・migration・backup

- schema/format変更: 注文itemへclassifierを追加、Jev採用時はclassifier_confidenceを追加。対象外はexcludedへ。注文全体へ`source_fingerprint`（既存項目、SHA-256、注文番号・商品名・数量のみから算出）を追加。
- migration: 既存注文JSONのmigrationなし。新cache（`data/category_jev_cache.json`）は欠損・破損時に空cacheとして継続する。CATEGORY_CLASSIFIER=keyword（既定）の間はこのcacheファイル自体が生成されない。
- backup対象: 既存のbackup計画（VPS管理側 `OPS-BKP-04`、`/opt/apps/coop-api/data` を丸ごと `/var/backups/vps-app-db/coop-api/` へ）が対象ディレクトリ全体を扱っており、`coop_orders.json` / `coop_latest.json` / `category_overrides.json` / （生成された場合の）`category_jev_cache.json` は追加設定なしでこのbackupに含まれる。本noticeで新たなbackup対象・保存先の変更はない。
- restore手順:
  1. `/var/backups/vps-app-db/coop-api/` から対象時点のbackupのchecksumと取得日時を確認する。
  2. 本番ディレクトリを直接上書きせず、隔離先（例: 一時ディレクトリ）へ展開する。
  3. `coop_orders.json` / `coop_latest.json` は `json.load()` でparse可能なことを確認し、トップレベルkey（`orders`配列、または`ingredients`/`kits`/`ready_to_eat`/`baby_food`/`seasonings`/`excluded`の各配列）の存在と、`total_items`・`excluded_count`が実item数と一致することを確認する。`category_overrides.json`は`original_name`をkeyとする辞書であることを確認する。
  4. `category_jev_cache.json`は復元必須ではない（regenerable: true）。破損・欠損時は空cacheとして扱われ、次回Jev問い合わせ時に再生成される。
  5. 復元後、隔離先の内容を正本パスへ反映し、`GET /api/coop/ingredients`（代表read flow）で分類結果が想定どおり返ることを確認する。復元前のデータは別途保持し、復元直後に削除しない。
  6. 実施日時・使用backupの取得日時・確認結果を記録する。secret値・商品名の内容そのものは記録しない。
- backward compatibility: 追加fieldは後方互換を意図。既存データ読み込み側の未知field許容をVPS側で確認。

## Deploy・rollback

- deploy前提: cron（07:00 / 20:00 JST）と重ならない時間帯を選び、手動 `/fetch` とカテゴリ・献立の変更操作を止めて整合した時点を作る。その時点の `/opt/apps/coop-api/data` 全体を即時退避し、checksum・取得日時・保持先を記録する（毎日の `OPS-BKP-04` とは別）。具体的な保持先と書込停止方法はVPS管理側のproduction実施計画で確定する。healthと代表read flowの確認が終わるまで退避物を保持する。加えてnotice受理、VPS管理review、rollback手順確定、必要なproduction承認を要する。
- deploy手順の変更: 通常時なし。Jevを有効化する場合はenv provisioning（後述の同時実行対応完了後）が必要。
- rollback方法: アプリartifactは直前versionへ手動再配置＋service再起動。dataのrollbackは、cronと手動書込（`/fetch`、カテゴリ・献立変更）を止め、現行data全体を復元前状態として別に退避する。日次またはdeploy直前backupを隔離先へ展開して検証後、正本パスへ切り替え、serviceと代表read flowを確認してから書込を再開する。復元前・復元後のdataは確認終了まで削除しない。artifact rollbackとdata rollbackは別手順として扱い、具体的な手順・保持先はVPS管理側の実施計画で確定する。
- rollback不能条件: 復元元のbackupがなく、注文JSON等が上書きされた場合、元データの復元が困難。

## Jev cacheの同時実行と有効化境界（COOPAPI-003-B04）

- 現状（本notice対象の変更後）: `data/category_jev_cache.json`への書き込みはread-modify-write全体を通した排他制御（lock等）を実装しておらず、cron（07:00/20:00 JST固定）と手動`/api/coop/fetch`が理論上同時に走った場合の更新競合は未検証。
- ただし、`CATEGORY_CLASSIFIER`の既定値は`keyword`であり、本notice時点でproductionへ`TYPESAFE_API_KEY`をprovisioningする計画はない。両条件が揃わない限りJev問い合わせとcache書き込みは一切発生しないため、**本notice・現在のdeployment計画の範囲では同時実行リスクは顕在化しない。**
- `CATEGORY_CLASSIFIER=jev`への切り替えとTYPESAFE_API_KEYのprovisioningは、本noticeに含めず、**別個のproduction承認事項**として扱う。その承認申請の前提条件として、cache read-modify-writeの直列化（lock実装）とその検証を完了させることを`ops/runtime-contract.yaml`（`jobs[].concurrency_note`）に明記した。

## Health・テスト

- health contract変更: なし。
- 実施テスト: task 20260927-001 は `.venv-codex`でpytest 52件pass。task 20260927-002 は `.venv-codex`でpytest 64件pass、`PYTHONIOENCODING=utf-8 python coop_parser.py` exit 0、`git diff --check` pass。task 20260927-003 はpytest 64件pass、`PYTHONIOENCODING=utf-8 python coop_parser.py` exit 0、配布候補5ファイルのみの隔離import pass、旧一覧の `jev_classifier` import失敗を確認。AST全走査でローカルmodule依存を網羅し、配布一覧との包含を確認。モックIMAP/分類器を使い、実メール・外部APIなし。
- 未実施テストと理由: production/VPS接続、実Jev API接続、cron実機検証はtaskで禁止。同一注文のmockテストは実施したが、VPS上でのcron実行・既存JSONへの初回fallbackは未確認。

## Data復元時の構造検証（B02）

- `coop_orders.json`: `orders`配列の各レコードについて、`total_items`が`ingredients`、`kits`、`ready_to_eat`、`baby_food`、`seasonings`各配列の要素数合計と一致し、`excluded_count`が`excluded`配列の要素数と一致することを確認する。数量0の商品と分類結果が「対象外」の商品はいずれもカテゴリ配列ではなく`excluded`へ入り、`total_items`には含まれない。
- `coop_latest.json`: 単一レコードに対し、同じ5カテゴリ配列の要素数合計と`total_items`、および`excluded`配列の要素数と`excluded_count`を照合する。
- `category_overrides.json` / `custom_meals.json`: JSONとしてparse可能で、トップレベルが辞書であることを確認する。
- 復元ドリルそのものは別のproduction作業として扱い、本taskでは実施しない。

## Log・監視

- log量/形式/保存先変更: Jev集計イベントを既存の1行JSON構造化ログへ追加。
- 新しいalert条件: なし。
- secret/個人情報対策: key、商品名、応答本文をログへ含めない。Jev応答本文もキャッシュしない。

## 提出前セルフチェック（2026-09-27 再提出）

- production baseline: 確認済み。coop-api deployed source `f26c2119ac5b3677916e5e4afe02242565a6da4f`。
- source commitとbaseline以降の全release commit/build差分: source commitは本taskのcommit 1。baselineからcommit 1までのrelease commit全件を記載する。notice文書のcommit 2はrelease_commitsへ含めない。
- B02/B03/B05: deploy直前backupと復元順序・レコード照合を記載。`coop_orders.json`上書き仕様、`custom_meals.json`のbackup要否、実際のdeploy経路をcontractへ反映。`.env`を除く配布候補5ファイルと遅延import依存の対応を検証。
- data transaction、同時実行、途中失敗、再実行: cache一時ファイル置換と読書失敗時fallbackは実装済み。cron/`/fetch`間の排他制御は未実装（コード変更なし）。ただしCATEGORY_CLASSIFIER既定keyword・TYPESAFE_API_KEY未provisioningのため当該cache自体が現状生成されない。詳細は上記「Jev cacheの同時実行と有効化境界」参照。
- image rollbackとdata rollback: artifact/dataを分け、deploy直前backup、書込停止、復元前data退避、隔離展開・検証、切替、read flow確認、保持条件を上記「Deploy・rollback」に記載した。
- job/log/retention、runtime/dependency、client連携: schedule/runtime/依存追加/client配信は変更なし。`ops/runtime-contract.yaml`をschema v1へ更新し、env_vars・persistent_paths・jobs・dependencies・deployの各項目を反映した。
- owner/review/production承認/client配信: app実装とproduction反映の承認を分離。今回deployなし。Jev有効化は別個のproduction承認事項として明記した。
- B05配布候補import検証: 一覧5ファイルのみを一時ディレクトリへコピーし、`coop_parser`、`jev_classifier`、`fetch_coop_mail`、`coop_api_server`のimport成功を確認する。旧一覧から`jev_classifier.py`を除いた対照では`jev_classifier`のimport失敗を確認する。関数内遅延importを含むAST走査でローカルモジュール参照が配布一覧に含まれることを確認する。
- notice status: commit 2で`ready_for_review`へ更新。remoteへのpushは行わない。
- VPS運用ポリシー同期: 配布記録の旧hashと作業時点の正本hashが一致しないため、現行正本を優先。アプリ側共通指示のversion/hash同期は別途必要。

未確認・該当なしの理由: 実VPSでの同時実行実機検証、backupからの実restoreドリルは、production接続を伴うため本レビュー対応の範囲外（VPS管理側の別作業）。

## 未解決事項

- VPS管理側で、上記restore手順の実機ドリル実施可否と時期を判断する。
- Jevをproductionで有効化する場合、cache直列化（lock実装・検証）の完了を前提に、TYPESAFE_API_KEYのprovisioningと商品名送信の最終確認を別途行う。

## 希望時期

VPS管理review（再審査）後に決定。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- ユーザーへの案内: この無人経路では案内文を作成しない。通知pathはresultから参照。

## Approval

- app owner: task 20260927-001, 20260927-002の実装承認、およびtask 20260927-003のsource・文書変更と2 commit作成が承認済み。
- VPS management review: 初回`blocked`（2026-09-27、B01〜B04）。B01/B04は解消済み。残blocker B02/B03/B05に対する本taskの対応を記録し、再審査依頼として提出（VPS管理レビュー記録（coop_api_server_notice_review_20260927））。
- production approval: 未取得
- related task_id: 20260927-001, 20260927-002, 20260927-003
