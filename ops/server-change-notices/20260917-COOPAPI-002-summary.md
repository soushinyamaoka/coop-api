# Server Change Notice

record_type: server_change

template_type: full

policy_bundle_version: 2026-09-04.1

notice_id: 20260917-COOPAPI-002

app: coop-api

source_branch: main

source_commit: 未確定（working tree。作業開始時HEADは `84494fe963c7bca963de1bbfc73834fec79ba308`）

production_baseline_commit: `f26c2119ac5b3677916e5e4afe02242565a6da4f`

release_commits: baseline以降の既存commitは `74e76dd`、`84494fe`。本taskのアプリ変更と本noticeは未commit

impact_level: L3

status: draft

created_by: Codex

production_change: required

vps_management_handoff: required

deployment_status: not_started

## 変更概要

商品分類の部分一致競合を修正する。`水菜`と`カレールー`を通常食材として扱い、`牛肉のおかずセット`を調理キットとして扱う。献立作成では`カレールー`をレシピ不要・1回使い切りの食材と判定しない。

## 変更理由

短い一般語の部分一致と汎用セット判定の優先順位により、表示分類および献立在庫計算が意図しない結果になっていたため。

## server_impact判定

server_impact: approval_required

判定理由: APIが返す分類と献立計算の意味が変わり、次回の定期取得後には永続JSON内の自動分類値も変わり得る。production配置・再起動とdata rollback条件の確認を要するためL3とした。

## 現在と変更後

| 項目 | 現在 | 変更後 |
|---|---|---|
| `水菜`の自動分類 | そのまま | 食材 |
| `カレールー`の自動分類 | 調理キット | 食材 |
| `カレールー`の献立判定 | レシピ不要・1回使い切り | 通常食材 |
| `牛肉のおかずセット`の自動分類 | 食材 | 調理キット |
| `カレーパン`の自動分類 | 調理キット | そのまま |
| `クンパッポンカリーキット` | 調理キット | 調理キット（不変） |

## 影響対象

- service/container: coop-api。service定義・runtime user・起動commandは変更なし
- URL/port/health: URL、port、bind、health contract、Bearer認証は変更なし
- cron/timer/worker: scheduleとworker構成は変更なし。既存の定期メール取得が次回保存する分類値には影響し得る
- dependency: 追加・更新なし。内部API転送先と外部依存は変更なし
- data/DB/volume: schema/pathは変更なし。`coop_latest.json`と`coop_orders.json`へ今後保存される自動分類値の意味が変わり得る。`category_overrides.json`は変更しない
- log/monitoring: 変更なし

## production変更

- 必要性: あり
- 想定作業: VPS管理側レビューと個別承認後に、確定commitの変更対象アプリファイルを配置し、既存の正規手順でserviceを反映・検証する
- downtime: service再起動が必要な場合は短時間の利用不能があり得る
- maintenance window: VPS管理側で判断

`production_change: required`のため、`deployment_status: not_started`のままVPS管理側へ引き継ぐ。

## 利用者への影響

- user_maintenance_impact: possible
- 対象利用者・機能: COOP商品のカテゴリ表示と献立作成結果が意図した分類へ変わる。deploy時に短時間のAPI停止があり得る
- 通知方法: VPS管理側レビューで要否を決定する

## env・secret contract

- 変更: なし
- 変数名・secret種類のみ: 変更なし
- provisioning/rotation: 不要

secret値は記載しない。

## Data・migration・backup

- schema/format変更: なし。既存JSON schemaは維持するが、自動分類値の意味が変わる
- migration: なし。既存保存済みJSONを一括再分類しない
- **既存データとの不整合について（2026-09-17、app owner判断）**: deploy時点より前の注文（`coop_orders.json`）は旧ロジックの分類値のまま保持され、deploy後の新規注文は新ロジックの分類値になる。同一食材名（例: 水菜）が注文週によって異なるカテゴリで並存する状態が生じるが、**この不整合は許容し、遡及的な再分類（migration）は行わない方針**とする。将来この方針を変更する場合は、別途migration taskとして設計する。
- backup対象: deploy前のアプリファイルのみ。上記方針により、既存の注文JSONの事前backupは本変更のためには必須としない
- restore確認: 旧codeへのrollbackで足りる。分類値の遡及的な復元は不要（上記方針のため）
- backward compatibility: JSONのfieldとカテゴリ語彙は維持する。ユーザー手動overrideを優先する既存仕様は不変

## Deploy・rollback

- deploy前提: source commitの確定・push、VPS管理側レビュー、production個別承認
- deploy手順の変更: なし
- rollback方法: 直前のアプリcodeへ戻す。分類値の遡及的な復元は不要（上記「Data・migration・backup」の方針のため）
- rollback不能条件: なし。分類値の不整合は許容する方針のため、rollback不能とみなす条件はない

## Health・テスト

- health contract変更: なし
- 実施テスト: `.venv-codex`のPython 3.12.14で全pytest、UTF-8標準出力指定の`coop_parser.py`、対象関数の分離実行、静的diffレビュー
- 結果: pytest 32件pass。4件の新分類と既存のキット分類を確認。`_is_ready_meal("カレールー")`は`False`
- 未実施テストと理由: production接続・実データ・Gmail・deploy検証はtaskで禁止されているため未実施

## Log・監視

- log量/形式/保存先変更: なし
- 新しいalert条件: なし
- secret/個人情報対策: 実メール・実データ・secret値をテストやnoticeへ記録しない

## 提出前セルフチェック

正本: VPS管理repositoryの `docs/templates/server_change_notice_pre_submission_checklist.md`

- [x] production baselineとbaseline以降のcommit・build入力差分を確認した
- [ ] source commitとnoticeをremoteの対象branchへpushした
- [x] data更新の意味、再実行、rollback条件を確認した
- [x] code rollbackとdata rollbackを分けた
- [x] job/log/retention、runtime/dependency、client配信の該当有無を確認した
- [x] app owner、VPS review、production承認を分離した
- [ ] secret非混入とtracked working tree cleanを確認した

未確認・該当なしの理由: task規約とプロジェクト規約によりcommit・pushしていないためsource commitは未確定で、working treeには本taskの差分が残る。client配信は該当なし。production接続は行っていない。

## 未解決事項

- source commitの確定とremote push
- VPS管理側によるrelease全体レビュー、data backup・rollback方針、deploy時の停止影響の確定
- production個別承認とdeploy後検証

## 希望時期

VPS管理側レビューとproduction個別承認後。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- ユーザーへの案内: 本無人実行経路では未実施
- VPS管理チャットへ渡すpath: `ops/server-change-notices/20260917-COOPAPI-002-summary.md`（公開repositoryへローカル絶対pathを記録しない方針のためrepository相対path）

## Approval

- app owner: task `20260917-002` のローカル実装を承認済み
- VPS management review: 未実施
- production approval: 未承認
- related task_id: 20260917-002
