# Server Change Notice

record_type: server_change
template_type: full
policy_bundle_version: 2026-09-04.1
notice_id: 20261118-COOPAPI-008
app: coop-api
source_branch: main
source_commit: 5b27f013aa8e962c545533cb74a9339b3880972b
production_baseline_commit: c94f8a0c3a310727779610d74f01b8a80d228913
release_commits: a09b441e733b58486b516f4dadbee7f6282fb73b, 5146ab10ac5de9ba26b84e5097b753763fb4f91d, 5b27f013aa8e962c545533cb74a9339b3880972b
impact_level: L2
status: ready_for_review
created_by: Claude
production_change: required
vps_management_handoff: required
deployment_status: not_started

## 再々提出（2026-11-27）: 再レビューの指摘への対応

再レビューで、保存失敗の502は確認済みとされ、残る指摘（排他・履歴の再試行・同日旧版・IMAPの初期化）と状態fileの権限・文書を `5b27f01` で修正した。配布対象のうち今回変わったのは `fetch_coop_mail.py` だけ（`coop_api_server.py` は `5146ab1` から不変）。

| 指摘 | `5146ab1` の問題 | 対応（`5b27f01`） |
|---|---|---|
| 排他 | 想定外の例外の場合、取込状況（`coop_fetch_status.json`）の読み書き（連続失敗回数の加算）を、取込の lock を外した後で行っていた。別の取込（cron・手動）と同時に書くと回数が失われ得た | 取得・保存・履歴の再試行・取込状況の記録を、すべて同じ lock（`data/.coop_import.lock`）の中で行う。lock の外では記録しない |
| 履歴の再試行 | 注文履歴の保存だけ失敗して最新注文が保存されると、次の取込ではその注文が「最新注文と同じ」になり、履歴に入る機会が無かった | 新しい注文が無かった取込（cronの変化なし・手動の `no_data`）で、最新注文が注文履歴に無ければ足す（分類は行わず、保存済みの最新注文をそのまま足す。`history_retry` イベント）。足せなければ失敗（`save`）として記録し、手動は502 |
| 同日旧版 | 同じ週の別の版を「最新注文と同じメールより前に届いたか」だけで判定していた。変更前の版が最新注文の後に再送された場合や、最新注文のメールが検索結果に無い場合に、変更前の版で最新注文を戻し得た | 同じ週の別の版は、メールの日時（Date）が保存済みの最新注文（`email_date`）より後のときだけ取り込む。どちらかの日時が読めない場合だけ、従来の届いた順で判定する |
| IMAPの初期化 | `IMAP4_SSL` の接続・最初のあいさつで `IMAP4.error` が出ると、ログイン前なので `auth` になっていた。接続・応答にタイムアウトが無く、待ち続け得た | `auth` は login 段階の `IMAP4.error`（`abort` を除く）だけ。接続・あいさつ・ログイン後の失敗は `connection`（ログの `operation` に段階 `connect`/`login`/`session`）。`IMAP4_SSL(..., timeout=60)` で接続・応答を60秒で打ち切る |
| 状態fileの権限 | `coop_fetch_status.json` の mode がプロセスの umask に依存していた | `coop_orders.json`・`coop_latest.json`・`coop_fetch_status.json` を同じ置き換え処理で書く。既存fileは mode を引き継ぎ、新しく作るときは umask に関係なく 0640。所有者・グループは書いたプロセス（本番では `coop-api`） |
| 文書 | `CLAUDE.md` の「最初に見つかった1通のみ処理」など、`5146ab1` の動きと合わない記述が残っていた | `API_SPEC.md`・`CLAUDE.md`・本通知を現在の動きに合わせた |

## 再提出（2026-11-27）: 差し戻し3点への対応

VPS管理レビューで差し戻された3点を `5146ab1` で修正し、回帰テストを追加した。

