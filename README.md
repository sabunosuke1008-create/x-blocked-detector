# x-blocked-detector (prototype)

「自分をブロックしている X(Twitter)アカウント」を、フォロワー/フォロー中・自分と接点のあるアカウントを対象に検出するプロトタイプです。

公式の開発者 API(X API v2 / OAuth)には依存せず、**X アプリ自身が使っている内部 GraphQL API**(`api.x.com/graphql`)を自分のセッションで再現します。ベースは `fa0311/twitter_openapi_python`、判定の型定義と非表示理由の分類は `prinsss/twitter-web-exporter` を参考にしています。

## ⚠️ 注意(必ず読む)

- これは**非公式(リバースエンジニアリング)方式**です。X の利用規約・自動化ポリシーに抵触する恐れがあり、**アカウント停止・一時ロック・CAPTCHA のリスク**があります。自己責任で、低速・低頻度で使用してください。
- 「全ユーザーからの被ブロック数」の取得は X の仕様上不可能です。本ツールが扱えるのは**候補リストに含まれたアカウントのみ**です。
- APK 解析の結果、`relationship_perspectives.blocked_by` により「そのユーザーにブロックされているか」が得られることを確認済み。ただしレスポンス仕様はアプリ更新で変わることがあります。

## セットアップ

```bash
pip install -r requirements.txt   # twitter_openapi_python
copy config.example.json config.json
```

`config.json` にクッキーを設定:

- `cookies.auth_token` / `cookies.ct0`: ブラウザで x.com にログイン → DevTools (F12) → Application → Cookies → `x.com` から取得
- `me.screen_name`: 自分(ハンドルは @ なし)

URL を手入力したくない場合はプロキシ(mitmproxy 等)でアプリの通信から取得する方法もあります。

## 使い方

```bash
# 設定の検証のみ(ネットワークなし)
python main.py --config config.json --dry-run

# 自分の user id を確認
python main.py --config config.json --mode self

# 特定のハンドルだけ判定
python main.py --config config.json --mode check --handles foo bar

# スキャン(候補収集 → 一括チェック → レポート)
python main.py --config config.json --mode scan
python main.py --config config.json --mode scan --limit 200
```

## 既知の問題と対処(重要)

X の Web 画面の HTML から ondemand JS の参照が消えたため、2026 年現在 `twitter_openapi_python` の依存ライブラリ(`x_client_transaction`)がトランザクションID(`x-client-transaction-id`)を生成できません(下記エラー)。

```
AttributeError: 'NoneType' object has no attribute 'group'   (x_client_transaction/utils.py, get_ondemand_file_url)
```

本ツールは自動で「トランザクションIDなしモード」にフォールバックします(`tid_mode: auto`)。ヘッダなしで X の内部 API が受け付けるかはエンドポイント次第ですが、取得系 GraphQL は多くは受け付けます。もし 400/403 系のエラーが出る場合は:

```bash
python main.py --config config.json --mode self --tid off
```

と明示すると、最初からヘッダなしで動きます(逆に、将来ライブラリが直ってヘッダ生成が成功するようになった場合も `auto` ならそのまま使えます)。

出力:

- コンソールに件数サマリ / BLOCKED_BY 一覧 / ERROR・SUSPENDED 参考一覧
- `blocked_report.csv`(utf-8-sig)
- `.state.json` に前回スキャン結果を保存し、次回は「新しくブロックされた/解除された」を表示

## 仕組み

1. **候補収集**(`xblocked/collect.py`): 自分との「接点」ベース — いいね / ブックマーク / 自分のツイート(リプ先・メンション・引用・RTの著者) / コネクトタブ / 通知 / 自分のツイートのリプツリー(返信者) / (オプション)自分のツイートのファボ・RT したユーザー
2. **判定**(`xblocked/classify.py` + `rawclient.py`): 内部 GraphQL の生JSONを直接取得し、`relationship_perspectives.blocked_by` を確認。`UserUnavailable.reason`(Blocked / Suspended / Deactivated / ...)で誤検出を分類
3. **レポート**(`xblocked/report.py`): 件数・一覧・CSV・差分表示

## Smart Block 検出(2026-08 追加)

X の **Smart Block**(フォロー関係を維持したまま相手の投稿を非表示にする機能)は、通常のブロックと異なり**フォロワー/フォロー関係が切断されない**ため、従来の「フォロワー/フォロー一覧には被ブロック者が残らない」という前提が当てはまりません。

APK 解析 + Playwright による動的解析により以下を特定し、実装しました:

- Android アプリの GraphQL 操作 `GetUserByScreenNameQuery` の persisted query ID: `DuN4Qld4UROZ63wKFX8cfw`
- このクエリの応答には Web 版に無い以下が含まれる:
  - `smart_blocked_by`(相手が私を Smart Block している)
  - `smart_blocking`(私が相手を Smart Block している)
  - `relationship_perspectives.live_following` / `muted_by`
- モデル側の対応: `com.x.models.Friendship` の `isSmartBlockingMe` / `isSmartBlockedByMe`

### x-client-transaction-id の生成

Smart Block 検出用のモバイルクエリは `x-client-transaction-id` ヘッダーを必須とする場合があります。本ツールは **Playwright を併用**してこのヘッダーを自動生成します:

1. Playwright で x.com を開く(要ログイン Cookie)
2. webpack チャンク 59924(`ondemand.s.*.js`)を遅延ロード
3. SVG アニメーションフレーム + `twitter-site-verification` キーから TID を生成
4. 生成した TID を Python 側の API リクエストに付与

Playwright が利用できない環境では TID 無しでフォールバックします(Web 版クエリ経由)。

### 判定ステータス

| ステータス | 意味 |
| --- | --- |
| `BLOCKED_BY` | 完全ブロックされている |
| `SMART_BLOCKED_BY` | Smart Block されている |
| `OK` | ブロックされていない |
| `SUSPENDED` | 相手のアカウントが凍結中 |
| `DEACTIVATED` | 相手が退会済み |

### フォロワー/フォロー収集の扱い

- 完全ブロック: 相手はフォロワー/フォローから自動的に外れる → 収集不要
- **Smart Block: 関係が維持される → フォロワー/フォロー収集が有意味**
- 既定では `max_following` / `max_followers` は 0。Smart Block も検出したい場合は値を設定してください。

## 検証済みの状況(2026-08、当該アカウント実測)

| 操作 | 結果 | 備考 |
| --- | --- | --- |
| `1.1/friendships/show.json`(**判定の主力**) | ✅ 200 (上限800/15分) | `relationship.source.blocked_by` を明示的に返す。単発だが最速 |
| **`GetUserByScreenNameQuery`(Androidモバイル版)** | ✅ 200 | **`smart_blocked_by` / `smart_blocking` を取得可能**(APKから抽出したqueryId `DuN4Qld4UROZ63wKFX8cfw`)。Web版に無い `live_following` / `muted_by` も取れる |
| `UserByRestId` (api.x.com) | ✅ 200 (上限500/15分) | ID単発 |
| `UserByScreenName` (x.com/i/api) | ✅ 200 (上限150/15分) | 画面名単発(Web版・smart系フィールド無し) |
| `Following` / `Followers` | ✅ 200 | 候補収集で使用 |
| `UserTweets` / `Likes` / `Bookmarks` | ✅ 200 | 候補収集で使用 |
| **`ConnectTabTimeline`** | ✅ 200 | **コネクトタブ(自分への活動通知)から候補収集** |
| **`NotificationsTimeline`** | ✅ 200 | **メンション/リプ/いいね通知から候補収集** |
| **`TweetDetail`**(リプツリー) | ✅ 200 | **自分のツイートの返信者を候補収集**(`max_tweet_threads`) |
| **`FollowersYouKnow`** | ✅ 200 | 相互に近いフォロワー(既定0=フォロワー同様スキップ) |
| `Favoriters` / `Retweeters` | ✅ 200 | オプション(数値>0 で有効) |
| `CombinedLists` / `ListMembers` | ✅ 200 ※0件 | リスト未所持のため現状効果なし |
| `UsersByRestIds`(ID一括) | ❌ 403/404 | サーバー側で列挙APIを遮蔽 → 単発判定へ自動フォールバック |
| `UserByRestId` (x.com/i/api) / `users/show` | ❌ 403 | 同上 |
| `UserTweetsAndReplies` / `SearchTimeline` | ❌ 404 | クエリIDが旧式 → 更新しても遡上不可 |

## 判定の流れ(速度)

1. 候補収集(いいね・ブックマーク・自分のツイート・コネクトタブ・通知・リプツリー etc.)を並列ページングで取得
2. 一括(`UsersByRestIds`)を試す → 403 なら即フォールバック
3. **`friendships/show.json` を並列(`concurrency` 既定6)で叩いて `source.blocked_by` を判定**
   - レート上限(800/15分)に達したらリセット時刻まで自動待機
   - 空ページを返すタイムラインは即終了し、無駄なページングを排除

実測(当該アカウント・フォロー8+自分のツイート5): **7秒で完了**(従来の逐次方式は85秒)。数百候補でも数十秒〜1分程度で収まります。

