# Server Change Notice

record_type: server_change

template_type: full

policy_bundle_version: 2026-09-04.1

notice_id: 20260927-COOPAPI-004

app: coop-api

source_branch: main

source_commit: f4259490f78f5b358a6eec938e86281148866394

production_baseline_commit: f26c2119ac5b3677916e5e4afe02242565a6da4f

release_commits: 74e76dd77bfaf801136b4bd23cf84f3bbae7df3f, 84494fe963c7bca963de1bbfc73834fec79ba308, ba76f9744aa528d8e23421c1d3612d454223fa00, aa164021830355807c9592d41d9b9bfcdd3c641b, 9cda9fe9c0b57f1b400f94cfc2962f337cc1dd7b, 00890f1e761f0c38f89bf511200e93816e7c51b0, e5efee4d995c60154fcdfb680565bbf75c8aab10, 116fbf5e993bfd6b1e9292036d17e7766d6cda3d, 03f65b2aa421a76d31833a25aa871a205d1e1896, 8543b416058ea6d9ee4feb3032d10156f707b191, aa5ef17d2edd33b39f7ab55eb43f82e60f8009c6, 9d24a3be0db309402411bbd2899190825a428488, 817b3ffc103aaf2e4a2145863ba30419b78a3d2b, fc4003876d09d9794077025646842a4d4754c893, cff88d5bd825100eae09d2f2032ebfd255b8f861, 822d14143bb0f8fec7e584f992a3588aceee0621, f4259490f78f5b358a6eec938e86281148866394

前半（`74e76dd`〜`fc40038`）はnotice 20260927-COOPAPI-003（accepted・production未反映）の内容と同一。本noticeはその上に積む変更（`cff88d5`、`f425949`）と、本notice文書のcommitを扱う。

impact_level: L3

status: ready_for_review

created_by: Claude

production_change: required

vps_management_handoff: required

deployment_status: not_started

## 変更概要

notice `20260927-COOPAPI-003`（VPS管理レビューでaccepted済み・production未反映）のB04で、
Jev有効化の前提条件とされた「取込処理の直列化」を実装した。cron（`main()`）と手動
`POST /api/coop/fetch`、および同一プロセス内の別スレッドが、`data/.coop_import.lock`を使った
非ブロッキングOSロックで直列化される。あわせて、Jevのproduction有効化
（`CATEGORY_CLASSIFIER=jev`への切替、`TYPESAFE_API_KEY`のprovisioning）を本noticeで申請する。

本noticeは003の反映を前提とする。003がproduction未反映のままでは、本notice単体の反映はできない
（003のコード変更に本noticeの変更が依存するため）。003と同時に反映するか、003を先に反映してから
本noticeを反映するかは、VPS管理側の実施計画で決定してよい。

## 変更理由

Jevによる商品分類は、B04で指摘されたcacheの同時書き込みリスクが解消されるまで、production
有効化を見送っていた。直列化の実装・テストが完了したため、Jev有効化そのものを申請する。

## server_impact判定

server_impact: approval_required

判定理由: (a) 取込処理へロックを追加し、cronと手動fetchの間、スレッド間で直列化する。ロック取得に
失敗した場合の応答（cronはスキップ・success、`/fetch`はHTTP 200 `status: busy`）が新設される。
(b) `CATEGORY_CLASSIFIER=jev`への切替と`TYPESAFE_API_KEY`のprovisioningにより、production から
`api.typesafe.ai`への新規外部送信が発生し、注文JSONへ保存される分類値の由来が変わり得る。
いずれもL3相当のVPS管理レビューとproduction個別承認を要する。

## 現在と変更後

| 項目 | 現在（003 accepted・未反映時点） | 変更後 |
|---|---|---|
| 取込の同時実行 | cron・手動fetch・スレッド間の排他制御なし | `data/.coop_import.lock`で直列化。取得失敗時、cronはスキップ、`/fetch`はbusy応答 |
| `CATEGORY_CLASSIFIER` | 既定`keyword`（003でも無効のまま） | `jev`へ切替。確信度0.8未満・エラー・タイムアウトはkeywordへfallback |
| `TYPESAFE_API_KEY` | 未provisioning | provisioningする（値はこのnoticeに記録しない） |
| 外部送信 | なし | 未キャッシュの商品名ごとに`api.typesafe.ai`へHTTPS送信 |
| `/api/coop/fetch`応答 | `{"status": "success"\|"no_data", ...}` | 上記に加え`{"status": "busy", "orders": 0}`を追加（既存の200判定ロジックと両立） |

## 影響対象

