# Server Change Notice

record_type: server_change
template_type: full
policy_bundle_version: 2026-09-04.1
notice_id: 20260930-COOPAPI-005
app: coop-api
source_branch: main
source_commit: c1c0505d88f904529a2e1dafe1066aad6c360430
production_baseline_commit: f4259490f78f5b358a6eec938e86281148866394
release_commits: be9f60a131558be92d1b889bde04328cf47e656a, cce6a9c6b03e917d8c2a39392f5a48c72f95a3a3, fecc422e95bd45e8507cdbaf269e087be525537b, 5bdb6aa7c868c2e371d017635a35ea2e0f20f02e, c1c0505d88f904529a2e1dafe1066aad6c360430
impact_level: L3
status: ready_for_review
created_by: Codex
production_change: required
vps_management_handoff: required
deployment_status: not_started

## 変更概要

分類override PUTを専用のプロセス・スレッドlock内でread-modify-writeし、`data/.category_overrides_tmp/` 内の一時fileから原子的に置換する。Jev cache一時fileは `data/.jev_cache_tmp/` 配下に置く。

## 変更理由

分類更新の競合による更新喪失と不完全JSON読取、およびJev cache保存中の一時fileによる日次backup競合を防止する。

## server_impact判定

server_impact: approval_required
判定理由: 永続分類dataの書込・mode/group・backup契約、APIの503応答契約を変更するL3。VPS管理側は一時directoryの除外と日次backup/manifest復旧を確認済み。API反映後のservice userでの生成権限と実分類JSONのbackup/隔離restore確認が必要。

## 現在と変更後

| 項目 | 現在 | 変更後 |
|---|---|---|
| PUT競合制御 | 分類PUTにlockなし | 固定lock fileとthread lock、最大2秒。競合時503 |
| 分類JSON保存 | 同一fileへ直接書込 | 専用一時directory、flush/fsync、data directory groupと0640を検証後replace |
| Jev cache一時file | data直下 | data/.jev_cache_tmp/、同一filesystemを確認 |
| backup契約 | cache final名のみ除外 | 専用一時directoryを事前読取検査・tar双方で厳密除外する必要 |

## 影響対象

- service/container: coop-api.service。設定変更なし
- URL/port/health: 変更なし。PUT競合時503
- cron/timer/worker: 変更なし
- dependency: 変更なし
- data/DB/volume: category_overrides.jsonとcache一時pathの保存動作変更。永続形式・schemaは不変。overrideはbackup必須
- log/monitoring: 商品名やdata値を含まない固定エラーlog

## production変更

- 必要性: あり
- 想定作業: VPS管理側で適用済みbackup修正版を維持。別途承認後にartifactを反映し、service user生成後の権限、分類JSONの次回archive収録・隔離restore、APIを確認
- downtime: brief-restart想定（実作業計画で確認）
- maintenance window: VPS管理側で決定

## 利用者への影響

- user_maintenance_impact: possible
- 対象利用者・機能: 分類PUTの競合時、固定日本語detailの503。meal-planner-appは非2xxを失敗として表示し、現在の一覧を保持して再読込を案内することをread-onlyで確認済み。端末配信は保留
- 通知方法: VPS管理側reviewで決定

## env・secret contract

- 変更: なし
- 変数名・secret種類のみ: 変更なし
- provisioning/rotation: なし

## Data・migration・backup

- schema/format変更: なし
- migration: なし
- backup対象: category_overrides.jsonは必須。category lock、`.category_overrides_tmp/`、`.jev_cache_tmp/`、再生成可能な `category_jev_cache.json` は不要
- restore確認: backup修正版の手動・自然実行、manifest strict、既存COOP archiveの隔離restoreはVPS管理側で成功。productionに分類overrideがまだ存在しないため、分類JSONを含む次回archive・隔離restoreは初回実分類書込後に確認する
- backward compatibility: JSON形式とendpointは維持。PUT競合時は503

## Deploy・rollback

- deploy前提: backup修正版の厳密除外と自然実行・manifest strictは確認済み。artifact反映後、service userで生成した分類JSON/lockのowner/group/modeとdeploy readを確認し、分類JSONが次回archiveへ収録されて隔離restoreできることを確認
- deploy手順の変更: なし
- rollback方法: 旧artifactを再配置しservice再起動。data rollbackは確認済backupから別途実施
- rollback不能条件: 実data/backupを確認せずにdata rollbackしない

## Health・テスト

