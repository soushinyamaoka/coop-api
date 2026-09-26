# Server Change Notice

record_type: server_change

template_type: full

policy_bundle_version: 2026-09-04.1

notice_id: 20260927-COOPAPI-003

app: coop-api

source_branch: main

source_commit: 未確定（作業ツリー）

production_baseline_commit: f26c2119ac5b3677916e5e4afe02242565a6da4f

release_commits: 未確定（task 20260927-001 の未commit差分）

impact_level: L3

status: draft

created_by: Codex

production_change: required

vps_management_handoff: required

deployment_status: not_started

## 変更概要

フィーチャーフラグで既定無効のJev商品分類を追加し、既存キーワード分類をフォールバックにする。分類結果への `classifier` / `classifier_confidence` の追加、Jev結果キャッシュファイルの導入、対象外行の既存excluded配列への追加、キーワード誤分類2件の修正を行う。

## 変更理由

任意でより精度の高い分類を選べるようにしつつ、既定のキーワード分類とオフライン動作を維持する。誤分類された油揚げとドリアの扱いを修正する。

## server_impact判定

server_impact: approval_required

判定理由: cron workerと同期 `/api/coop/fetch` の共通取込処理に分類と外部API呼び出しを追加する。既定無効だが、利用者が明示的に有効化すると商品名を api.typesafe.ai へ送信し、15秒予算内で最大8並列・1要求10秒timeoutで問い合わせる。永続JSONの分類意味が変わり得るためL3とし、VPS管理レビュー、data backup/rollbackおよびproduction反映の個別承認が必要。

## 現在と変更後

| 項目 | 現在 | 変更後 |
|---|---|---|
| 分類処理 | キーワード分類のみ | 既定はキーワード。CATEGORY_CLASSIFIER=jevかつTYPESAFE_API_KEY設定時はJevを選択可能 |
| 外部接続 | 分類時の外部接続なし | Jev有効時に商品名ごとにapi.typesafe.aiへ要求。低信頼・失敗はキーワードへfallback |
| 保存データ | 注文JSONにcategory | classifierと採用時のconfidenceを追加。別ファイルに商品名と判定結果のみをcache |
| 対象外行 | 0点注文のみexcluded | 高信頼の対象外行も既存excludedへ追加 |

## 影響対象

- service/container: coop-api serviceおよび定期workerのアプリコード。service定義変更なし。
- URL/port/health: 変更なし。
- cron/timer/worker: schedule変更なし。worker取込ロジックが変わる。
- dependency: 新規依存なし。既存httpxを使用。
- data/DB/volume: data/category_jev_cache.jsonを新規作成。coop_latest.json / coop_orders.jsonに追加項目と分類値が保存される。
- log/monitoring: Jev件数、cache hit、採用、fallback、エラー種別の集計ログを追加。商品名、API key、応答本文は出力しない。

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
- provisioning/rotation: Jevを有効化する場合のみVPS管理レビュー後にprovisioning。値はこのnoticeに記録しない。

## Data・migration・backup

- schema/format変更: 注文itemへclassifierを追加、Jev採用時はclassifier_confidenceを追加。対象外はexcludedへ。
- migration: 既存注文JSONのmigrationなし。新cacheは欠損・破損時に空cacheとして継続する。
- backup対象: category_jev_cache.json、coop_latest.json、coop_orders.json。
- restore確認: 未実施。cacheは再生成可能だが、注文JSONの分類値を戻す手順はVPS管理reviewで確定する。
- backward compatibility: 追加fieldは後方互換を意図。既存データ読み込み側の未知field許容をVPS側で確認。

## Deploy・rollback

- deploy前提: notice受理、VPS管理review、backup/rollback手順確定、必要なproduction承認。
- deploy手順の変更: 通常時なし。Jevを有効化する場合はenv provisioningが必要。
- rollback方法: アプリartifactを戻す。cacheは旧版で未使用。注文JSONを旧版へ戻す場合のrestore方法は要確定。
- rollback不能条件: rollback前に旧バックアップがなく注文JSONが上書きされた場合、元の自動分類値の復元が困難。

## Health・テスト

- health contract変更: なし。
- 実施テスト: `.venv-codex`でpytest 52件pass、`PYTHONIOENCODING=utf-8 python coop_parser.py` exit 0、`git diff --check` pass。
- 未実施テストと理由: production/VPS接続、実Jev API接続、cron実機検証はtaskで禁止。

## Log・監視

- log量/形式/保存先変更: Jev集計イベントを既存の1行JSON構造化ログへ追加。
- 新しいalert条件: なし。
- secret/個人情報対策: key、商品名、応答本文をログへ含めない。Jev応答本文もキャッシュしない。

## 提出前セルフチェック

- production baseline: 確認済み。coop-api deployed source `f26c2119ac5b3677916e5e4afe02242565a6da4f`。
- source commitとbaseline以降の全release commit/build差分: 作業中でsource未commitのため未確定。
- data transaction、同時実行、途中失敗、再実行: 実装ではcache一時ファイル置換と読書失敗時fallbackを追加。実VPSでの同時worker動作、注文JSON rollbackは未検証。
- image rollbackとdata rollback: artifact rollbackとJSON backup restoreを分ける必要あり。restore手順はVPS管理review待ち。
- job/log/retention、runtime/dependency、client連携: schedule/runtime/依存追加/client配信は変更なし。cache retention方針とログ監視はVPS管理側で確認。
- owner/review/production承認/client配信: app実装とproduction反映の承認を分離。今回deployなし。
- noticeはdraftのまま。未commit・未pushのためready_for_reviewではない。

未確認・該当なしの理由: source commitとrelease範囲は未commit。VPS側同時実行・data restore・cache retentionはproductionに接続できない無人taskのため未確認。

## 未解決事項

- VPS管理側で注文JSONのbackup/rollbackとcache retentionを決定する。
- Jevをproductionで有効化する場合、TYPESAFE_API_KEYのprovisioningと商品名送信の最終確認を行う。
- source commitがないため、このnoticeはdraft。VPS reviewに提出する前にsource/release情報と提出手順を更新する。

## 希望時期

VPS管理review後に決定。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- ユーザーへの案内: 未実施（無人実行経路。resultへhandoffを記録）
- VPS管理チャットへ渡すローカル絶対path: 未記載。公開リポジトリへローカル絶対パスを保存しないため、resultから参照する。

## Approval

- app owner: task 20260927-001で実装承認済み
- VPS management review: 未実施
- production approval: 未取得
- related task_id: 20260927-001