## 大規模化(上限撤廃)の戦略

### 1. 収集 = 判定(追加プローブを激減)

タイムラインの各ユーザーノードには `relationship_perspectives.blocked_by` が**最初から内蔵**されています(`Following`/`Followers`/`UserTweets`/`Likes`/`Bookmarks` の全著者ノードで実測確認済み)。つまり収集ページを読むだけで「ブロックされているか」も同時に確定でき、**1ユーザー1リクエストのプローブが原則不要**になります。プローブは「メンション情報(JSON上 `blocked_by` が無い部分)だけで見つかった候補」に限定されます。

### 2. フォロー/フォロワー一覧は対象外(ブロックで双方向に切断される)

X の仕様上、**相手にブロックされると、その相手は自分のフォローリストから自動的に外れ、相手側のフォローも自動的に解除されます**(「ブロックリストに入れるとお互いに自動アンフォローする」= Unfollr / TweetDelete の公式挙動解説と一致)。つまり:

- 自分をブロックしている人は**フォロワーにもフォロー中にも存在し得ない**
- 通知(相手からのリプ/メンション/いいね)もブロック後に届かない
- 唯一残る痕跡は**ブロック前に行った自分の操作履歴**(いいね/ブックマーク/リポスト/引用の記録、自分のツイート中のリプ先・メンション)

このため `max_following` / `max_followers` / `max_followers_you_know` は**既定0(スキップ)**にしました。候補は「操作履歴ベース」の収集元が主力です。

### 3. 収集ソースの並列化

5つの収集元(`Following` / `Followers` / `UserTweets` / `Likes` / `Bookmarks`)を別スレッドで同時に回します。

### スケール試算(全上限撤廃した場合)

| パターン | ページ数 | 実時間 |
| --- | --- | --- |
| フォロー中 3,000人 | 150頁 | 約2〜4分(1予算窓=500/15分) |
| 自分のツイート 20,000件 | 1,000頁 | 15分(予算2窓に自動分散) |
| ブックマーク 全件 | 数百頁 | 数分 |
| いいね 全件 | 数百〜千頁 | 5〜15分 |
| フォロワー 100,000人 | 5,000頁 | 2.5〜5時間(**不要なので通常スキップ**) |
| メンションのみ候補へのプローブ | 数千件 | friendships(800/15分)+並列で数窓 |

どのサイズになっても**レート予算を自動追跡して待機**するため、タイムアウトで落ちることはありません(数日かけてでも完了します)。

X 側の仕様変更でレスポンスから `legacy` が消える等の構造変化があったため、厳格な pydantic モデルではなく**生JSONをそのまま読む方式**にしています。クエリIDは web クライアント(`main.*.js`)から毎回自動抽出・キャッシュします(`--refresh-ids` で強制更新)。

## config の主な項目

| キー | 意味 |
| --- | --- |
| `limits.max_following` / `max_followers` | **既定0**(ブロックで双方向アンフォローされるため、被ブロックは存在し得ない) |
| `limits.max_own_tweets` | 自分のツイートの取得上限(リプ先/メンション/RT/引用の著者=被ブロック候補の主力) |
| `limits.max_likes/max_bookmarks` | **いいね/ブックマーク済みツイートの著者(ブロック前の痕跡が残る最重要ソース)** |
| `limits.max_connect` / `max_notifications` | **コネクトタブ/通知(私へのリプ・メンション・いいね)から候補化**(既定50/50) |
| `limits.max_tweet_threads` | **自分の直近ツイートのリプツリーを展開し返信者を候補化**(既定10本) |
| `limits.max_favoriters/max_retweeters` | 自分のツイートへのファボ/RT した人(既定0・ブロック後は表示されないため補助) |
| `limits.use_search` / `search_queries` | 検索(`to:handle`)で接点候補を追加 ※2026-08現在404 |
| `delay_seconds` | ページ間スリープ(大きいほど安全) |
| `batch_size` | 一括判定のバッチサイズ(最大100) |
| `concurrency` | 単発判定の並列数(既定6、最大16) |
| `tid_mode` | `auto`(既定): 通常の `x-client-transaction-id` を試し、生成できない場合はヘッダなしでフォールバック / `off`: 最初からヘッダなし |

## テスト

```bash
python -m pytest tests/ -q
```

## ライセンスに関する参考

- `fa0311/twitter_openapi_python`: AGPL 系(本プロトタイプはその上に実装)
- `prinsss/twitter-web-exporter`: MIT(型定義・分類ロジックの参考)
- `buckket/twtblocks`: GPL-3.0(旧方式の参考文献)