- service/container: coop-api serviceおよび定期workerのアプリコード。service定義変更なし。新規モジュール追加なし（ロックは`fetch_coop_mail.py`内に実装、配布一覧`deploy-files.txt`の変更不要）。
- URL/port/health: 変更なし。
- cron/timer/worker: schedule変更なし。ロック取得に失敗した回はスキップされ`job_end`は`success`。
- dependency: 新規パッケージ依存なし（`fcntl`/`msvcrt`は標準ライブラリ）。Jev有効化により外部依存`api.typesafe.ai`が実際に使われ始める（003で宣言済み、production未使用だった）。
- data/DB/volume: `data/.coop_import.lock`を新規作成（`regenerable: true`、backup不要）。`CATEGORY_CLASSIFIER=jev`が有効になると`data/category_jev_cache.json`が実際に生成され始める（003で宣言済み）。
- log/monitoring: `import_lock`（`result: acquired|busy`）、`import_skipped(reason=locked)`を追加。ロックファイルの内容・PIDはログに出さない。

## production変更

- 必要性: あり
- 想定作業: 003・本noticeのVPS管理レビュー後、承認されたartifactを配置し、`TYPESAFE_API_KEY`のprovisioning、`CATEGORY_CLASSIFIER=jev`への設定変更、service反映、health・取込・busy応答の検証を行う。今回の作業では実施していない。
- downtime: service再起動時の短時間停止の可能性。実際の時間はVPS管理側のdeploy計画で決定。
- maintenance window: VPS管理側reviewで要否を決定。

## 利用者への影響

- user_maintenance_impact: possible
- 対象利用者・機能: 商品カテゴリ、献立候補、定期/手動メール取込結果。
- busy応答時のアプリ表示（初回VPS管理レビューの指摘に対する判断）: **アプリ側で対応する。** `meal-planner-app`（commit `20e7c90`、local commit・未push）で、`status: "busy"`を`no_data`と同様に扱い、既存データを表示したまま「別の取込処理を実行中です。しばらくしてから再度お試しください。」と表示するよう修正した。このアプリ修正は、Jev有効化と同じリリース（EAS Update）で配布する。
- 移行期間の受容判断: 修正前のアプリ（配布済みの版）は`busy`を成功系として扱い、取込が行われていないのに既存データを「取得後のデータ」として表示する。本noticeのserver反映がアプリ配布より先になった場合、その間は旧挙動となる。busyは手動取得と定期取込（07:00/20:00 JST）などが実際に重なった場合に限られ、表示されるのは直前に保存済みの正しいデータ（最新の注文を取り込めていないだけ）で、誤ったデータやエラーにはならないため、移行期間中の旧挙動は受容する。server反映とアプリ配布はなるべく同時期に行う。
- 通知方法: downtimeの有無をVPS管理側がdeploy計画で判断。

## env・secret contract

- 変更: あり
- 変数名・secret種類のみ: `CATEGORY_CLASSIFIER`（値を`jev`へ変更）、`TYPESAFE_API_KEY`（TypeSafeのAPI credential、新規provisioning）
- provisioning/rotation: VPS管理側が`.env`（`root:coop-api 0640`）へprovisioningする。値はこのnoticeに記録しない。ローテーション方針は未定（初回provisioningのため）。

## Data・migration・backup

- schema/format変更: なし（分類関連のfieldは003で追加済み）。本noticeの新規ファイルは`data/.coop_import.lock`のみ（中身は使わない）。
- migration: 不要。
- backup対象: `data/.coop_import.lock`はbackup不要（`regenerable: true`）。003で宣言済みの`category_jev_cache.json`が実際に生成され始める（同じくbackup不要）。
- restore確認: 本noticeによる追加のrestore手順はない。003のrestore手順（`coop_orders.json`/`coop_latest.json`/`category_overrides.json`/`custom_meals.json`）がそのまま適用される。
- backward compatibility: `/api/coop/fetch`のレスポンスに新しい`status: "busy"`値が加わる（HTTP 200）。修正後のアプリは`busy`を専用に扱う。修正前のアプリは未知の`status`値でもクラッシュせず成功系として扱う（詳細と受容判断は「利用者への影響」参照）。

## Deploy・rollback

- deploy前提: 003・本noticeのVPS管理レビュー受理、`TYPESAFE_API_KEY`のprovisioning計画確定、必要なproduction承認。
- deploy手順の変更: 003と同じ自動経路（`deploy-coop-api.bat` → `deploy.bat` → リモート`/opt/apps/deploy.sh coop-api`、`pip install`込み）。追加の手順はない。
- rollback方法: `CATEGORY_CLASSIFIER=keyword`へ戻す、または`TYPESAFE_API_KEY`を削除してservice再起動する。ロック機構自体はkeyword運用でも動作し続けるため、ロック導入部分のみを戻すことは想定していない（問題が出た場合は003の反映前まで artifact rollbackする）。data側のrollbackは003のrestore手順に従う。
- rollback不能条件: 003と同様。分類値を含む注文JSONが上書きされ、backupがない場合。

## Jev cacheの同時実行（COOPAPI-003-B04への対応）

