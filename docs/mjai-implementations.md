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

実装の挙動は更新され得る。本書の記述は2026年8月30日時点の公開資料に基づく。相互運用を必要とするシステムは、実装名だけでなく commit、release または protocol revision を固定すべきである。

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
| input unit | 1回のreadで意味上処理する event 数 |
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

## 3. Gimite `mjai` TCP Server

### 3.1 Transportとlifecycle

Gimite profile は TCP 上の1行1 JSON object である。ホストは `hello` を送信し、bot は `join` を返す。ゲーム開始時に `start_game.id` が自分の絶対座席を通知する。1ゲームの終了後、サーバは接続を閉じる [GIMITE-MJAI]。

公開説明の `protocol_version` は1であるが、現行公開コードは3を送信する [GIMITE-SERVER]。版番号ごとの差分交渉は存在しない。

### 3.2 応答モデル

ホストは各 event を全プレイヤーへ配信し、全プレイヤーから1 actionを読む。行動しないプレイヤーも `none` を返す。応答はrequest IDを持たず、到着順で直前eventへ結び付く。

Gimite v3は、自分の `tsumo`、他家の `dahai`・`kakan` に `possible_actions` を付加する。他家の加槓に対して `hora` を返すことで槍槓を宣言できる [GIMITE-GAME]。

TCP player実装の応答timeoutは60秒である。timeout、不正JSONまたは違法actionは `error` actionへ変換され、game validationで処理される [GIMITE-PLAYER]。

### 3.3 識別子

```text
profile_id: gimite-mjsonp-v3
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

mjai.appは歴史的に重要な競技用profileであるが、旧サービスは2026年4月30日に終了し、後継はRiichiLabへ移行した [MJAI-APP-SUNSET]。本節は既存botと牌譜を扱うために記録する。

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

したがって、mjai-reviewerを単一の対局wire dialectとして扱ってはならない。牌譜converterとengine adapterを別profileとして識別する必要がある。

## 9. 相互運用上の含意

### 9.1 直接接続できない組合せ

次はadapterなしで接続できない。

- GimiteまたはMortalのobject streamと、mjai.appまたはAkagiのarray batch
- arrival-order型botと、`request_id`を必須とするRiichiLab request
- 4人固定botと、Akagi・RiichiLabの3人麻雀event
- replay consumerと、非公開情報を要求するlive play session

### 9.2 Adapterの最低要件

adapterは次を明示しなければならない。

1. object streamからbatchを作るflush条件
2. batchからobject streamへ展開した際のresponse抑制
3. seatの取得元と上書き規則
4. `possible_actions`、`can_act`、`request_action`の変換
5. timeoutとlate responseの扱い
6. `hora`・`ryukyoku`の欠落結果member
7. 裏ドラ表示牌欄の名称変換（YAMAIでは `ura_dora_markers` へ正規化）
8. 3人麻雀と未知eventの拒否規則

情報を損失する変換は、黙って既定値を補ってはならない。変換不能または推定したmemberを診断として記録すべきである。adapterの各変換規則と損失箇所は、[YRC 0003] 第16節のMJAI移行要件、および同第17節が要求する同一release tagのSchema、registry、公式test vectorへ追跡可能にする。

## 10. Security Considerations

stdio profileではstdoutをprotocol専用とし、診断をstderrへ分離する必要がある。WebSocket profileでは認証token、observationおよび完全情報replayをログへ出力してはならない。

外部bot subprocessは、入力牌譜、model、任意codeを処理する。競技・review hostはfilesystem、network、CPU、memory、process数および実行時間をsandboxで制限すべきである。

## 11. Registry Considerations

本書の `profile_id` はYAMAI Project内の記述用識別子であり、各上流projectが割り当てた公式名称ではない。stable registryへ登録する前に、対象revision、maintainerおよびconformance fixtureを固定する必要がある。

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
- [MORTAL-ENGINE] Equim-chan, Mortal `mortal.py`.  
  https://github.com/Equim-chan/Mortal/blob/main/mortal/mortal.py
- [MORTAL-EVENT] Equim-chan, Mortal MJAI Event.  
  https://github.com/Equim-chan/Mortal/blob/main/libriichi/src/mjai/event.rs
- [MJAI-APP] smly, mjai.app README.  
  https://github.com/smly/mjai.app
- [MJAI-APP-SUNSET] smly, “Sunsetting the Old RiichiLab”.  
  https://github.com/smly/mjai.app/discussions/203
- [AKAGI-BOT] Shinkuan, “Writing an mjai bot for Akagi”.  
  https://github.com/shinkuan/Akagi/blob/v3/mjai_bot/README.md
- [RIICHI-PROTOCOL] RiichiLab, “MJAI Protocol”.  
  https://riichi.dev/docs/protocol
- [RIICHI-PROTOCOL-V2] smly, “Protocol v2: request_id, action_ack, and time bank are now live”.  
  https://github.com/smly/RiichiEnv/discussions/216
- [MJAI-REVIEWER] Equim-chan, mjai-reviewer.  
  https://github.com/Equim-chan/mjai-reviewer
- [MJAI-REVIEWER-FAQ] Equim-chan, mjai-reviewer FAQ.  
  https://github.com/Equim-chan/mjai-reviewer/blob/master/faq.md
- [YRC 0001] YAMAI Project, “デファクト MJAI プロトコル記述仕様”.
- [YRC 0002] YAMAI Project, “MJAI プロトコルの設計上の欠陥”.
- [YRC 0003] YAMAI Project, “YAMAI Protocol Version 1 (1.0-draft.4)”.

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