- health contract変更: なし
- 実施テスト: 指定Pythonで合成データ限定 `pytest tests`（82 passed、POSIX所有検証2 skipped）、`coop_parser.py`（UTF-8標準出力でexit 0）、`git diff --check`（pass）
- 結果: ローカル検証成功。Windows実行環境のためPOSIX file mode/group確認は未実施。別プロセスの並行PUTと非OSError保存失敗時のcleanupも合成データで確認
- 未実施テストと理由: service userでの生成後owner確認と実分類JSONを含むarchive・隔離restoreは、production API反映前で分類override未作成のため未実施。VPS管理側のbackup script除外、自然backup、manifest strict、既存archive隔離restoreは確認済み

## Log・監視

- log量/形式/保存先変更: 固定メッセージのerror logのみ
- 新しいalert条件: なし
- secret/個人情報対策: 商品名・JSON内容をlogへ出さない

## 提出前セルフチェック

- 初回提出前セルフチェック（2026-09-30 JST、1回）: 配布済みAIポリシー `2026-09-05.1` と正本SHA-256は一致し、full notice template `2026-09-04.1` を使用。初回時点で参照可能なbaselineコピーに差があったが、VPS管理初回レビューで稼働正本 `f425949` を確認し、旧値 `f26c211` のコピーは現行正本として採用しないと確定した。
- 初回sourceまでのcommitは `be9f60a`（旧notice 004の文書）、`cce6a9c`（今回のコード2件・runtime contract・合成試験）。その後の `fecc422` と `5bdb6aa` は通知005だけのcommit。今回のB01対応 `c1c0505` はruntime contractだけを変更し、再提出sourceまでの全5commitを上記に列挙した。配布一覧は `coop_parser.py`、`jev_classifier.py`、`fetch_coop_mail.py`、`coop_api_server.py`、`requirements.txt`。`.env`、`data/`、一時file、notice、runtime contract、testは配布対象外。依存version・共通deploy経路・bind・service・cron・認証に変更なし。
- 初回提出前のVPS管理read-only preflight（notice commit `fecc422`、実remote照合付き）は `ready_with_manual_checks`、blocker 0、機微値候補 0、source `cce6a9c`、実remote `fecc422`、tracked clean。manual checkの未追跡1件は既存の `AGENTS.md` であり配布一覧にない。この結果は通知更新commit `5bdb6aa` に記録し、同commitの実remote一致も初回レビューで確認済み。
- 空欄商品名は既存どおり空文字keyとして保存され得る。JSON形式は不変で旧clientは競合時503を非2xxとして扱う。並行PUT、失敗後の旧file保持・cleanup、例外後のlock解放は合成データで確認。失敗後の明示的な再試行試験、POSIX権限とbackup実行userの読取、分類overrideを含む稼働backup・隔離復元は未確認。data rollbackは確認済backupの隔離復元後にのみ検討し、artifact rollbackと分離する。
- B01再提出時の増分確認: `5bdb6aa..c1c0505` はruntime contractのJev状態に関する文書4か所のみ。004でJevがproduction有効化・実行確認済みである事実と、005の保存方式は未反映である事実を分けた。コード・配布一覧・依存・認証・データ形式は不変。文書差分のためアプリ試験は再実行しない。通知最終commitのpushと実remote一致を確認して再提出する。

## 未解決事項

- VPS管理はbackup修正版を稼働させ、2026-10-01 03:10 JSTの自然実行、COOP job success、manifest strict（60 files・13,464,751 bytes）を確認済み。既存archiveの隔離restoreも成功。
- 2026-10-01時点でproductionに分類overrideはない。API反映後、service userが生成したJSON/lockのowner/group/modeとdeployからの読取を確認し、実分類JSONが次回archiveに含まれることと隔離restoreを確認する。
- B01対応source `c1c0505` はpush済み。VPS管理側はsource・実remote・baselineを確認し、B01解消を認めて技術受理した（2026-09-30）。sourceは今回も変更していない。
- meal-planner-app端末配信は保留。

## 希望時期

VPS管理レビュー後に別途調整。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- ユーザーへの案内: 初回提出およびB01訂正の再提出済み。VPS管理で技術受理
- VPS管理チャットへ渡すローカル絶対path: C:\work\PRG\HomeTools\meal-planner\api\coop-api\ops\server-change-notices\20260930-COOPAPI-005-summary.md

## Approval

- app owner: 実装提出・B01訂正済み
- VPS management review: 技術受理（2026-09-30）。production反映は別承認。日次backup/manifestは確認済み、生成後権限と分類JSONを含む次回archive/隔離restoreは未確認
- production approval: 未実施
- related task_id: 20260930-009
