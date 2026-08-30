# YRC 0004: 代表的 MJAI 実装プロファイル

| 項目 | 値 |
|---|---|
| 文書系列 | YAMAI Request for Comments (YRC) |
| 文書番号 | YRC 0004 |
| 表題 | Implementation Profiles of Widely Used MJAI Applications |
| 分類 | Informational |
| 状態 | Draft |
| 版 | 1.0-draft.1 |
| 発行日 | 2026-08-30 |
| 更新対象 | なし |
| 廃止対象 | なし |

## Abstract

MJAI を採用するアプリケーションは、Gimite のイベント語彙を共有する一方、transport、frame 単位、行動要求の時点、座席の通知方法、timeout および独自フィールドが異なる。本書は、代表的な6実装について公開仕様と公開コードから観測できる wire profile を記述する。

本書の目的は、「MJAI対応」という表明だけでは判定できない互換性を、実装名と revision に結び付けて明示することである。本書は各実装を評価または順位付けせず、新しい適合要件を制定しない。

## Status of This Memo

本書は YAMAI Project が管理する Informational 文書であり、IETF Internet Standard ではない。本書の配布に制限はない。

実装の挙動は更新され得る。本書の記述は2026年8月30日時点の公開資料に基づく。相互運用を必要とするシステムは、実装名だけでなく、§2.1に示すcommit、releaseまたはprotocol revisionを固定することが望ましい。

## Table of Contents

1. 適用範囲
2. 比較表
3. Gimite `mjai` TCP Server
4. Mortal Engine
5. mjai.app Simulator
6. Akagi v3 Bot Interface
7. RiichiLab Protocol v2
8. mjai-reviewer
9. 相互運用上の含意
10. Security Considerations
11. Registry Considerations
12. References
Appendix A. Machine-readable Profile Template

## 1. 適用範囲

### 1.1 比較対象

本書は次を比較する。

1. Gimite `mjai`: 原典の TCP 対局サーバ実装
2. Mortal: AI engine の標準入出力 interface
3. mjai.app: 旧競技プラットフォームの simulator interface
4. Akagi v3: 外部 bot subprocess interface
5. RiichiLab: WebSocket を使用する現行競技プラットフォーム
6. mjai-reviewer: 牌譜変換・AI review application

Akochan、Mortalの学習データ形式、Mahjong Soul・天鳳の原生 wire protocol は対象外である。

### 1.2 比較軸

| 軸 | 定義 |
|---|---|
| transport | TCP、stdio、WebSocket、file等 |
| input unit | 1個のlogical frame／application messageで意味上処理するevent数（OSの`read`境界ではない） |
| output unit | 1 input unit に対して要求される response |
| action trigger | responseを返す時点 |
| correlation | requestとresponseを対応付ける方法 |
| seat source | 自分の絶対座席を取得する方法 |
| timeout | action期限と期限超過時の処理 |
| extension | 原典にない eventまたはmember |

## 2. 比較表

| 実装 | transport | Host → Bot | Bot → Host | correlation | seat source | 3人麻雀 |
|---|---|---|---|---|---|---|
| Gimite `mjai` | TCP JSON Lines | 1 object / line | 全eventへ1 action / line | 到着順 | `start_game.id` | 非対応 |
| Mortal | stdio JSON Lines | 1 event object / line | 行動生成時だけ1 object / line | event順 | argv `0..3` | 基本interfaceは4人 |
| mjai.app | stdio JSON Lines | 1 event array / line | 1 action object / line | batch順 | argv `0..3` | 対象外 |
| Akagi v3 | stdio JSON Lines | 1 event array / line | 1 reaction object / line | batch順 | `start_game.id`、argv、env | `kita`拡張あり |
| RiichiLab v2 | WebSocket text message | 1 object / message | `request_action`へ1 object | `request_id` | `start_game.id` | `Observation3P` |
| mjai-reviewer | file/変換 + engine subprocess | engineにより異なる | engineにより異なる | engine adapter | CLIで視点指定 | 4人のみを基本とする |

同じ JSON event 名を使用していても、input unit が object と array で異なる実装は直接接続できない。

ここでいうframeは、実装がJSONを意味解釈する論理境界である。TCPまたはstdioのOS `read`は、1行を分割して返すことも複数行をまとめて返すこともあるため、read回数・byte分割・write回数はframe数とは数えない。JSON Lines profileは改行までを蓄積して1 logical frameとして解析し、1回のreadに複数行が含まれる場合は各行を順番に処理する。WebSocket profileはprotocolが定義するtext messageをlogical frameとし、fragment frameの境界は意味を持たない。