- `data/.coop_import.lock`により、cron・手動`/fetch`・同一プロセス内の別スレッドの間で、IMAP取得・同一注文判定・分類（Jev cacheのread-modify-writeを含む）・注文JSON保存の全体を直列化した。
- ロックを取得できないとき、cronは`import_skipped(reason=locked)`を記録し取込をスキップ（`job_end: success`）、`/fetch`は待機せずHTTP 200で`{"status": "busy", "message": "...", "orders": 0}`を返す。
- プロセスが異常終了した場合、OSがロックを解放するため、ロックファイルが残っていても次回の取得を妨げない（`subprocess`を使った別プロセスからの検証、および強制終了後の解放をテストで確認済み）。
- 以上により、B04で指摘された前提条件を満たしたと考えている。VPS管理側での実機確認は別途必要。
- ジョブログの整合（初回VPS管理レビューの指摘で修正、commit `f425949`）: 当初の実装では、手動`/fetch`がロック競合した場合に`ImportBusyError`が外側の例外処理にも捕捉され、`job_failed`と`job_end(status=success)`が併記されていた。busyは内部failureではないため、`job_failed`を出さず、`import_lock(result=busy)`・`import_skipped(reason=locked)`・`job_end(status=success)`のみを記録するよう修正した。cron経路（busy時は例外を投げずに終了）は元から`job_failed`を出していない。

## Health・テスト

- health contract変更: なし。
- 実施テスト: `.venv-codex`でpytest 70件pass（既存64件＋新規6件: 別プロセスからのロック取得失敗と解放、別スレッドからのロック取得失敗、プロセス異常終了後のロック解放、cronのロックスキップとログ、`/fetch`のbusy応答、手動取込のbusy時に`job_failed`が出ず`job_end(success)`が1件だけ出ること）。アプリ側は`npx tsc --noEmit`がpass。`PYTHONIOENCODING=utf-8 python coop_parser.py` exit 0。`git diff --check` pass。モックIMAP・モック分類器・一時ディレクトリを使用、実メール・実API・実データなし。
- 未実施テストと理由: production/VPS接続、実Jev API接続、実際のcron実行タイミングでの重複発生検証はtaskで禁止・環境上未実施。

## Log・監視

- log量/形式/保存先変更: `import_lock`（`result: acquired|busy`）と`import_skipped(reason=locked)`を既存の1行JSON構造化ログへ追加。busy時はcron・手動とも`job_failed`を出さず、`job_end(status=success)`のみ。
- 新しいalert条件: なし（VPS管理側で`busy`頻発の監視要否を判断してよい）。
- secret/個人情報対策: ロックファイルの内容・PID、TypeSafeのkey・応答本文・商品名はログへ出さない（003から継続）。

## 提出前セルフチェック

- production baseline: 確認済み。coop-api deployed source `f26c2119ac5b3677916e5e4afe02242565a6da4f`（003・本noticeともproduction未反映のため不変）。
- source commitとbaseline以降の全release commit/build差分: 上記`release_commits`に記載。前半は003と同一、`cff88d5`（直列化）と`f425949`（busy時のジョブログ修正）が本noticeの実装commit。
- data transaction、同時実行、途中失敗、再実行: `data/.coop_import.lock`による直列化を実装し、別プロセス・別スレッド・異常終了後の解放をテストで確認した。
- image rollbackとdata rollback: 003のrestore手順を流用。本notice固有の追加rollback手順はない。
- job/log/retention、runtime/dependency、client連携: schedule変更なし。ログにイベントを追加。`ops/runtime-contract.yaml`の`concurrency_control`/`concurrency_note`を実装に合わせて更新済み。
- owner/review/production承認/client配信: app実装とproduction反映の承認を分離。今回deployなし。
- notice status: 本ファイル作成時点で`ready_for_review`とする。source commitはローカルのみで、push後にremote上のlocal/cachedと一致することを確認する（003で確立した手順と同じ。VPS管理側のレビューはpush後に行う）。

未確認・該当なしの理由: 実VPSでの同時実行実機検証は、production接続を伴うため本対応の範囲外（VPS管理側の別作業）。

## 未解決事項

- VPS管理側で、`TYPESAFE_API_KEY`のprovisioning方法・保管・ローテーション方針を確定する。
- アプリ修正（`meal-planner-app` commit `20e7c90`）のEAS Update配布は、本noticeのserver反映と同時期に行う（配布はアプリ側の作業で、VPS管理側の作業ではない）。
- 003・本noticeの反映順序・タイミングをVPS管理側の実施計画で確定する。

## 希望時期

VPS管理review後に決定。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- ユーザーへの案内: 表示済み（定型文どおり）。

## Approval

- app owner: task 20260927-006で取込直列化の実装承認済み（Codexが実装・commit、Claudeが検証。Codex自身のresult記述に誤りがあったためClaudeが訂正記録した）。本notice作成はユーザー依頼（Jev本番有効化を最初から意図）により対話的Claude Codeセッションで実施。
- VPS management review: 初回`blocked`（ジョブログの矛盾、busy時の旧データ表示の判断未記録）。本更新で両点に対応し、再審査依頼として提出。003は`accepted`済み・production未反映。
- production approval: 未取得
- related task_id: 20260927-001, 20260927-002, 20260927-003, 20260927-006