| 差し戻しの点 | 初回提出（`a09b441`）の問題 | 対応（`5146ab1`） |
|---|---|---|
| 保存失敗時の応答と状態更新 | 注文履歴を保存できなくても、取得した注文があれば HTTP 200 `success` を返していた。最新注文（`coop_latest.json`）の書き込みが失敗すると例外のまま HTTP 500 になり、状況は `internal` になっていた。両fileとも直接上書きで、書き込み途中の失敗でfileが壊れ得た | 保存に失敗したら、取得できた注文があっても HTTP 502 `error_kind=save` を返し、`coop_fetch_status.json` に `failure`/`save` を記録する。想定外の例外も HTTP 502 `internal`（状況にも `internal`）。`coop_orders.json`・`coop_latest.json` は同じdirectoryの一時file（`.<name>.<ランダム>.tmp`）に書いてから `os.replace` で置き換え、既存fileの mode を引き継ぐ。失敗時は元のfileが残り、一時fileは消す。`results_save_failed` に `target=coop_latest`・`reason=latest_write_failed` を追加 |
| 古い再送メールの扱い | cronは、新しい順に見た最初の注文確認メールが最新注文と同じ（`unchanged`）か古い（`older_than_latest`）と、そこで取込を終えていた。新しい注文Bの後に古い注文Aが再送されると、Bを見落とした。手動取得は古い注文を取り込み、`success` を返していた | 最大5通の候補を新しく届いた順に見て、最新注文より前の注文日・最新注文と同じメール・最新注文より前に届いた同じ週の別の版（変更前の注文）は分類せずに飛ばし、次の候補を見る。最新注文より後に届いた同じ週の版（注文の変更）と、より新しい注文日の注文を取り込む。手動取得は、新しい注文が無ければ最新注文を取り込み直し、古い注文しか無ければ `no_data`。古い注文の履歴追加は過去分取込（`--backfill-days`）で行う |
| IMAP切断の分類 | `imaplib.IMAP4.abort`（切断）は `IMAP4.error` の子クラスのため、ログイン後の切断も `auth` になっていた。メール取得中の切断・`OSError` は1通ごとの失敗として次の候補を試し続け、`message` になっていた | `auth` はログインが終わる前の `IMAP4.error` だけ。`abort` とログイン後の `IMAP4.error` は `connection`。メール取得中の `abort`・`OSError` はその場で打ち切り `connection`（過去分取込は、切断までに取得できた分を保存して失敗とする） |

配布対象は初回と同じ2file（`coop_api_server.py`・`fetch_coop_mail.py`）。依存・認証・bind・cron・env・lock・終了コードは不変。

## 変更概要

COOPメールの取得失敗を、「注文確認メールが無かった」と区別して返す。あわせて、通常取込の直近の成否を記録してAPIで返し、古い注文メールで最新注文が戻らないようにする。

1. `POST /api/coop/fetch`: 取得そのものに失敗した場合（Gmailにログインできない・接続できない・切断された等）と、保存に失敗した場合は、HTTP 502 で `{"status":"error","error_kind":…,"detail":…}` を返す。`no_data` は、取得に成功して注文確認メールが無かったときだけ返す。
2. 通常取込（cronの定期取込・`POST /api/coop/fetch`）のたびに、成否を `data/coop_fetch_status.json` に記録する（新規file）。`GET /api/coop/ingredients` の応答に `fetch_status` を追加する（記録前は `null`）。
3. 保存済みの最新注文（`coop_latest.json`）より前の注文日の注文は、最新注文にしない。通常取込（cron・手動）は、古い注文メールを分類（Jev）・保存せずに飛ばし、その後ろの新しい注文を探す（再提出で変更。上表）。
4. `coop_orders.json`・`coop_latest.json` の書き込みを、一時fileからの置き換えにする（既存の mode を引き継ぐ）。

## 変更理由

2026-10-10 20:00 から 11-18 07:00 まで、Gmailのアプリパスワード失効でIMAP認証が78回失敗し、注文の取り込みが止まっていた（VPS管理側の確認による）。しかし `POST /api/coop/fetch` は失敗時も `no_data`（「注文確認メールが見つかりませんでした」）を返し、cronの失敗はログにしか残らないため、利用者が気づくまで約6週間かかった。アプリ（別途配信）が、`fetch_status` と502を使って、失敗をCOOPタブに表示する。