### 2.1 Source snapshots and evidence

本書のprofile記述はInformationalな観測記録であり、下表のrevisionに対してだけ再現可能性を主張する。`未固定`は、公式URLと対象ファイルは確認できたが、commit履歴を確認できなかったことを表す。確認日は2026-08-30である。外部profileのrevisionはYAMAIの適合要件ではない。

| profile | protocol revision | 公式source revision／確認結果 | 対象ファイルまたは仕様 |
|---|---|---|---|
| Gimite `mjai` | `mjsonp`, 原典 `protocol_version=1`／現行コード `3` | `master`。公式GitHubのcommit履歴は2026-08-30に取得不能でcommit未固定 | [`lib/mjai/tcp_game_server.rb`](https://github.com/gimite/mjai/blob/master/lib/mjai/tcp_game_server.rb)、[`lib/mjai/game.rb`](https://github.com/gimite/mjai/blob/master/lib/mjai/game.rb)、[`lib/mjai/tcp_player.rb`](https://github.com/gimite/mjai/blob/master/lib/mjai/tcp_player.rb)、[`lib/mjai/action.rb`](https://github.com/gimite/mjai/blob/master/lib/mjai/action.rb) |
| Mortal | MJAI event stream（protocol version fieldなし） | `main@0cff2b52982be5b1163aa9a62fb01f03ce91e0d2` | [`mortal/mortal.py`](https://github.com/Equim-chan/Mortal/blob/0cff2b52982be5b1163aa9a62fb01f03ce91e0d2/mortal/mortal.py)、[`libriichi/src/mjai/event.rs`](https://github.com/Equim-chan/Mortal/blob/0cff2b52982be5b1163aa9a62fb01f03ce91e0d2/libriichi/src/mjai/event.rs) |
| mjai.app | `mjai-client:v3`, event-array batch | `main@cc24bace09673d1d38b4315031a1ce63fb1b5abf`（shutdown noticeを含む最終確認commit） | [`README.md`](https://github.com/smly/mjai.app/blob/cc24bace09673d1d38b4315031a1ce63fb1b5abf/README.md) |
| Akagi | `v3.7.0` | `v3.7.0@a7565de28037c3759647d1d6327e5be42d11e924` | [`README.md`](https://github.com/shinkuan/Akagi/blob/v3.7.0/README.md)、[`mjai_bot/README.md`](https://github.com/shinkuan/Akagi/blob/v3.7.0/mjai_bot/README.md) |
| RiichiLab | Protocol v2（2026-06-10 22:51 JST有効） | 公式仕様および告知で確認。実装は [`RiichiEnv@b1d08b3615a710f929679fefb50d1c384f2070b9`](https://github.com/smly/RiichiEnv/commit/b1d08b3615a710f929679fefb50d1c384f2070b9) を観測基準とする | [公式MJAI Protocol](https://riichi.dev/docs/protocol)、[`README.md`](https://github.com/smly/RiichiEnv/blob/b1d08b3615a710f929679fefb50d1c384f2070b9/README.md) |
| mjai-reviewer | README記載の1.x系（0.x系とは非互換） | `master@2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9` | [`README.md`](https://github.com/Equim-chan/mjai-reviewer/blob/2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9/README.md)、[`faq.md`](https://github.com/Equim-chan/mjai-reviewer/blob/2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9/faq.md)、[`src/review/mortal.rs`](https://github.com/Equim-chan/mjai-reviewer/blob/2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9/src/review/mortal.rs) |

## 3. Gimite `mjai` TCP Server

### 3.1 Transportとlifecycle

Gimite profile は TCP 上の1行1 JSON object である。ホストは `hello` を送信し、bot は `join` を返す。ゲーム開始時に `start_game.id` が自分の絶対座席を通知する。1ゲームの終了後、サーバは接続を閉じる [GIMITE-MJAI]。

公開説明の `protocol_version` は1であるが、現行公開コードは3を送信する [GIMITE-SERVER]。版番号ごとの差分交渉は存在しない。

### 3.2 応答モデル

ホストは各eventを全プレイヤーへ配信し、全プレイヤーから1 actionを読む。行動しないプレイヤーも `none` を返す。応答はrequest IDを持たず、到着順で直前eventへ結び付く。

Gimite v3は、自分の `tsumo`、他家の `dahai`・`kakan` に `possible_actions` を付加する。他家の加槓に対して `hora` を返すことで槍槓を宣言できる [GIMITE-GAME]。Host → Playerのprotocol error message（`{"type":"error","message":...}`）と、Player → Hostの `error` actionは別のものとして扱う。前者は接続・入力エラーの通知、後者は要求を処理できないことを知らせる行動であり、いずれもYAMAIの`kind=error`または`kind=action`へ自動的に読み替えられない。

TCP player実装の応答timeoutは60秒である。timeout、不正JSONまたは違法actionはPlayer → Hostの `error` actionとして扱われ、game validationで処理される [GIMITE-PLAYER]。接続受け入れやjoin失敗時にHost → Playerへ送る`error` protocol messageとは方向も意味も異なる。

### 3.3 識別子

```text
profile_id: gimite-mjai-v3
aliases: gimite-mjsonp-v3 (旧記載。公式transport schemeの `mjsonp://` に由来)
transport: tcp-jsonl-object
input_unit: event-object
response_policy: every-event
correlation: arrival-order
seat_source: start_game.id
```

## 4. Mortal Engine

### 4.1 Transport

Mortalの標準engineは、stdinから1行ずつMJAI event objectを読み取る。内部botがactionを生成した場合だけ、stdoutへ1行のaction objectをflushする [MORTAL-ENGINE]。これは1行にevent arrayを受け取るbatch interfaceではない。

player IDは起動引数の整数 `0..3` で指定する。Mortalの `Event::StartGame` 型は `id` を保持しない [MORTAL-EVENT]。

### 4.2 Event subsetとextension

Mortalのevent型は原典全体を実装せず、独自拡張を持つことをコード上で明示する。主な差は次のとおりである。

- `start_kyoku.scores` と4×13枚の `tehais` を必須型として保持する
- `hora` は `actor`、`target`、任意の `deltas`・裏ドラ表示牌欄を保持し、原典の詳細結果を削減する（YAMAI gatewayでは `ura_dora_markers` へ正規化）
- `ryukyoku` は任意の `deltas` だけを保持する
- `meta` にQ値、推論時間、shanten等を記録できる
- `can_act` をeventへ追加できる

### 4.3 識別子

```text
profile_id: mortal-mjai-stream
transport: stdio-jsonl-object
input_unit: event-object
response_policy: action-events-only
correlation: event-order
seat_source: argv
```

## 5. mjai.app Simulator

### 5.1 状態

mjai.appは歴史的に重要な競技用profileであるが、旧サービスは2026年4月30日に終了し、後継はRiichiLabへ移行した [MJAI-APP-SUNSET]。本節は既存botと牌譜を扱うために記録する。したがって、本profileはretired（現行サービスへの接続先ではない）として扱い、RiichiLabとwire互換であると解釈しないことが望ましい。

### 5.2 Batch interface

botはplayer ID `0..3` をargvで受け取る。ホストは、前回の応答後から次の行動可能eventまでを1個のJSON arrayへ蓄積し、stdinの1行として送信する。botは各batchへ1個のMJAI action objectを返す [MJAI-APP]。

ホストはエラー後にcontainerを再起動し、東1局以外から再開できる。このため、botはprocess lifetimeをgame lifetimeと同一視できない。

### 5.3 Timeoutとpenalty

protocol説明は2秒以内に応答しない場合を満貫相当のチョンボとする。一方、公開Simulator APIの説明では `timeout` の既定値を3.0秒としている [MJAI-APP]。実際の期限はdeployment設定に依存するため、botは固定値を仮定すべきでない。

### 5.4 識別子

```text
profile_id: mjai-app-batch-v3
transport: stdio-jsonl-array
input_unit: event-array
response_policy: every-batch
correlation: batch-order
seat_source: argv
```

## 6. Akagi v3 Bot Interface

### 6.1 Batch interface

Akagi v3はbotをgameごとにsubprocessとして起動する。stdinの各行は、前回のreaction後に蓄積したMJAI eventのJSON arrayである。botは各行へ必ず1個のreaction objectを返す。診断はstderrへ出力する [AKAGI-BOT]。

主な互換性特性は次のとおりである。

- `start_game.id` を座席の最終的な権威とする
- argvと `AKAGI_PLAYER_ID` もmjai.app互換のため提供する
- `start_game` に `num_players` と `aka_flag` を追加する
- 3人麻雀で `kita` eventを追加する
- reactionの任意 `meta` objectをHUDへ転送する
- batchが `end_game` を含む場合、1回応答して終了する
- 文書上の既定reaction budgetは約5秒である

### 6.2 識別子

```text
profile_id: akagi-v3-batch
transport: stdio-jsonl-array
input_unit: event-array
response_policy: every-batch
correlation: batch-order
seat_source: start_game.id
extensions: num_players, aka_flag, kita, meta
```

## 7. RiichiLab Protocol v2

### 7.1 Transportとrequest

RiichiLabはWebSocket text messageで1個のJSON objectを送受信する。botは `request_action` にだけ応答する。`request_action` は単調増加する `request_id`、`grace_ms`、残り `bank_ms`、`deadline_ms`、`possible_actions` およびbase64 encodingされたRiichiEnv observationを持つ [RIICHI-PROTOCOL]。

botはresponseへ同じ `request_id` を含める。古いIDは `stale` として破棄され、未来または未知IDはprotocol違反となる。後方互換のためIDなしresponseもarrival orderで受理される [RIICHI-PROTOCOL-V2]。

### 7.2 ACKとtimeout

ホストはaction処理後に `action_ack` を送る。statusは少なくとも `accepted`、`rejected`、`unparseable`、`stale`、`defaulted` を持つ。期限超過時、自摸番ではツモ切り、claim時では `none` を既定actionとして採用する。

time bankは局ごとにresetされる。公開既定値は各requestの3秒graceと局ごとの15秒bankである [RIICHI-PROTOCOL-V2]。

### 7.3 3人麻雀

4人麻雀は `Observation`、3人麻雀は `Observation3P` を使用する。MJAI eventだけでなくobservationのbinary-compatible schemaもprofileの一部である。

### 7.4 識別子

```text
profile_id: riichilab-v2
transport: websocket-json-object
input_unit: request-or-event-object
response_policy: request-action-only
correlation: request_id
seat_source: start_game.id
extensions: request_action, action_ack, observation, time
```

## 8. mjai-reviewer

mjai-reviewerは対局ホストではなく、天鳳・Mahjong Soul等の牌譜をMJAI eventへ変換し、MortalまたはAkochan等のengineへ入力するreview applicationである [MJAI-REVIEWER]。公開FAQは3人麻雀牌譜を非対応と明記する [MJAI-REVIEWER-FAQ]。

このprofileでは次が重要である。

- 主入力はlive gameではなく完全情報または指定視点のreplayである
- engine subprocessのwire profileは選択したengine adapterに依存する
- 同じ牌譜でもreview対象seatごとに非公開情報をmaskする必要がある
- 1.xと旧0.x系は互換でない
- 変換元に存在しないMJAI情報はconverterが推定する場合がある

したがって、mjai-reviewerを単一の対局wire dialectとして扱わないことを推奨する。牌譜converterとengine adapterを別profileとして識別することが望ましい。

## 9. 相互運用上の含意

### 9.1 直接接続できない組合せ

次はadapterなしで接続できない。

- GimiteまたはMortalのobject streamと、mjai.appまたはAkagiのarray batch
- arrival-order型botと、`request_id`を必須とするRiichiLab request
- 4人固定botと、Akagi・RiichiLabの3人麻雀event
- replay consumerと、非公開情報を要求するlive play session

### 9.2 Adapter設計で明示する事項（推奨）

adapterの設計上、相互運用に必要な次の事項を明示することを推奨する。本書はadapterへ規範要件を課さず、YAMAI適合性は [YRC 0003] `1.0-draft.5` の規範文書で判断する。

1. object streamからbatchを作るflush条件
2. batchからobject streamへ展開した際のresponse抑制
3. seatの取得元と上書き規則
4. `possible_actions`、`can_act`、`request_action`の変換
5. timeoutとlate responseの扱い
6. `hora`・`ryukyoku`の欠落結果member
7. 裏ドラ表示牌欄の名称変換（YAMAIでは `ura_dora_markers` へ正規化）
8. 3人麻雀と未知eventの拒否規則

情報を損失する変換は、黙って既定値を補わず、変換不能または推定したmemberを診断として記録することが望ましい。adapterの各変換規則と損失箇所は、[YRC 0003] `1.0-draft.5` 第16節のMJAI移行要件、および同第17節が要求する同一release tagのSchema、registry、公式test vectorへ追跡可能にすることを推奨する。

## 10. Security Considerations

stdio profileではstdoutをprotocol専用とし、診断をstderrへ分離することが望ましい。WebSocket profileでは認証token、observationおよび完全情報replayをログへ出力しないことを推奨する。

外部bot subprocessは、入力牌譜、model、任意codeを処理する。競技・review hostはfilesystem、network、CPU、memory、process数および実行時間をsandboxで制限すべきである。

## 11. Registry Considerations

本書の `profile_id` はYAMAI Project内の記述用識別子であり、各上流projectが割り当てた公式名称ではない。stable registryへ登録する場合は、対象revision、maintainerおよびconformance fixtureを固定することが望ましい。

## 12. References

### 12.1 Informative References

- [GIMITE-MJAI] Gimite, “Mjai 麻雀AI対戦サーバ”.  
  https://gimite.net/pukiwiki/index.php?Mjai+%E9%BA%BB%E9%9B%80AI%E5%AF%BE%E6%88%A6%E3%82%B5%E3%83%BC%E3%83%90=
- [GIMITE-SERVER] Gimite, `tcp_game_server.rb`.  
  https://github.com/gimite/mjai/blob/master/lib/mjai/tcp_game_server.rb
- [GIMITE-GAME] Gimite, `game.rb`.  
  https://github.com/gimite/mjai/blob/master/lib/mjai/game.rb
- [GIMITE-PLAYER] Gimite, `tcp_player.rb`.  
  https://github.com/gimite/mjai/blob/master/lib/mjai/tcp_player.rb
- [MORTAL-ENGINE] Equim-chan, Mortal `mortal.py`（`main@0cff2b52982be5b1163aa9a62fb01f03ce91e0d2`）。
  https://github.com/Equim-chan/Mortal/blob/0cff2b52982be5b1163aa9a62fb01f03ce91e0d2/mortal/mortal.py
- [MORTAL-EVENT] Equim-chan, Mortal MJAI Event（`main@0cff2b52982be5b1163aa9a62fb01f03ce91e0d2`）。
  https://github.com/Equim-chan/Mortal/blob/0cff2b52982be5b1163aa9a62fb01f03ce91e0d2/libriichi/src/mjai/event.rs
- [MJAI-APP] smly, mjai.app README（`main@cc24bace09673d1d38b4315031a1ce63fb1b5abf`）。
  https://github.com/smly/mjai.app/blob/cc24bace09673d1d38b4315031a1ce63fb1b5abf/README.md
- [MJAI-APP-SUNSET] smly, “Sunsetting the Old RiichiLab”.  
  https://github.com/smly/mjai.app/discussions/203
- [AKAGI-BOT] Shinkuan, “Writing an mjai bot for Akagi”（`v3.7.0@a7565de28037c3759647d1d6327e5be42d11e924`）。
  https://github.com/shinkuan/Akagi/blob/v3.7.0/mjai_bot/README.md
- [RIICHI-PROTOCOL] RiichiLab, “MJAI Protocol”.  
  https://riichi.dev/docs/protocol
- [RIICHI-PROTOCOL-V2] smly, “Protocol v2: request_id, action_ack, and time bank are now live”.  
  https://github.com/smly/RiichiEnv/discussions/216
- [MJAI-REVIEWER] Equim-chan, mjai-reviewer（`master@2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9`）。
  https://github.com/Equim-chan/mjai-reviewer/blob/2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9/README.md
- [MJAI-REVIEWER-FAQ] Equim-chan, mjai-reviewer FAQ（同revision）。
  https://github.com/Equim-chan/mjai-reviewer/blob/2dc5ec5c8b28517cfb45f57eb21536d9a8f67aa9/faq.md
- [YRC 0001] YAMAI Project, “デファクト MJAI プロトコル記述仕様”.
- [YRC 0002] YAMAI Project, “MJAI プロトコルの設計上の欠陥”.
- [YRC 0003] YAMAI Project, “YAMAI Protocol Version 1 (1.0-draft.5)”.

## Appendix A. Machine-readable Profile Template

```yaml
profile_id: example-profile
upstream:
  repository: https://example.invalid/repository
  revision: commit-or-release
role: game-host | bot-engine | replay-converter | review-host
transport: tcp | stdio | websocket | http | file
framing: jsonl-object | jsonl-array | websocket-object | other
input_unit: event-object | event-array | request-object | replay
response_policy: every-event | every-batch | action-events-only | request-only | none
correlation: arrival-order | event-order | batch-order | request-id
seat_source: start_game.id | argv | environment | configured
timeout:
  value_ms: null
  consequence: none | default-action | penalty | disconnect | implementation-defined
player_counts: [4]
extensions: []
unknown_event_policy: ignore | reject | configured
source: https://example.invalid/specification
```
