# x-blocked-detector

「自分をブロックしている X(Twitter)アカウント」を検出するツール(プロトタイプ)。
公式の開発者 API には依存せず、X アプリ自身が使う内部 GraphQL API を自分のセッションで再現します。

## ⚠️ 注意(必ず読む)

- **非公式(リバースエンジニアリング)方式**です。X の利用規約・自動化ポリシーに抵触する恐れがあり、**アカウント停止・一時ロック・CAPTCHA のリスク**があります。自己責任・低頻度で使用してください。
- 「全ユーザーからの被ブロック数」は X の仕様上取得できません。検出できるのは**候補(自分と接点のあるアカウント)に含まれる相手のみ**です。
- X 側の仕様変更で壊れることがあります。

## セットアップ

```bash
pip install -r requirements.txt
copy config.example.json config.json
```

`config.json` を編集:

- `cookies.auth_token` / `cookies.ct0`: x.com にログイン → DevTools (F12) → Application → Cookies → `x.com` から取得
- `me.screen_name`: 自分のハンドル(@なし)

## 使い方

```bash
# 設定の検証(ネットワークなし)
python main.py --config config.json --dry-run

# 自分の user id を確認
python main.py --config config.json --mode self

# 特定のハンドルだけ判定
python main.py --config config.json --mode check --handles foo bar

# スキャン(候補収集 → 判定 → レポート)
python main.py --config config.json --mode scan
```

主なオプション:

| オプション | 意味 |
| --- | --- |
| `--limit N` | 判定する候補数の上限 |
| `--cache-ttl HOURS` | 判定キャッシュの有効時間(既定168=7日。`0` で無効) |
| `--clear-cache` | キャッシュ(`state.json`)を削除してからスキャン |
| `--max-pages N` | 収集ページ総量の上限(巨大アカウント向け・既定無制限) |
| `--time-budget SEC` | 収集フェーズの時間上限(秒) |
| `--tid off` | 400/403 が続く場合に試す(トランザクションIDなしで動作) |

出力:

- コンソール: 件数サマリ / BLOCKED_BY 一覧(検出した瞬間にも `  [!!] @user` と逐次表示)
- `blocked_report.csv`(utf-8-sig)
- `.state.json`: 前回結果を保存。次回は「新規ブロック / 解除」差分の表示と、TTL 内の判定キャッシュ利用による高速化に使う

## 判定ステータス

| ステータス | 意味 |
| --- | --- |
| `BLOCKED_BY` | 完全ブロックされている |
| `SMART_BLOCKED_BY` | Smart Block されている(フォロー関係は維持されたまま投稿が非表示) |
| `OK` | ブロックされていない |
| `SUSPENDED` | 相手のアカウントが凍結中 |
| `DEACTIVATED` | 相手が退会済み |

## 既知の問題と対処

2026 年現在、依存ライブラリ(`twitter_openapi_python`)がトランザクションID(`x-client-transaction-id`)を生成できないため、本ツールは自動で「ヘッダなしモード」にフォールバックします(`tid_mode: auto`)。取得系 API は多くの場合ヘッダなしで動作しますが、400/403 が続く場合は `--tid off` を試してください。将来ライブラリが直り TID 生成が成功するようになっても、`auto` のまま問題ありません。

## config の主な項目

| キー | 意味 |
| --- | --- |
| `limits.max_own_tweets` | 自分のツイート取得上限(リプ先/メンション/RT/引用の著者=候補の主力) |
| `limits.max_likes` / `max_bookmarks` | いいね/ブックマーク済みツイートの著者(ブロック前の痕跡が残る最重要ソース) |
| `limits.max_connect` / `max_notifications` | コネクトタブ/通知から候補化(既定50/50) |
| `limits.max_tweet_threads` | 自分の直近ツイートの返信者を候補化(既定10本) |
| `limits.max_following` / `max_followers` | **既定0(スキップ)**。完全ブロック者は双方向アンフォローされ一覧に存在しないため。Smart Block も検出したい場合は値を設定 |
| `limits.max_favoriters` / `max_retweeters` | 自分のツイートへのファボ/RT した人(既定0・補助) |
| `delay_seconds` | ページ間スリープ(大きいほど安全) |
| `concurrency` | 判定の並列数(既定12・最大16) |
| `tid_mode` | `auto`(既定) / `off` |
| `cache_ttl_hours` | 判定キャッシュのTTL(既定168、0で無効) |
| `max_pages` / `time_budget_seconds` | 収集の横断予算(既定0=無制限) |

## 推奨ワークフロー

初回は予算つきで全走破 → 以降はキャッシュ差分スキャン:

```bash
python main.py --config config.json --mode scan --max-pages 400
python main.py --config config.json --mode scan   # 以降はキャッシュで高速(プローブ0件も可能)
```

## テスト

```bash
python -m pytest tests/ -q
```