また、復旧時に古い注文メール（10/14・10/21お届け分）を受け取り直したため、現行の取込では次の定期取込で最新注文が古い注文に戻るおそれがあった（今回は過去分取込で回避した）。3はその再発防止。

## server_impact判定

server_impact: approval_required
判定理由: APIの応答に失敗時の新しいHTTPステータス（502）と項目（`fetch_status`）を追加し、`data/` に新しいfileを作るため。既存の項目・認証・bind・cron定義は不変。保存済みデータの意味・形式は変えない。

## 現在と変更後

| 項目 | 現在（baseline `c94f8a0`） | 変更後 |
|---|---|---|
| `POST /api/coop/fetch` の取得失敗 | HTTP 200 `no_data` | HTTP 502 `status=error`、`error_kind`、`detail`（利用者向けの文） |
| `POST /api/coop/fetch` の保存失敗 | HTTP 200 `success`（最新注文の書き込み失敗は500） | HTTP 502 `error_kind=save` |
| `POST /api/coop/fetch` のメールなし | HTTP 200 `no_data` | 同左 |
| `POST /api/coop/fetch` の成功・busy | HTTP 200 `success` / `busy` | 同左 |
| `GET /api/coop/ingredients` | 最新注文の食材 | `fetch_status`（下表）を追加。既存項目は不変 |
| 取込の成否の記録 | ログ（`job_end`）のみ | ログに加えて `data/coop_fetch_status.json` |
| 最新注文より前の注文 | 最新注文を上書きする | 取り込まずに次の候補を見る（`email_candidate_skipped`、`reason=older_than_latest`）。最新注文は変わらない |
| 最新注文と同じメール（cron） | 取込を終える | 次の候補を見る（`reason=same_as_latest`）。同じ週の変更前の版（メールの日時が最新注文以前）は `reason=superseded` で飛ばす |
| `coop_orders.json`・`coop_latest.json` の書き込み | 直接上書き | 一時fileから `os.replace`（既存の mode を引き継ぐ） |
| 履歴の保存だけ失敗した注文 | 次の取込で履歴に入らない | 新しい注文が無かった取込で履歴に足す |
| Gmail IMAP の接続・応答 | タイムアウトなし | 60秒で打ち切り `connection` |
| 過去分取込（`--backfill-days`） | 履歴のみ | 同左。取込状況は記録しない |

`fetch_status` の項目: `last_attempt_at`（日本時間）、`last_result`（`success`/`failure`。メールが無かっただけなら `success`）、`last_error_kind`（`auth`/`connection`/`message`/`configuration`/`save`/`internal`、成功時は `null`）、`last_success_at`、`consecutive_failures`。秘密値・メール本文・件名・商品名・金額は含めない。

## 影響対象

