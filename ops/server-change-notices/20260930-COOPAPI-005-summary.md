# Server Change Notice

record_type: server_change
template_type: full
policy_bundle_version: 2026-09-04.1
notice_id: 20260930-COOPAPI-005
app: coop-api
source_branch: main
source_commit: cce6a9c6b03e917d8c2a39392f5a48c72f95a3a3
production_baseline_commit: f4259490f78f5b358a6eec938e86281148866394
release_commits: be9f60a131558be92d1b889bde04328cf47e656a, cce6a9c6b03e917d8c2a39392f5a48c72f95a3a3
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
判定理由: 永続分類dataの書込・mode/group・backup契約、APIの503応答契約を変更するL3。backup scriptの事前読取検査とtar双方で一時directoryを正確に除外し、稼働確認するまでproduction反映できない。

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
- 想定作業: VPS管理側でbackup scriptの `.category_overrides_tmp/` と `.jev_cache_tmp/` の専用path除外を事前読取検査とtarへ追加し稼働確認。その後に別途承認されたartifact反映と権限・API確認
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
- restore確認: VPS管理側のbackup修正版で分類JSONを含む実backupの復元確認が未了
- backward compatibility: JSON形式とendpointは維持。PUT競合時は503

## Deploy・rollback

- deploy前提: backup scriptの`.category_overrides_tmp/`と`.jev_cache_tmp/`厳密除外を事前読取検査とtar双方に実装し、VPS管理側で稼働確認。さらに分類overrideを含む実backupの確認と隔離復元確認
- deploy手順の変更: なし
- rollback方法: 旧artifactを再配置しservice再起動。data rollbackは確認済backupから別途実施
- rollback不能条件: 実data/backupを確認せずにdata rollbackしない

## Health・テスト

- health contract変更: なし
- 実施テスト: 指定Pythonで合成データ限定 `pytest tests`（82 passed、POSIX所有検証2 skipped）、`coop_parser.py`（UTF-8標準出力でexit 0）、`git diff --check`（pass）
- 結果: ローカル検証成功。Windows実行環境のためPOSIX file mode/group確認は未実施。別プロセスの並行PUTと非OSError保存失敗時のcleanupも合成データで確認
- 未実施テストと理由: backup実行userからの実読取、VPS上の除外と稼働・復旧・隔離復元はproduction接続禁止のためVPS管理側確認

## Log・監視

- log量/形式/保存先変更: 固定メッセージのerror logのみ
- 新しいalert条件: なし
- secret/個人情報対策: 商品名・JSON内容をlogへ出さない

## 提出前セルフチェック

提出準備の確認: VPS管理の配布済みAIポリシー `2026-09-05.1` は正本SHA-256と一致し、server change noticeは現行full template `2026-09-04.1` を使用。VPS管理の進行中作業ツリーにあるproduction baselineは `f4259490f78f5b358a6eec938e86281148866394` で、ユーザー訂正と分類PUT引き継ぎ文書も同commitを示す。一方、アプリ共通指示が指す保存先のproduction baselineファイルは `f26c211` のままであり、正本間の差異をVPS管理側で解消する必要がある。baselineからsourceまでの全commitは上記2件で、`be9f60a` は旧notice 004の文書更新のみ、`cce6a9c` は今回のコード2件・runtime contract・合成試験の変更。配布一覧は `coop_parser.py`、`jev_classifier.py`、`fetch_coop_mail.py`、`coop_api_server.py`、`requirements.txt` で、今回の変更コード2件を含む。`.env`、`data/`、一時file、notice、runtime contract、testは配布一覧にない。依存versionと共通deploy経路は変更なし。code commit前に実remote `main` は `be9f60a` でlocal HEADと一致した。

未確認・該当なしの理由: source codeはcommit済み。noticeのcommitと両commitのpush、push後の実remote一致、VPS管理read-only preflightは提出直前に確認する。VPS稼働確認・分類overrideを含む実backup確認・隔離復元確認はVPS管理側で未完了。空欄商品名は現行APIと同じく空文字keyとして保存され得るが、今回の保存方式で新たな特別扱いはしない。JSON形式は不変で旧clientは競合時503を非2xxとして扱う。並行PUT、失敗後の旧file保持・cleanup、例外後のlock解放は合成データで確認した。失敗後の明示的な再試行試験は未実施。data rollbackは確認済backupの隔離復元後にのみ検討し、artifact rollbackと分離する。

## 未解決事項

- VPS管理backup scriptの事前読取検査とtar双方に `.category_overrides_tmp/` と `.jev_cache_tmp/` を厳密除外し、稼働確認すること。
- VPS管理の進行中作業ツリーと、アプリ共通指示が指す保存先のproduction baseline記録が異なる。稼働commit `f425949` の正本記録をVPS管理側で確定・同期すること。
- 分類overrideを含む稼働backupの確認と隔離復元確認が未了。
- backup実行userからのmode/group読取はVPS上で未確認。
- テスト結果とsource/release commitsは上記のとおり。実remoteは変更前の `be9f60a` まで照合済みで、今回のpush後の一致は未確認。
- meal-planner-app端末配信は保留。

## 希望時期

VPS管理レビュー後に別途調整。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- ユーザーへの案内: 未実施
- VPS管理チャットへ渡すローカル絶対path: C:\work\PRG\HomeTools\meal-planner\api\coop-api\ops\server-change-notices\20260930-COOPAPI-005-summary.md

## Approval

- app owner: 実装提出待ち
- VPS management review: 未実施
- production approval: 未実施
- related task_id: 20260930-009