- service/container: `coop-api.service`。設定変更なし。要restart
- URL/port/health: 変更なし。認証・bind・公開面は不変。`POST /api/coop/fetch` が失敗時に502を返す（HTTPS入口・proxyで502を書き換えないこと）
- cron/timer/worker: 時刻・定義・lock file・終了コードは変更なし。cronの取込も成否を `coop_fetch_status.json` に書く（lock の中で書く）。IMAPが応答しない場合も60秒で終わる
- dependency: 変更なし（`requirements.txt` 不変。標準ライブラリの `dataclasses` を追加で使うのみ）
- data/DB/volume: `coop_orders.json`・`coop_latest.json`・**新しいfile `coop_fetch_status.json`**（数百バイト、取込のたびに上書き）は、同じdirectoryの一時file（`.<name>.<ランダム>.tmp`）に書いてから `os.replace` で置き換える。所有者・グループは書いたプロセス（API・cronとも `coop-api`）。mode は既存fileを引き継ぎ、新しく作るときは umask に関係なく 0640。既存fileの形式は不変（履歴の再試行で足すのは、保存済みの最新注文と同じ形式の1件）
- log/monitoring: 既存の `email_candidate_skipped` に `reason=same_as_latest`/`older_than_latest`/`superseded`、`import_skipped` に `reason=no_newer_order`、`results_save_skipped` に `target=coop_latest`・`reason=older_than_latest`、`results_save_failed` に `target=coop_fetch_status`／`target=coop_latest`（`reason=latest_write_failed`）が加わる。新しいイベント `history_retry`（`target=coop_orders`・`order_count`。履歴に足し直したときだけ）。`external_call_failed` に段階 `operation=connect`/`login`/`session` が付くことがある。cronの `unchanged` は候補を見終えた後に1回出す（従来の `item_count` は出さない）。1回の取込で取得するメールは、従来の最大1〜5通から最大5通になり得る（同じ注文が続く通常時）
- 配布対象: `coop_api_server.py`、`fetch_coop_mail.py`（`deploy-files.txt` の5file中、baseline からの変更は2file。`5b27f01` で変わったのは `fetch_coop_mail.py` のみ）。`coop_parser.py`・`jev_classifier.py`・`requirements.txt`・`.env`・`data/`・test・文書・通知書は配布対象外・不変

## production変更

- 必要性: あり
- 想定作業: VPS管理側の別承認後に、2fileを既存の共通deploy経路で反映し、service再起動と動作確認を行う
- downtime: brief-restart想定
- maintenance window: VPS管理側で決定。cron（07:00・20:00 JST）の時刻を避ける
- 反映後の確認（案）:
  1. `POST /api/coop/fetch` が HTTP 200（`success` または `no_data`）を返し、`data/coop_fetch_status.json` が作られ、`last_result=success` であること。所有者・グループ・modeが `coop-api:coop-api`・0640 であること（新規fileは umask に関係なく 0640 で作る）。異なる場合は報告をお願いしたい（`.env` の `GMAIL_APP_PASSWORD` を変えて502を確かめる試験は、本番では行わない）
  2. `GET /api/coop/ingredients` に `fetch_status` が含まれること
  3. 次のcron取込の後も `last_result=success` であること
  4. 1の後、`coop_orders.json`・`coop_latest.json` の所有者・グループ・mode が反映前と変わらないこと、`data/` に `.*.tmp` が残っていないこと

## 利用者への影響

- user_maintenance_impact: none
- 対象利用者・機能: 家族2名のアプリ利用。restart中の数秒だけCOOP APIが使えない可能性がある
- 見え方の変化: 取得失敗時、**アプリを更新していない端末**は「メール取得に失敗しました: サーバーエラー (502)」と表示する（現在の「見つかりませんでした」より正確）。更新済みのアプリ（`367447a`、配信は本反映の後）は、失敗の理由と、定期取込が失敗している間の警告をCOOPタブに表示する
- 古い注文メールを受け取り直しても、COOPタブの最新注文は変わらない

## env・secret contract

- 変更: なし
- 変数名・secret種類のみ: 変更なし
- provisioning/rotation: なし。なお、Gmailの認証情報は起動時に1回だけ読むため、`.env` の差し替え後は service の再起動が必要（現行どおり。今回の復旧で確認された点を `CLAUDE.md` に記録した）

## Data・migration・backup

- schema/format変更: 既存fileはなし。新しいfile `coop_fetch_status.json` を追加
- migration: なし。初回の取込まで `fetch_status` は `null`
- backup対象: `coop_fetch_status.json` は、次回の取込で作り直される状況の記録で、backupは不要（`data/` ごとのbackupに含まれても問題ない）。一時file（`.coop_fetch_status.json.*.tmp`）は通常残らないが、書き込み途中の異常終了で残った場合は削除してよい。backupの除外条件に加えるかはVPS管理側で判断
- restore確認: 不要（失っても次の取込で作られる）
- backward compatibility: 既存の項目・endpoint・認証は維持。旧アプリは `fetch_status` を無視し、502は「サーバーエラー」として表示する。新アプリは、`fetch_status` が無い・502を返さない旧サーバーでも従来どおり動く

## Deploy・rollback

- deploy前提: 通知007が反映済みであること（baseline `c94f8a0`。runtime contract に反映済みと記録）
- deploy手順の変更: なし（共通deploy経路・`USE_VENV=yes`）
- rollback方法（artifact）: 旧artifactの2fileを再配置しservice再起動
- rollback時のdata: `coop_fetch_status.json` は旧コードから読まれず、残っても無害（削除してもよい）。既存の `coop_orders.json`・`coop_latest.json` の形式は変えないため、データのrollbackは不要

## Health・テスト

- health contract変更: なし
- 再々提出時のテスト（ローカル、合成データのみ）: Windows `pytest` 143 passed・4 skipped（POSIXの mode 検証）、`python:3.10-slim`（Docker、POSIX）で 147 passed。追加した回帰テスト（`5146ab1` のコードでは8件が失敗することを確認。mode のテストは POSIX のみ）:
  - 取込状況の記録が、成功時も想定外の例外時も lock を持ったまま行われること
  - 履歴の保存だけ失敗した注文が、次の取込（変化なし）で分類なしに履歴へ入り、以後は重複しないこと。再試行も失敗したら `save` で連続失敗に数えること。手動の `no_data` でも再試行すること
  - 同じ週の変更前の版が、最新注文の後に再送されても・単独でも、最新注文を戻さないこと。変更後の版は最新注文のメールが無くても取り込むこと
  - `IMAP4_SSL` に timeout を渡すこと。初期化時の `IMAP4.error`・`abort`・タイムアウト・接続拒否が `connection` になること（ログインの認証失敗は従来どおり `auth`）
  - umask 002 でも新規の3fileが 0640 になり、既存の mode（例: 0600）は引き継ぐこと。一時fileが残らないこと
- 再提出時のテスト（ローカル、合成データのみ）: Windows `pytest` 130 passed・3 skipped（POSIXの権限検証）、`python:3.10-slim`（Docker、POSIX）で 133 passed。追加した回帰テスト（初回提出のコードでは14件が失敗することを確認）:
  - 最新注文Aの後に新しい注文B、その後にAの再送が届いても、Bを取り込む（cron・手動とも）
  - 最新注文Bの後に前の週の注文Aが再送されても、Aは分類・保存しない（cronは何もしない、手動はBを取り込み直す）。古い注文しか無い手動取得は `no_data`
  - 同じ週の注文の変更前の版（最新注文より前に届いたもの）では最新注文を戻さず、後に届いた版は取り込む
  - 注文履歴を保存できない・最新注文を書き込めない場合に、502 `save`・状況 `failure`/`save`、元のfileが残り一時fileが残らないこと。想定外の例外で502 `internal`
  - 書き込み途中の失敗で注文履歴が壊れないこと。置き換え後も既存の mode（0640）が保たれること（POSIX）
  - 検索中・取得中の `abort`、取得中の接続リセット、ログイン後の `IMAP4.error` が `connection`。ログイン中の `abort` も `connection`、ログインの認証失敗は従来どおり `auth`。過去分取込は、切断までの分を保存して失敗
- 初回提出時の実施テスト: `pytest`（113 passed、POSIX所有検証2 skipped）。追加した主なテスト:
  - Gmailのログイン失敗で、`POST /api/coop/fetch` が502・`error_kind=auth` を返し、`coop_fetch_status.json` に失敗が記録されること
  - 接続失敗・タイムアウト・その他の例外の `error_kind`（`connection`・`internal`）
  - 注文確認メールが無い場合は従来どおり200 `no_data`、記録は `success`
  - 連続失敗の回数が増え、成功で0に戻り、`last_success_at` が更新されること
  - 過去分取込とbusyでは記録しないこと。記録の書き込みに失敗しても取込の結果が変わらず、一時fileが残らないこと
  - `GET /api/coop/ingredients` の `fetch_status`（記録前は `null`）
  - 最新注文より前の注文は最新注文にせず履歴にだけ追加し、同じ週の注文の変更メールは最新注文を更新すること。cronでは古い注文メールを分類せずに終えること
  - 既存テスト（成否の判定・終了コード・lock・重複除外・過去分取込）は変更なしで成功（busyのテストは、呼び出す関数名の変更に合わせて差し替え先だけ変えた）
- 結果: ローカル検証成功。POSIXでの `os.replace`・mode（新規0640・既存の引き継ぎ）は Docker（root）で確認。`coop-api` ユーザーでの所有者・グループは未確認（本番で確認をお願いしたい）
- 未実施テストと理由:
  - production相当のファイル権限・実際のcron取込: production未反映のため
  - 実Gmailへの接続: 本番の認証情報を使う試験はしていない

## Log・監視

- log量/形式/保存先変更: なし（上記のreason・targetの追加のみ）
- 新しいalert条件: なし。`coop_fetch_status.json` の `consecutive_failures` を監視に使うかは、VPS管理側で判断（今回の停止は、`external_call_failed`（`error_kind=auth`）の連続で検知できた）
- secret/個人情報対策: 状況fileとログに、秘密値・メール本文・件名・送信者・商品名・金額を出さない

## 提出前セルフチェック

通知006で全項目を実施済みのため、今回は変更範囲に絞って確認した。再提出では、`a09b441` から `5146ab1` までの差分（配布対象2file・`API_SPEC.md`・`CLAUDE.md`・test）だけを確認した。依存・認証・bind・cron・env・lock・終了コード・`deploy-files.txt` は不変で、コミットに秘密値・実メールの内容は無い。再々提出では、`5146ab1` から `5b27f01` までの差分（`fetch_coop_mail.py`・`API_SPEC.md`・`CLAUDE.md`・test）だけを確認した。新しい依存は無い（標準ライブラリの `stat` のみ）。

- baseline `c94f8a0` から `source_commit` までのops以外の差分は、配布対象の `coop_api_server.py`・`fetch_coop_mail.py` と、文書・test（`API_SPEC.md`・`CLAUDE.md`・`tests/`）。`coop_parser.py`・`jev_classifier.py`・`requirements.txt`・`deploy-files.txt` は変更なし。依存・認証・bind・cron・env変数名・lock・終了コードは不変。
- 配布物に `.env`・`data/`・一時file・実メールの内容が入らないこと、通知書とコミットに実際の金額・番号・URL・アドレス・パスワードが無いことを確認した。
- VPS管理側の `review_notice_preflight.ps1` は、アプリ側のPowerShell 5.1では実行できない（通知006・007と同じ）。VPS管理側での実行をお願いしたい。

## 未解決事項

1. 本番での所有者・グループの確認。新規の `coop_fetch_status.json` は 0640 で作り、`coop_orders.json`・`coop_latest.json` は置き換え後も mode を引き継ぐが、所有者・グループは書いたプロセスになるため、反映後に3fileとも `coop-api:coop-api`・0640 であることの確認をお願いしたい。
2. `consecutive_failures` を監視・通知に使うかの判断（VPS管理側）。
3. アプリ（`367447a`）の端末配信は、本noticeのproduction反映・確認の後に、app ownerが別途判断する。

## 希望時期

VPS管理レビュー後に別途調整。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- VPS管理チャットへ渡すローカル絶対path: C:\work\PRG\HomeTools\meal-planner\api\coop-api\ops\server-change-notices\20261118-COOPAPI-008-summary.md

## Approval

- app owner: 方針を承認（2026-11-18「その対応、進めましょう」「良いです」）
- VPS management review: 初回（`a09b441`）は差し戻し（保存失敗時の応答と状態更新・古い再送メールの扱い・IMAP切断の分類）。2026-11-27 に `5146ab1` で再提出。再レビューで保存失敗の502は確認済み、排他・履歴の再試行・同日旧版・IMAPの初期化・状態fileの権限・文書を差し戻し。2026-11-27 に `5b27f01` で再々提出
- production approval: 未実施（本番反映・端末配信は別承認）
