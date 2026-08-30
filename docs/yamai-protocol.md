# YRC 0003: YAMAI Protocol Version 1

| 項目 | 値 |
|---|---|
| 文書系列 | YAMAI Request for Comments (YRC) |
| 文書番号 | YRC 0003 |
| 表題 | YAMAI Protocol Version 1 |
| 分類 | Standards Track |
| 状態 | Draft |
| Protocol Version | 1.0-draft.5 |
| 文書版 | 1.0-draft.5 |
| 発行日 | 2026-08-30 |
| 更新対象 | なし |
| 廃止対象 | なし |

## Abstract

本書は、リーチ麻雀の対局ホストと AI プレイヤーの間で、状態イベント、行動要求、行動応答および局結果を交換する YAMAI Protocol Version 1 を規定する。

YAMAI は JSON ベースのイベント語彙を MJAI から継承する一方、通知と要求を異なる message kind として定義し、各要求へ一意な `request_id`、各合法手へ不透明な `action_id`、各ホストメッセージへ単調増加する `seq` を付与する。また、ゲーム開始前の版・機能・ルール交渉、複数和了を含む原子的な局精算、遅延・重複応答の冪等処理、情報公開範囲、再同期および資源上限を規定する。

初期 profile は4人リーチ麻雀を対象とする。3人麻雀は本書の適合範囲外であり、別 profile の登録を必要とする。

## Status of This Memo

本書は YAMAI Project が管理する Standards Track Draft であり、IETF Internet Standard ではない。本書の配布に制限はない。

本書は実装および相互運用試験を目的とする Draft である。wire上のProtocol Versionは文書版と同じ `1.0-draft.5` とする。異なるdraft版は互換とみなしてはならない（MUST NOT）。安定版 `1.0` の割当ては、規範JSON Schema、registry、test vectorおよび2つ以上の独立した相互運用実装が公開された後に限る。

## Table of Contents

1. 状態、規約および要件語
2. 目的
3. プロトコルモデル
4. JSON と transport
5. 共通 envelope
6. 版・機能交渉
7. `riichi-4p` profile
8. 行動要求
9. `ack` と timeout
10. イベント順序
11. visibility と mode
12. エラー
13. 再接続と snapshot
14. 拡張
15. 資源・安全要件
16. MJAI からの移行
17. 適合性
18. Security Considerations
19. Registry Considerations
20. Normative References
21. Informative References
Appendix A. セッション状態機械
Appendix B. 最小交換例

## 1. 状態、規約および要件語

- Protocol name: `yamai`
- Version: `1.0-draft.5`
- Date: 2026-08-30
- Initial profile: `riichi-4p`
- Serialization: JSON
- Transports: JSON Lines, WebSocket text frame

本書は相互運用実験のためのドラフトである。draft版の文法は `MAJOR.MINOR-draft.REVISION` とする。draft同士は完全一致する場合に限り交渉可能である。安定版は `MAJOR.MINOR` とし、同一major内のminor互換性は第14節に従う。

### 1.1 Requirements Language

本書の **MUST**、**MUST NOT**、**REQUIRED**、**SHALL**、**SHALL NOT**、**SHOULD**、**SHOULD NOT**、**RECOMMENDED**、**NOT RECOMMENDED**、**MAY** および **OPTIONAL** は、すべて大文字で表記される場合に限り、[BCP 14] の意味で解釈しなければならない。

### 1.2 データモデル上の表記

`member` は JSON object の name/value pair、`message` は1個の top-level JSON object、`event` はホストが確定した状態変更、`action` はプレイヤーが選択した候補を意味する。

表における「必須」は当該 member が存在しなければならないことを表す。「任意」は省略可能であることを表す。明示的に許可されない `null` は、member の欠落と同値ではない。

数式中の配列添字は0始まりとする。時刻・期間の単位は member 名に `_ms` がある場合は millisecond とする。

## 2. 目的

YAMAI は次の性質を保証する対局プロトコルである。

1. ホストとプレイヤーが同じプロトコル版・ルールを理解している
2. 各行動が、どの要求への回答か一意に判定できる
3. 遅延・重複した行動が別の局面へ適用されない
4. クライアントが合法手を独自推測せず選択できる
5. すべての状態変更を順序付きで再現できる
6. 複数和了を含む局結果が原子的に確定する
7. 不正入力や切断で安全に失敗できる

ホストは、牌山、ルール、合法性、競合解決および点数計算の最終的な権威である。

## 3. プロトコルモデル

YAMAI は message を次の `kind` に分類する。

| `kind` | 方向 | 状態変更 | 応答 |
|---|---|---:|---|
| `hello` | Host → Player | しない | `join` |
| `join` | Player → Host | しない | `welcome` または `error` |
| `welcome` | Host → Player | セッション確立 | 不要 |
| `event` | Host → Player | する | 不要 |
| `request` | Host → Player | しない | `action` を1個 |
| `action` | Player → Host | ホスト受理後にのみする | `ack` |
| `ack` | Host → Player | action 結果を確定 | 不要 |
| `error` | 双方向 | 原則しない | fatal なら切断 |
| `snapshot` | Host → Player | 状態を置換 | 不要 |

プレイヤーは `request` を受信した場合にだけ `action` を送信しなければならない（MUST）。`event` への `none` 応答を送信してはならない（MUST NOT）。

## 4. JSON と transport

### 4.1 JSON 共通要件

message は [RFC 8259] の JSON object でなければならない（MUST）。

- 文字コードは UTF-8（MUST）
- BOM は禁止（MUST NOT）
- top-level は object（MUST）
- 重複キーは受信時に拒否（MUST）
- 整数は `-(2^53-1)` 以上 `2^53-1` 以下（MUST）
- `NaN`、`Infinity`、コメント、末尾カンマは禁止（MUST NOT）
- 1メッセージの既定上限は 1 MiB（UTF-8 JSON payloadのみ。JSONLのCR/LFおよびWebSocketのframe headerは含めない）（MUST）
- JSON の最大ネスト深さは64（MUST）
- 未知の標準フィールドは拒否（MUST）。`x_<owner>_<name>` 形式の拡張フィールドだけは、第14節の条件を満たす場合に限り無視してよい（MAY）
- 未知の `kind`、`type`、必須 capability は拒否（MUST）

各endpointは、自身が受信可能な上限を `hello.receive_limits` または `join.receive_limits` で通知しなければならない（MUST）。送信者はpeerの `receive_limits` を超えるmessageを送信してはならない（MUST NOT）。`max_message_bytes` は65,536以上1,048,576以下、`max_json_depth` は16以上64以下、`max_unresolved_requests` は1以上4以下でなければならない（MUST）。`riichi-4p` profileは `max_unresolved_requests >= 4` を要求し、提示値を満たせないendpointは、ゲーム開始前に `unsupported_limit` で拒否しなければならない（MUST）。

### 4.2 JSON Lines transport

フレーム文法を [RFC 5234] の ABNF で次のように定義する。[RFC 8259] の `JSON-text` は末尾の空白にCR/LFを含み得るため、行全体には直接使用せず、同RFCの `object` productionと、CR/LFを含まない `WSP`だけを使用する。JSON string内の改行はエスケープされた文字列として扱う。

```abnf
YAMAI-line = object *WSP [CR] LF
CR         = %x0D
LF         = %x0A
```

- 1メッセージを1行へ UTF-8 JSON として送信する（MUST）
- 行末は LF (`0x0A`)（MUST）
- 受信側は CRLF も受理してよい（MAY）
- JSON text 内の改行は JSON string の内外を問わず禁止し、string 内で必要な改行はエスケープする（MUST）
- 空行は無視せず `invalid_frame` とする（MUST）
- 送信側は各メッセージを flush する（MUST）
- 受信側は区切り後に先読みした byte を次のフレーム用に保持する（MUST）

TCP と標準入出力は同じフレーミングを使用できる（MAY）。TCP の接続寿命はゲームの寿命と独立であり、`end_game` だけを理由に切断する必要はない。

### 4.3 WebSocket transport

- endpoint はWebSocket subprotocol `yamai.1.draft5` を交渉する（MUST）
- 1 text message は1個の YAMAI message だけを含む（MUST）
- 送信者は [RFC 6455] に従って text message を複数 frame へ分割できる（MAY）
- 受信者は分割された frame を完全な text message へ再構成してから JSON を解析する（MUST）
- binary message は `unsupported_frame` として拒否する（MUST）
- WebSocket の message boundary を YAMAI の message boundary とする（MUST）

### 4.4 transport 非依存性

transport は message の意味を変更してはならない（MUST NOT）。batch transport は1フレームへ複数 YAMAI message を格納してはならない（MUST NOT）。複数 message はそれぞれ独立したフレームとして連続送信する。

## 5. 共通 envelope

`welcome` 完了後、ホストから送る `event`、`request`、`ack`、`error` および `snapshot` は次の共通memberを持たなければならない（MUST）。`hello`、`join` および `welcome` は交渉messageであり、このenvelopeの対象外である。

```json
{
  "yamai": "1.0-draft.5",
  "kind": "event",
  "session_id": "s_01J6...",
  "game_id": "g_01J6...",
  "seq": 42,
  "event": {"type": "dahai", "actor": 2, "pai": "7s", "tsumogiri": true}
}
```

| フィールド | 要件 | 意味 |
|---|---|---|
| `yamai` | 必須 | 選択された完全なProtocol Version |
| `kind` | 必須 | メッセージ種別 |
| `session_id` | 必須 | 接続を越えて再開可能なセッションID |
| `game_id` | 必須 | 一意なゲームID |
| `seq` | 必須 | セッション内で1から単調に1増加する番号 |
| `original_seq` | replay eventでは必須、それ以外は禁止 | replay元記録のhost message seq。replay sessionの振り直し前の番号 |

ID は64文字以下の ASCII `[A-Za-z0-9._:-]` でなければならない（MUST）。ID の内部構造を受信側が解釈してはならない（MUST NOT）。

プレイヤーは受信済みの最大 `seq` を保持しなければならない（MUST）。`seq` が期待値より大きい場合、当該messageを破棄し、`expected_seq` と `received_seq` を持つ `sequence_gap` errorを送信しなければならない（MUST）。ホストが同じ `seq` を再送する場合、元のmessageとbyte-for-byteで同一でなければならない（MUST）。同一の再送は無視できる（MAY）。同じ `seq` で内容が異なる場合はfatal `sequence_conflict` とする（MUST）。

`sequence_gap` を受信したホストは、`expected_seq` から送信済みの最新 `seq` までの全messageを元の番号と内容で再送しなければならない（MUST）。一部だけを再送してはならない（MUST NOT）。再送できず `snapshot` capability が有効なら、第13.3節のsnapshotを送信できる（MAY）。いずれも不可能な場合、ホストはfatal `resume_unavailable`でsessionを終了しなければならない（MUST）。

## 6. 版・機能交渉

### 6.1 `hello`

```json
{
  "kind": "hello",
  "protocol": "yamai",
  "versions": ["1.0-draft.5"],
  "profiles": [
    {
      "name": "riichi-4p",
      "revisions": ["1.0-draft.3"],
      "hashes": {"1.0-draft.3": "sha256:140a9b6d4d962799bb2bf2bc5dcdc2fa9ee88cc64374c930d0c3fe84ea749fb8"}
    }
  ],
  "capabilities": {"required": [], "optional": ["resume", "snapshot"]},
  "receive_limits": {"max_message_bytes": 1048576, "max_json_depth": 64, "max_unresolved_requests": 4}
}
```

### 6.2 `join`

```json
{
  "kind": "join",
  "version": "1.0-draft.5",
  "mode": "play",
  "view": "seat",
  "profile": "riichi-4p",
  "profile_revision": "1.0-draft.3",
  "profile_hash": "sha256:140a9b6d4d962799bb2bf2bc5dcdc2fa9ee88cc64374c930d0c3fe84ea749fb8",
  "client": {"name": "ExampleAI", "version": "2.3.0"},
  "capabilities": {"required": [], "optional": ["resume", "snapshot"]},
  "receive_limits": {"max_message_bytes": 1048576, "max_json_depth": 64, "max_unresolved_requests": 4},
  "room": "default"
}
```

クライアントは `hello.versions` に存在する版を1個選ばなければならない（MUST）。draft版は文字列が完全一致しなければならない（MUST）。安定版でmajorが異なる版へ暗黙にdowngradeしてはならない（MUST NOT）。

`hello.profiles` は profile 名ごとの対応revisionと、そのrevisionに対応する `sha256:` 付き64桁小文字hexのhashを提示する。`revisions` と `hashes` のキー集合は一致しなければならない（MUST）。hashは、リポジトリの `test-vectors/yrc-0003/1.0-draft.5/manifest.json` の `profile_hash_inputs` に列挙されたJSON文書を読み込み、`profile_schema`、`rules_schema`、`scoring_vectors_schema`、`yrc0003_registry`、`yrc0005_registry`、`official_vectors` および `scoring_vectors` という7 memberのobjectへ投影した値を対象とする。YRC 0003 registryのprofile `hash` memberは対象objectから除外し、全JSON文書の member 名が `profile_hash` である値は `sha256:` + 64個のASCII `0` へ正規化する。対象objectは [RFC 8785] JSON Canonicalization Scheme (JCS) で直列化し、UTF-8 byte列へSHA-256を適用する（MUST）。数値、Unicode escape、キー順および空白の扱いをJCS以外の方法で実装してはならない（MUST NOT）。`join.profile_revision` と `join.profile_hash` は同じ広告済み組を選択し、`hello` に存在しなければならない（MUST）。`welcome` は選択結果をそのまま返さなければならない（MUST）。revisionまたはhashが一致しない場合、ホストは `profile_mismatch` で拒否しなければならない（MUST）。

`capabilities` は `required` と `optional` の2配列を持たなければならない（MUST）。重複および両配列への同一値の記載は禁止する（MUST NOT）。`required` はpeerが理解しない場合に交渉を拒否する機能、`optional` は両者が提示した場合だけ有効になる機能である。両者の `required` はpeerの `required` または `optional` に含まれなければならず（MUST）、満たせない場合は `unsupported_capability` で拒否する。未知のexperimental capabilityは `optional` なら無視できるが、`required` なら拒否しなければならない。安定 capability は小文字 snake case、実験用 capability は `x-<owner>-<name>` とする。

新規sessionを要求する `join` は `resume` memberを省略する。再開は `play` modeだけで許可し、`resume` を伴う場合は `target` を省略し、tokenが対象gameとseatを識別する（MUST）。`mode` は `play`、`spectate` または `replay` のいずれかであり、`view` は `play` では文字列 `seat`、`spectate` では文字列 `public`、`replay` では文字列 `public`、`full` または `{ "seat": N }` とする（MUST）。`play` では `target` を指定してはならない（MUST NOT）。`spectate` の `target` は `{ "type": "game", "id": <game_id> }`、`replay` の `target` は `{ "type": "game", "id": <game_id> }` または `{ "type": "recording", "id": <recording_id> }` の一方を必須とする（MUST）。`target.type` を省略してはならない（MUST NOT）。

```json
{
  "kind": "join",
  "version": "1.0-draft.5",
  "mode": "play",
  "view": "seat",
  "profile": "riichi-4p",
  "profile_revision": "1.0-draft.3",
  "profile_hash": "sha256:140a9b6d4d962799bb2bf2bc5dcdc2fa9ee88cc64374c930d0c3fe84ea749fb8",
  "client": {"name": "ExampleAI", "version": "2.3.0"},
  "capabilities": {"required": [], "optional": ["resume", "snapshot"]},
  "receive_limits": {"max_message_bytes": 1048576, "max_json_depth": 64, "max_unresolved_requests": 4},
  "resume": {"token": "rt_01J6...", "last_seq": 120}
}
```

`resume.last_seq` はクライアントが完全に適用した最後のhost messageである。未適用または部分適用したmessageの番号を指定してはならない（MUST NOT）。

### 6.3 `welcome`

```json
{
  "yamai": "1.0-draft.5",
  "kind": "welcome",
  "session_id": "s_01J6...",
  "game_id": "g_01J6...",
  "resumed": false,
  "resume": {"token": "rt_01J6...", "expires_in_ms": 600000},
  "seat": 0,
  "mode": "play",
  "view": "seat",
  "profile": "riichi-4p",
  "profile_revision": "1.0-draft.3",
  "profile_hash": "sha256:140a9b6d4d962799bb2bf2bc5dcdc2fa9ee88cc64374c930d0c3fe84ea749fb8",
  "players": [
    {"seat": 0, "name": "ExampleAI"},
    {"seat": 1, "name": "BotB"},
    {"seat": 2, "name": "BotC"},
    {"seat": 3, "name": "BotD"}
  ],
  "scores": [25000, 25000, 25000, 25000],
  "rules": {
    "game_length": "tonnan",
    "starting_points": 25000,
    "extension": {"mode": "sudden_death", "target_points": 30000, "max_extra_rounds": 4},
    "ranking_policy": "initial_seat_order",
    "red_fives": {"m": 1, "p": 1, "s": 1},
    "kuitan": true,
    "ron_policy": "double_only",
    "reaction_priority": "hora_call_chi",
    "multiple_ron_settlement": {"honba": "each_winner", "kyotaku": "first_winner"},
    "bankruptcy": "end_game",
    "bankruptcy_threshold": 0,
    "dealer_continuation": {"win": true, "tenpai_draw": true},
    "abortive_draw_continuation": true,
    "agariyame": true,
    "noten_payment": {"total_points": 3000, "unit": 100, "remainder": "lowest_seat"},
    "riichi_stick_value": 1000,
    "honba_ron_value": 300,
    "honba_tsumo_value_per_payer": 100,
    "kiriage_mangan": false,
    "kazoe_yakuman": "yakuman",
    "double_yakuman": ["kokushi_13_wait", "suuankou_tanki", "junsei_chuuren", "daisuushii"],
    "pao": {"yakus": ["daisangen", "daisuushii"], "ron": "split", "tsumo": "liable_all"},
    "chombo": {"penalty_points": 8000, "distribution": "equal_other_players", "remainder": "lowest_seat"},
    "ankan_chankan": "kokushi_only",
    "kan_dora_timing": {"ankan": "before_rinshan", "daiminkan": "after_rinshan_discard", "kakan": "after_rinshan_discard"},
    "invalid_action_policy": "reject",
    "time_control": {"grace_ms": 3000, "bank_ms": 15000, "bank_scope": "kyoku"},
    "abortive_draws": ["kyushukyuhai", "suufon_renda", "suucha_riichi", "suukan_sanra", "sanchaho"],
    "local_yaku": []
  }
}
```

`welcome` は交渉結果であり `seq` を持ってはならない（MUST NOT）。`welcome.mode`、`welcome.view` および `welcome.seat` は `join` の要求と一致しなければならない（MUST）。`resumed == false` の場合 `replay_from_seq` を含めてはならず（MUST NOT）、`resumed == true` の場合 `replay_from_seq` と `resume` を必須とする（MUST）。`spectate` または `replay` では `welcome.game_id` が tagged `join.target` のgameを識別しなければならない（MUST）。新規sessionでは `resumed` を `false` とし、最初のenveloped host messageの `seq` を1とする。再開成功時は `resumed` を `true`、`replay_from_seq` を `join.resume.last_seq + 1` とし、同じ `session_id` と `game_id` を返さなければならない（MUST）。ホストは `replay_from_seq` から全messageを再送するか、第13.3節のsnapshotを送信する。

`resume` capabilityが有効な場合、ホストは `welcome.resume.token` を毎回新しい値へrotateしなければならない（MUST）。`expires_in_ms` は `welcome` 送信完了からの有効期間である。再開に失敗した場合、ホストは交渉用fatal `resume_unavailable` を返し、新規sessionへ暗黙にfallbackしてはならない（MUST NOT）。`resume` capabilityが無効なjoinに `resume` memberがある場合も、ホストは `resume_unavailable` で拒否しなければならない（MUST）。

`rules` のすべての member は profile の Schema で意味を定義しなければならない（MUST）。profile が必須とする member を省略してはならない（MUST NOT）。クライアントは理解できない必須ルール値を `unsupported_rules` で拒否しなければならない（MUST）。

## 7. `riichi-4p` profile

役、役満condition、ドラbonus、符および基本点の意味論は [YRC 0005] に従わなければならない（MUST）。本書と [YRC 0005] を組み合わせて1個の `riichi-4p` 規範profileを構成する。

### 7.1 座席

座席は整数 `0` から `3` である。座席はゲーム中固定し、seat-indexed array は座席順とする。親は event ごとの `oya` で表す。

### 7.2 必須ルール

`riichi-4p` の `rules` は次の member をすべて持たなければならない（MUST）。既定値による省略を禁止する（MUST NOT）。

`riichi-4p` profileのendpointは `max_message_bytes >= 1,048,576`、`max_json_depth >= 64` および `max_unresolved_requests >= 4` を受信可能でなければならない（MUST）。`join.receive_limits` がこの下限を満たさない場合、ホストは `unsupported_limit` で交渉を拒否しなければならない（MUST）。完全な `legal_actions` またはsnapshotをpeer上限に合わせて切り詰めてはならない（MUST NOT）。

| キー | 型・値 | 意味 |
|---|---|---|
| `game_length` | `tonpu`, `tonnan` | 規定のゲーム長 |
| `starting_points` | 非負整数 | 開始点 |
| `extension` | `{mode,target_points,max_extra_rounds}` | 規定局後の延長。`mode` は `none` または `sudden_death`、`max_extra_rounds` は0～100 |
| `ranking_policy` | `initial_seat_order` | 同点時は小さい絶対seatを上位とする |
| `red_fives` | `m,p,s` ごとの0～4 | 各色の赤五枚数 |
| `kuitan` | boolean | 喰いタン |
| `ron_policy` | `multiple`, `head_bump`, `double_only` | 全ロン、頭ハネ、または二家和まで許可し三家和を途中流局とする |
| `reaction_priority` | `hora_call_chi` | 和了、daiminkan/pon、chi、noneの固定優先順 |
| `multiple_ron_settlement` | `{honba,kyotaku}` | 複数ロン時の本場・供託。下記規則に従う |
| `bankruptcy` | `continue`, `end_game` | トビ終了 |
| `bankruptcy_threshold` | 整数 | `scores < threshold` をトビとする境界 |
| `dealer_continuation` | `{win,tenpai_draw}` | 親和了・流局聴牌時の連荘 |
| `abortive_draw_continuation` | boolean | 途中流局時の連荘 |
| `agariyame` | boolean | オーラス親のアガリ止め |
| `noten_payment` | `{total_points,unit,remainder}` | 通常流局時に授受する点の総額と100点単位の配分 |
| `riichi_stick_value` | 非負整数 | リーチ供託額 |
| `honba_ron_value` | 非負整数 | ロン時の1本場加算 |
| `honba_tsumo_value_per_payer` | 非負整数 | ツモ時に各支払者が加算する1本場額 |
| `kiriage_mangan` | boolean | 切り上げ満貫 |
| `kazoe_yakuman` | `yakuman`, `sanbaiman` | 数え役満の上限 |
| `double_yakuman` | condition IDの配列 | ダブル役満として扱う形。空配列なら全役満を単倍とする |
| `pao` | `{yakus,ron,tsumo}` | 対象役と責任払い。`ron` は `split`/`liable_all`、`tsumo` は `liable_all`/`normal` |
| `ankan_chankan` | `never`, `kokushi_only` | 暗槓への槍槓 |
| `kan_dora_timing` | `{ankan,daiminkan,kakan}` | 各槓の公開時点。値は `before_rinshan` または `after_rinshan_discard` |
| `invalid_action_policy` | `reject`, `default`, `chombo` | 不正 action への対局上の処置 |
| `chombo` | `{penalty_points,distribution,remainder}` | `chombo`時の総点数、他家への配分、端数席 |
| `time_control` | `{grace_ms,bank_ms,bank_scope}` | 猶予時間、持ち時間、`bank_scope` は `kyoku` または `game` |
| `abortive_draws` | reason ID の配列 | 採用する途中流局 |
| `local_yaku` | yaku ID の配列 | 合意済みローカル役。通常は空 |

`local_yaku` に未知の値があるクライアントは接続を拒否しなければならない（MUST）。合法手だけを選択するプレイヤーであっても、和了判断と期待値計算が変化するため、未知の値を無視してはならない（MUST NOT）。

`time_control.grace_ms` と `time_control.bank_ms` は0以上600,000以下の整数でなければならない（MUST）。`grace_ms` は全requestに共通する非課金の猶予であり、time bankを消費しない。requestのdeadlineは、requestの送信完了時刻に `grace_ms + timeout_ms + time_bank_ms` を加えた時刻である。`bank_scope == "kyoku"` では `start_kyoku` ごと、`bank_scope == "game"` では `start_game` ごとに bank を reset する。

`starting_points`、`extension.target_points`、`bankruptcy_threshold`、`riichi_stick_value`、`honba_ron_value`、`honba_tsumo_value_per_payer` および `chombo.penalty_points` は100の倍数でなければならない（MUST）。`noten_payment.total_points` は600の倍数、`noten_payment.unit` は100、`noten_payment.remainder` は `lowest_seat` でなければならない（MUST）。

`extension.mode == "none"` の場合、`max_extra_rounds` は0でなければならない（MUST）。`sudden_death` の場合、規定最終局の終了時に最高点が `target_points` 未満なら、`max_extra_rounds` を上限として局を延長する。延長局数は `start_kyoku.extension_round` で表し、通常局は0、最初の延長局は1とし、`max_extra_rounds` を超えてはならない（MUST）。同点順位は常に `ranking_policy` で決定し、`end_game.rankings` はその結果と一致しなければならない（MUST）。

`ron_policy == "head_bump"` では、候補のうち `(actor - target + 4) mod 4` が最小の和了者だけを採用する。`multiple` では全和了者を採用する。`double_only` では二家和まで採用し、三家和は `sanchaho` として流局にする。`double_only` では `abortive_draws` に `sanchaho` を含め、それ以外では含めてはならない（MUST）。

複数ロンの `first_winner` は `(actor - target + 4) mod 4` が最小の和了者とする。`multiple_ron_settlement.honba` は `each_winner` または `first_winner` である。前者は各winへ本場を加算し、後者はfirst winnerだけへ加算する。`kyotaku` は `first_winner` または `equal_split` である。`kyotaku` は供託本数で表し、点数は `kyotaku × riichi_stick_value` とする。`equal_split` では供託点を100点単位で等分し、除算の余りをfirst winnerへ加算する。未配分の供託本数はなく、`next.kyotaku` は配分後の本数でなければならない（MUST）。各winの `deltas` はこの配分と一致しなければならない（MUST）。

`pao.yakus` は責任払いの対象役を列挙する。責任seatが決定された時点で、ホストは `pao` eventを記録しなければならない（MUST）。`pao.ron == "liable_all"` では責任seatが全額を支払う。`split` では責任seatの支払額を `ceil(hand_points / 2, 100)`、放銃seatの支払額を `hand_points - 責任seat支払額` とする。両seatが同じなら全額をそのseatが支払う。`pao.tsumo == "liable_all"` では責任seatが全額を支払い、`normal` では通常のツモ支払いとする。本場は和了点と同じ比率で同じ支払者へ配分し、供託は支払者と独立して和了者へ付与する。複数winでは各winの支払を独立に展開し、同一支払者の合算後もtop-level `deltas` と一致させる。ここで `ceil(x,100)` はx以上の最小の100の倍数である。

`noten_payment.total_points` は通常流局で授受する総点数である。聴牌者数を `t` とし、`total_points` は600の倍数でなければならない（MUST）。`0 < t < 4` の場合、各seatの純差額は、`t==1` なら聴牌者 `+total_points`・各不聴者 `-total_points/3`、`t==2` なら各聴牌者 `+total_points/2`・各不聴者 `-total_points/2`、`t==3` なら各聴牌者 `+total_points/3`・不聴者 `-total_points` とする。`t==0` または `t==4` の場合は全seatの差額を0とする。配分はseat単位の純差額で表し、個別seat間の支払明細を要求してはならない（MUST）。

`chombo.penalty_points` はchomboで移動する総点数であり、`distribution == "equal_other_players"` の場合はoffenderから他の3 seatへ100点単位で等分し、端数は `remainder == "lowest_seat"` で最小seatへ加算する。ホストは支払元・支払先・点数を `result.penalty.payments` に記録し、top-level `deltas` はその合計と一致させなければならない（MUST）。

局終了後、ホストは次の順序で `next` を決定しなければならない（MUST）。

1. `deltas` を適用して確定 `scores` を得る。
2. `bankruptcy == "end_game"` かついずれかのscoreが `bankruptcy_threshold` 未満なら、他の条件に優先して `next.type = "end_game"` とする。
3. 親が和了し `dealer_continuation.win` がtrue、通常流局で親が聴牌し `tenpai_draw` がtrue、または途中流局で `abortive_draw_continuation` がtrueなら `dealer_continues = true` とする。
4. 規定最終局より前では、`dealer_continues` なら `renchan`、それ以外なら `rotate` とする。
5. 規定最終局以後で `dealer_continues` かつ `agariyame` がtrue、親がrank 1、親scoreが `extension.target_points` 以上なら `end_game` とする。それ以外で親が続行する場合は `renchan` とする。
6. 規定最終局以後で親が続行しない場合、最高scoreがtarget以上なら `end_game` とする。target未満かつ `extension.mode == "sudden_death"` で使用済み延長局数が `max_extra_rounds` 未満なら `rotate`、それ以外なら `end_game` とする。

`game_length == "tonpu"` の規定最終局は東4局、`tonnan` は南4局である。途中流局では本場を1増加する。通常流局は親の聴牌にかかわらず本場を1増加する。和了時は連荘なら本場を1増加し、親流れなら0へ戻す。

`abortive_draws` の初期reasonは次の条件で成立する。conditionを満たしても一覧に含まれないreasonを宣言してはならない（MUST NOT）。

`fanpai` はlive wallが0枚となり、最後の自摸および打牌に和了がなく、途中流局も成立しない通常流局である。`fanpai` は `abortive_draws` に含めない。

- `kyushukyuhai`: 自分の最初の自摸後、他家を含め鳴きがなく、自分の手牌に異なる么九牌が9種以上ある場合に、そのplayerが `ryukyoku` actionを選択する。
- `suufon_renda`: 鳴きのない第一巡で4人の最初の打牌が同一の風牌であり、4枚目にロンがない。
- `suucha_riichi`: 4人全員の `reach_accepted` が成立し、4人目のリーチ打牌にロンがない。
- `suukan_sanra`: 卓上の成立済み槓が4回に達し、4回全てが同一playerによるものではなく、4回目の槓への槍槓およびその嶺上打牌への和了がない。
- `sanchaho`: `ron_policy == "double_only"` で同一打牌または加槓に3人が和了可能である。

この一覧で表現できない必須ルールは、交渉済み capability と namespaced rule key を使用しなければならない（MUST）。状態または点数へ影響するルールを、説明文だけで追加してはならない（MUST NOT）。

### 7.3 牌

公開牌は MJAI と同じ文字列表記を使用する。

```text
1m..9m  1p..9p  1s..9s
E S W N P F C
5mr 5pr 5sr
```

非公開牌を文字列 `?` で表してはならない（MUST NOT）。単一の非公開牌は JSON `null`、非公開手牌は `{"count": 13}` で表さなければならない（MUST）。

```json
{
  "hands": [
    {"tiles": ["1m", "2m", "3m", "5pr", "E", "E", "E", "4s", "5s", "6s", "7p", "8p", "9p"]},
    {"count": 13},
    {"count": 13},
    {"count": 13}
  ]
}
```

action 内の牌は公開牌文字列でなければならない（MUST）。赤五と通常五は異なる牌として比較し、面子の牌種を比較する場合に限り同じ五として扱う。

### 7.4 event

`event` は状態を1回だけ変更する。プレイヤーは `seq` 順に event を適用しなければならない（MUST）。

本節の event 表および例は、共通 envelope の `event` member の値を示す。wire message は第5節の envelope で包まなければならない（MUST）。

| event `type` | 必須フィールド | 説明 |
|---|---|---|
| `start_game` | `players`, `rules`, `scores` | ゲーム開始。`welcome` と同値なゲーム情報を記録用に含む |
| `start_kyoku` | `bakaze`, `kyoku`, `honba`, `kyotaku`, `oya`, `extension_round`, `dora_marker`, `scores`, `hands` | 局開始 |
| `tsumo` | `actor`, `pai` | ツモ。非行動者の view では `pai:null` |
| `dahai` | `actor`, `pai`, `tsumogiri` | 打牌 |
| `chi` | `actor`, `target`, `pai`, `consumed[2]` | チー成立 |
| `pon` | `actor`, `target`, `pai`, `consumed[2]` | ポン成立 |
| `daiminkan` | `actor`, `target`, `pai`, `consumed[3]` | 大明槓成立 |
| `ankan_declared` | `actor`, `consumed[4]` | 暗槓宣言。槍槓判断前で、面子は未確定。`consumed` はplay viewではactorだけが牌種を受け取り、他viewでは第11節の投影規則に従う |
| `ankan` | `actor`, `consumed[4]` | 暗槓成立。`consumed` の公開範囲はprofileのview投影で固定する |
| `kakan_declared` | `actor`, `pai`, `consumed[3]` | 加槓宣言。槍槓判断前で、既存ポンは未変更 |
| `kakan` | `actor`, `pai`, `consumed[3]` | 槍槓がなかった加槓の成立 |
| `dora` | `dora_marker` | ドラ表示牌追加 |
| `reach` | `actor` | リーチ宣言 |
| `reach_accepted` | `actor`, `deltas`, `scores`, `kyotaku` | リーチ成立・供託反映 |
| `pao` | `actor`, `yaku_id`, `liable_seat` | 役ごとの責任払いseat決定履歴 |
| `end_kyoku` | `result`, `deltas`, `scores`, `next` | 局結果を原子的に確定 |
| `end_game` | `scores`, `rankings`, `kyotaku` | ゲーム終了。未配分供託本数も確定 |

`scores` と `deltas` は4要素でなければならない（MUST）。点数変更 event では、各座席について `new_scores[i] == old_scores[i] + deltas[i]` が成立しなければならない（MUST）。

点数変更の全体保存則は、供託を含めて `sum(new_scores) + new_kyotaku * riichi_stick_value == sum(old_scores) + old_kyotaku * riichi_stick_value` としなければならない（MUST）。`old_kyotaku` と `new_kyotaku` は当該eventまたは直前に確定した局状態から取得する。`reach_accepted` ではactorの `deltas` による控除と `kyotaku` の1本増加を同時に検証し、`end_kyoku` では配分した供託をwinの `deltas` に一度だけ含める。paoを含む各winは、profileの支払式または明示されたpaymentへ展開できなければならず、点数の発行・消滅・二重計上を許可してはならない（MUST NOT）。

`start_kyoku.kyotaku`、`reach_accepted.kyotaku`、`end_kyoku.next.kyotaku` および `end_game.kyotaku` は供託本数であり、1本の点数は `rules.riichi_stick_value` である。`reach_accepted` はactorから `riichi_stick_value` を1本分控除し、kyotakuを1増やす。和了時に配分した供託点はwinの `deltas` に含め、配分後の残本数を `next.kyotaku` へ繰り越す。和了者がいない場合も供託を失わせず、次局または `end_game.kyotaku` へ繰り越さなければならない（MUST）。

`ankan_declared` と `kakan_declared` は pending 状態を開始する。`ankan` または `kakan` は面子を確定し、`end_kyoku` は pending 状態を破棄する。宣言 event だけを根拠として副露を確定してはならない（MUST NOT）。

`pao` eventは責任払いの決定履歴であり、`actor` は対象役を成立させるseat、`yaku_id` は `rules.pao.yakus` の値、`liable_seat` は責任seatである。`liable_seat` は `actor` と異なるseatでなければならず（MUST）、和了時の `result.wins[].pao` はそれまでの全 `pao` eventの `yaku_id` → `liable_seat` 対応と一致しなければならない（MUST）。責任seatを推測させるだけの自由記述を送信してはならない（MUST NOT）。

`end_game.rankings` はseat-indexedな4要素の整数arrayであり、値 `1,2,3,4` を重複なく1回ずつ含まなければならない（MUST）。高い `scores` を持つseatを上位とし、同点は `rules.ranking_policy` で解決する。`end_game.scores` は最終の実点であり、未配分の供託は `end_game.kyotaku` に本数で報告し、scoresまたはrankingsへ暗黙に加算してはならない（MUST NOT）。`end_game` 後に同じ `game_id` のeventまたはrequestを送信してはならない（MUST NOT）。

`start_game.players`、`start_game.rules` および `start_game.scores` は、同じsessionの `welcome` に含まれる値とJSONのobject member順を除いて同値でなければならない（MUST）。不一致を受信したクライアントはfatal `invalid_message` としてsessionを終了しなければならない（MUST）。

### 7.5 `end_kyoku`

ホストは、和了が1件か複数かにかかわらず、局内の全和了を1個の `end_kyoku` にまとめなければならない（MUST）。

```json
{
  "type": "end_kyoku",
  "result": {
    "type": "hora",
    "wins": [
      {
        "actor": 0,
        "target": 2,
        "pai": "7s",
        "fu": 40,
        "han": 4,
        "yakus": [{"id": "reach", "value": 1, "unit": "han"}],
        "bonuses": [{"id": "dora", "han": 2}, {"id": "uradora", "han": 1}],
        "ura_dora_markers": ["4p"],
        "pao": [],
        "hand_points": 12000,
        "deltas": [12000, 0, -12000, 0]
      },
      {
        "actor": 1,
        "target": 2,
        "pai": "7s",
        "fu": 30,
        "han": 2,
        "yakus": [{"id": "tanyao", "value": 1, "unit": "han"}],
        "bonuses": [{"id": "akadora", "han": 1}],
        "ura_dora_markers": [],
        "pao": [],
        "hand_points": 2000,
        "deltas": [0, 2000, -2000, 0]
      }
    ]
  },
  "deltas": [12000, 2000, -14000, 0],
  "scores": [37000, 27000, 11000, 25000],
  "next": {"type": "renchan", "honba": 1, "oya": 0, "kyotaku": 0, "extension_round": 0}
}
```

流局の表現例を次に示す。

```json
{
  "type": "end_kyoku",
  "result": {
    "type": "ryukyoku",
    "reason": "fanpai",
    "tenpai": [true, false, false, true]
  },
  "deltas": [1500, -1500, -1500, 1500],
  "scores": [26500, 23500, 23500, 26500],
  "next": {"type": "renchan", "honba": 1, "oya": 0, "kyotaku": 0, "extension_round": 0}
}
```

`next.type` は `renchan`、`rotate` または `end_game` のいずれかでなければならない（MUST）。局継続判断を `wins` の順序から推測してはならない（MUST NOT）。

`end_kyoku` は次の member を持たなければならない（MUST）。

| member | 型 | 制約 |
|---|---|---|
| `type` | string | `end_kyoku` |
| `result` | object | 下記 result variant の1個 |
| `deltas` | integer[4] | 局全体の seat-indexed 点差 |
| `scores` | integer[4] | `previous_scores[i] + deltas[i]` |
| `next` | object | `type`、`honba`、`oya`、`kyotaku`、`extension_round` を持つ。`end_game` ではこれらを省略 |

`result.type == "hora"` の場合、`result.wins` は1個以上3個以下の win object を持たなければならない（MUST）。各 win object は次の member を持つ。

| member | 型 | 制約 |
|---|---|---|
| `actor` | seat | 和了者 |
| `target` | seat | ツモでは `actor` と同じ、ロンでは放銃者 |
| `pai` | tile | 和了牌 |
| `fu` | 非負整数 | 適用ルールで計算した符 |
| `han` | 非負整数 | 役満を除く合計飜。役満だけなら0 |
| `yakus` | array | 登録済み yaku ID、`value`、`unit` (`han` または `yakuman`) |
| `bonuses` | array | 登録済みbonus IDと正の整数 `han`。該当なしなら空array |
| `hand_points` | 非負整数 | 本場・供託を除く和了点の総支払額 |
| `deltas` | integer[4] | 当該 win に割り当てた本場・供託を含む点差 |
| `ura_dora_markers` | tile[] | リーチ和了時の裏ドラ表示牌。リーチでない場合は空array |
| `pao` | object[] | `yaku_id` と責任払い `liable_seat` の対応。該当なしは空array |

`result.wins` は `actor` の昇順で整列しなければならない（MUST）。top-level `deltas` は全 win の `deltas` を要素ごとに加算した値と一致しなければならない（MUST）。供託と本場は `rules.multiple_ron_settlement` に従って割り当て、二重計上してはならない（MUST NOT）。

各 `wins[].pao` elementは `{ "yaku_id": <登録済みyaku ID>, "liable_seat": <seat> }` でなければならず（MUST）、同一 `yaku_id` を重複させてはならない。`wins[].ura_dora_markers` は当該winの和了時点で公開する裏ドラ表示牌を正確に列挙し、`reach_accepted` が成立していないwinでは空arrayでなければならない（MUST）。

役満でないwinでは、`han` は `yakus` の `unit == "han"` の `value` と全 `bonuses[].han` の合計に一致しなければならない（MUST）。役満winでは `han` を0、`bonuses` を空arrayとし、`unit == "yakuman"` の `value` 合計を役満倍数とする。ドラ、裏ドラおよび赤ドラは役ではなくbonusとして記録する。

`result.type == "ryukyoku"` の場合、`result.reason` は Result Reasons registry の値、`result.tenpai` は4要素の boolean arrayでなければならない（MUST）。途中流局で聴牌判定を行わない場合、`tenpai` は `null` とする。通常流局で `0 < t < 4` の場合、`deltas` は `rules.noten_payment.total_points` のseat単位の純差額規則に従い、各seatの受取または支払の純差額と一致しなければならない（MUST）。個別seat間の支払明細を要求してはならない。

`result.type == "penalty"` の場合、`result.offender`、登録済み `result.reason`、`result.penalty.payments` および top-level `deltas` を持たなければならない（MUST）。各paymentは `{from,to,points}` で、`from` は offender、`to` は他seat、合計とtop-level `deltas`は一致しなければならない。このvariantは `invalid_action_policy == "chombo"` またはprofileが登録した penalty ruleでのみ使用できる（MUST）。

## 8. 行動要求

### 8.1 `request`

```json
{
  "yamai": "1.0-draft.5",
  "kind": "request",
  "session_id": "s_01J6...",
  "game_id": "g_01J6...",
  "seq": 43,
  "request_id": "r_01J6...",
  "seat": 0,
  "caused_by_seq": 42,
  "timeout_ms": 3000,
  "time_bank_ms": 15000,
  "legal_actions": [
    {"action_id": "a0", "action": {"type": "dahai", "actor": 0, "pai": "3m", "tsumogiri": false}},
    {"action_id": "a1", "action": {"type": "dahai", "actor": 0, "pai": "7s", "tsumogiri": true}},
    {"action_id": "a2", "action": {"type": "hora", "actor": 0}}
  ],
  "default_action_id": "a1"
}
```

`request` は次の要件を満たさなければならない。

- `request_id` は `game_id` 内で一意である（MUST）。
- `seat` は受信 session の `welcome.seat` と一致する（MUST）。
- `caused_by_seq` は判断の原因となった、同じ session の適用済み event を参照する（MUST）。
- `legal_actions` は、その view で選択できる完全な集合であり、1個以上512個以下の要素を持つ（MUST）。
- 各 `legal_actions[].action.actor` は `seat` と一致する（MUST）。`none` だけは `actor` を省略できる（MAY）。
- `action_id` は request 内で一意な64文字以下のIDである（MUST）。
- `default_action_id` は `legal_actions` に存在する（MUST）。
- `timeout_ms` と `time_bank_ms` は0以上600,000以下の整数である（MUST）。
- 競合解決が必要な request は `decision_group_id`、`decision_group_members`、`decision_group_deadline_ms` および `decision_group_close` を持つ（MUST）。単独 decision の request はこれらを省略できる（MAY）。

同じ打牌または加槓に複数プレイヤーが反応する request は、同じ `decision_group_id` を持たなければならない（MUST）。`decision_group_members` は当該groupに属する全requestの `{request_id,seat}` の配列であり、group内の全requestで同一でなければならない（MUST）。`decision_group_deadline_ms` はgroup内で最初のrequestの送信完了時からの共通期限であり、各requestの個別deadline以上でなければならない。`decision_group_close` は本版では `all_resolved_or_deadline` 固定とする。ホストは当該 request を並列に発行できる（MAY）が、groupの全memberがterminalになるか共通期限へ達するまでstate eventを送信してはならない（MUST）。

同一seatに同時に存在できる未解決requestは1個だけであり（MUST）、1個の `decision_group_members` に同じseatを2回以上含めてはならない（MUST NOT）。これによりtime bankはseatごとの共有残量から一度だけ消費される。

### 8.2 `action`

```json
{
  "yamai": "1.0-draft.5",
  "kind": "action",
  "session_id": "s_01J6...",
  "game_id": "g_01J6...",
  "request_id": "r_01J6...",
  "action_id": "a2"
}
```

プレイヤーは `legal_actions` の `action_id` を1個だけ返さなければならない（MUST）。action object を再構築して送信してはならない（MUST NOT）。この規則は、`consumed` の順序、赤牌、既定値およびルール差による不一致を排除する。

pass が合法な場合、ホストは `none` action を候補に含めなければならない（MUST）。自摸番で打牌が必須の場合、`none` を含めてはならない（MUST NOT）。

### 8.3 複合行動

リーチとその打牌、ならびにチー・ポンと直後の打牌は、単一の合法候補として表さなければならない（MUST）。

```json
{
  "action_id": "a7",
  "action": {
    "type": "reach",
    "actor": 0,
    "dahai": {"type": "dahai", "actor": 0, "pai": "7s", "tsumogiri": false}
  }
}
```

```json
{
  "action_id": "a8",
  "action": {
    "type": "pon",
    "actor": 0,
    "target": 3,
    "pai": "E",
    "consumed": ["E", "E"],
    "dahai": {"type": "dahai", "actor": 0, "pai": "9p", "tsumogiri": false}
  }
}
```

複合action内の `dahai` は独立した完全なdahai objectであり、`type` は常に `dahai`、`actor` は外側actionの `actor` と同じでなければならない（MUST）。ホストは受理後、`reach` と `dahai`、または `pon` と `dahai` を別々の `event` として連続配信しなければならない（MUST）。途中に別の `request` を挿入してはならない（MUST NOT）。

### 8.4 競合解決

ホストは、同じ `decision_group_id` の応答を、全memberがterminalになるか `decision_group_deadline_ms` に達した時点で一度だけ原子的に解決しなければならない（MUST）。未応答memberには `default_action_id` を適用し、`defaulted` ackを生成する。期限前に受信した合法actionは、他memberの応答を待ってから優先順位を評価する。

優先順位は次の順序に固定する（MUST）。

1. `hora`: `rules.ron_policy` に従い、`multiple` は全て、`head_bump` は `(actor-target+4) mod 4` が最小の1人、`double_only` は最小の2人を採用する。3人以上なら全horaを採用せず `sanchaho` のryukyokuとする。
2. `daiminkan` または `pon`: 複数候補は `(actor-target+4) mod 4` が最小の1人を採用する。
3. `chi`: 複数候補は同じ距離式の最小の1人を採用する。
4. 採用候補がない場合は全ての `none` を受理し、次のtsumoへ進む。

`hora` が1個以上採用された場合、副露候補は全て `superseded` とし、1個の `end_kyoku` を送る。副露候補を採用した場合、同groupの他候補を `superseded` とし、全ackを送信してから採用eventを1個だけ送る。全memberのackと採用eventは同一groupのtransactionに属し、group解決中に別のstate eventまたは次groupのrequestを送信してはならない（MUST NOT）。

合法であったが他家の優先行動に負けた action の status は `rejected` ではなく `superseded` とする（MUST）。当該 action をチョンボ等の違法行動として扱ってはならない（MUST NOT）。

### 8.5 不正 action

`request_id` が未解決であるが `action_id` が `legal_actions` に存在しない場合、ホストは `rules.invalid_action_policy` に従って次を実行しなければならない（MUST）。

| policy | 処理 |
|---|---|
| `reject` | `rejected` ack と recoverable `invalid_action` を送信し、元の期限まで request を未解決のまま維持する |
| `default` | `default_action_id` を直ちに採用し、`defaulted` ack を送信する |
| `chombo` | `rejected` ack の後、全ての未解決requestをterminal statusへ遷移させ、`result.type == "penalty"` の `end_kyoku` を送信する |

JSON 構文違反、message Schema 違反または `session_id` 不一致は、この policy の対象外である。これらは第12節の error として処理する。

`chombo` が発生した場合、offenderの不正actionには `rejected` ackを送信し、同じgroupおよび他のgroupに残る全未解決requestには `stale` または `superseded` ackを送信してから、`result.type == "penalty"` の `end_kyoku` を送信しなければならない（MUST）。新しいactionを受理してpenaltyへ混在させてはならない（MUST NOT）。`end_kyoku` の `result.penalty.payments` と `deltas` は `rules.chombo` の配分へ一致させ、`next` は通常の局進行規則で決定する。これにより `end_kyoku` 送信時に未解決requestを残してはならない。

## 9. `ack` と timeout

```json
{
  "yamai": "1.0-draft.5",
  "kind": "ack",
  "session_id": "s_01J6...",
  "game_id": "g_01J6...",
  "seq": 44,
  "request_id": "r_01J6...",
  "action_id": "a2",
  "status": "accepted",
  "elapsed_ms": 812,
  "time_bank_ms": 15000
}
```

`status` は次のいずれかでなければならない（MUST）。

| status | 意味 | 状態への適用 |
|---|---|---|
| `accepted` | 選択が採用された | 後続 event で適用 |
| `passed` | `none` が受理された | 変更なし |
| `superseded` | 合法だが優先行動に負けた | 変更なし |
| `defaulted` | 期限切れで既定行動を採用 | 後続 event で適用 |
| `stale` | 古い・解決済み request への応答 | 変更なし |
| `rejected` | request/action の組が不正 | 常に非終端。`invalid_action_policy == reject` では元requestを期限まで維持し、`default`/`chombo` では直後に `defaulted` または `stale`/`superseded` の終端ackを送る。errorを伴う場合がある |

すべての `ack` は `request_id`、`status`、`action_id`、`elapsed_ms` および `time_bank_ms` を持たなければならない（MUST）。`action_id` は当該statusに対応する選択（`rejected` では受信したaction）を表す。`elapsed_ms` はrequest送信開始からの確定経過時間、`time_bank_ms` はack適用後の残量であり、いずれも0以上1,800,000以下、後者は0以上600,000以下でなければならない（MUST）。`rejected` 以外のackは終端状態であり、`rejected` ackだけを送ってrequestを終端化してはならない（MUST NOT）。終端状態へ遷移した後の再送は、旧ackを通常送信へ挿入せず、第13節のreplayまたはsnapshotの履歴としてだけ扱う。

期限の計測は、ホストの単調増加する時計で、完全なrequest messageを当該transportの送信キューへ渡し終えた時点に開始する（MUST）。JSON LinesではLFを含む1行をflushした時点、WebSocketではtext message全体（fragmentを含む）を送信APIへ渡し終えた時点を同じ開始点として扱う。`elapsed_ms` はこの時計からの経過時間を切り捨てた整数である。decision groupでは全memberのrequest messageを送信キューへ渡し終えた後の最初の時点を共通起点とし、各requestの個別時計が共通起点より前に始まってはならない（MUST）。deadlineは `grace_ms + timeout_ms + time_bank_ms` であり、`elapsed_ms <= grace_ms + timeout_ms + time_bank_ms` のactionを期限内とし、それを超えた時点でtimeoutとする。`elapsed_ms <= grace_ms + timeout_ms` の場合、time bankを消費しない。超過した場合の消費量は `min(max(0, elapsed_ms - grace_ms - timeout_ms), prior_time_bank_ms)`、残量は `prior_time_bank_ms - consumed_ms` とする。actionの受付とtimeout処理が同じdeadlineを競合する場合、ホストは単一の状態機械ロック内で先に到達した処理を1回だけ採用し、同一monotonic timestampではtimeoutを優先しなければならない（MUST）。ホストは ack の `elapsed_ms` と `time_bank_ms` に確定値を格納しなければならない（MUST）。`bank_scope` の開始時に残量を `rules.time_control.bank_ms` へ reset しなければならない（MUST）。

期限超過時、ホストは `default_action_id` を採用し、`defaulted` ack を送信しなければならない（MUST）。

ホストが action を既に受理している場合、同じ `request_id` と `action_id` の再送には、元のackを再送せず、既に送信済みなら新しいapplication messageを生成してはならない（MUST NOT）。resumeまたはsequence-gapのreplayでは、最初のackを元の `seq` および内容で再送する。同じ `request_id` に異なる `action_id` が再送された場合、最初の選択を維持し、受信したactionの `request_id`、`action_id`、元のstatusを含むrecoverable `request_conflict` errorを返さなければならない（MUST）。timeoutによりrequestが既に `defaulted` で解決されている場合、後着actionを適用せず、新しい `seq` の `stale` ackを返さなければならない（MUST）。`accepted`、`passed`、`superseded`、`defaulted` または `stale` へ遷移したrequestのID、選択action、terminal statusおよびackのwire内容は、少なくとも当該gameの `end_game` まで保持しなければならない（MUST）。

## 10. イベント順序

### 10.1 打牌と鳴き

```text
event dahai
request(s) chi/pon/daiminkan/hora/none
action(s)
ack(s)
event chi|pon|daiminkan と event dahai、または end_kyoku
event tsumo                         if 全てnone
```

### 10.2 加槓・暗槓と槍槓

```text
event kakan_declared
request(s) hora/none to other seats
action(s)
ack(s)
end_kyoku                     if hora accepted
event kakan                    otherwise, commit the meld
```

`kakan_declared` を受信したプレイヤーに `hora` が合法なら、ホストは request を発行しなければならない（MUST）。`ankan_chankan` が `kokushi_only` なら、国士無双で和了可能なプレイヤーに限り `ankan_declared` 後の request を発行する。

成立した槓の種別を `K` とし、`T = rules.kan_dora_timing[K]` とする。槍槓 `hora` が受理された場合は `ankan`／`kakan` の成立eventを送信せず、下記の分岐に従う `dora`（必要な場合）に続けて `end_kyoku` を送信する。`T == "before_rinshan"` の場合、ホストは次の順に送信しなければならない（MUST）。

```text
event ankan|daiminkan|kakan
event dora
event tsumo
request/action/ack for rinshan turn
end_kyoku                     if rinshan hora accepted
```

`T == "after_rinshan_discard"` の場合、ホストは次の順に送信しなければならない（MUST）。

```text
event ankan|daiminkan|kakan
event tsumo
request/action/ack for rinshan turn
event dora
end_kyoku                     if rinshan hora accepted
event dahai                    otherwise
```

後者では、`dora` は選択された嶺上打牌の `dahai` eventより前に公開されるが、打牌actionの選択後である。嶺上和了の場合も `dora` を公開してから `end_kyoku` を送信し、`dahai`およびその反応requestは送信しない（MUST）。通常の嶺上打牌の場合は `dahai` の後に第10.1節の反応groupを開始する。槓種別に異なる `T` を使用できる。

### 10.3 リーチ

複合 `reach` action を受理したホストは次の event を連続配信しなければならない（MUST）。

```text
event reach
event dahai
request(s) reactions to dahai
ack(s)
event reach_accepted  if the round continues and rules accept reach
event chi|pon|daiminkan と event dahai、または event tsumo
```

`reach_accepted` はreaction groupを原子的に解決した後、`hora`、`chi`、`pon` または `daiminkan` が採用されず、かつrulesがreachを認める場合に限り、鳴きeventより先に送信する（MUST）。ロンまたは鳴きが採用された場合は `reach_accepted` を送信せず、`reach` eventは未成立の宣言として局終了時に破棄する。宣言時の供託を暗黙に点数へ反映してはならない。供託を適用する位置はprofileで固定する。`reach_accepted.deltas`、`scores` および `kyotaku` は、その適用と供託本数の増加を検証可能にしなければならない（MUST）。

### 10.4 状態前後条件

ホストは次の前後条件を満たさないeventを送信してはならず、プレイヤーは違反を `invalid_message` として扱わなければならない（MUST）。`state` の用語は第13.3節のsnapshotと同じである。

| event | 直前条件 | 適用後の更新 |
|---|---|---|
| `start_game` | sessionがactiveでgame未開始 | players、rules、scoresを初期化し、kyotakuを0とする |
| `start_kyoku` | game開始済み、前局が終了 | hands、rivers、melds、dora、wall、oya、honba、kyotakuを指定値へ置換し、`first_turn_eligible`を全seat true、`kan_counts`を全seat 0、pendingを空にする |
| `tsumo` | `awaiting_draw`、actorが現在手番、wallまたはrinshan牌が存在 | actorの手牌へpaiを追加し、wallを1減らし、phaseを`awaiting_action`へ進める。live wall最後の牌なら`haitei`をtrueとする |
| `dahai` | `awaiting_action`、actorが手番、paiがactorの手牌に存在 | paiをriverへ移し、`first_turn_eligible[actor]`をfalse、reactionが必要なら`awaiting_responses`へ進める |
| `chi`/`pon`/`daiminkan` | 直前dahaiへのreaction groupが解決済み、採用候補 |対象牌を副露へ移し、対象seatのfirst-turn資格をfalseとし、chi/ponは直後のdahai request、daiminkanは嶺上drawへ進める |
| `ankan_declared`/`kakan_declared` | `awaiting_action`、actorが手番、対象牌が合法 | `pending_kan`を設定し、槍槓判定が必要ならresponse groupへ進める。面子、`kan_counts`、doraはまだ更新しない |
| `ankan`/`kakan`/`dora` | pending kanまたはkan-dora timingが許す状態 | pendingを確定し、該当actorの`kan_counts`を1増加、dora timingに従いdoraを追加する |
| `reach_accepted` | 当該reachのreaction groupでhora/鳴きがなく、供託を控除可能 | actorのreach stateをaccepted、kyotakuを1増加、scores/deltasを同時に更新する |
| `end_kyoku` | hora、ryukyokuまたはpenaltyが確定し、未解決requestがない | pendingを全て破棄し、result、scores、nextを原子的に確定する |
| `end_game` | 最終`end_kyoku`後 | scores、rankings、kyotakuを固定し、同gameの後続event/requestを禁止する |

`first_turn_eligible`、`kan_counts`、`haitei`、`rinshan`、`pending_kan` および `reach_status` はevent適用後の値を保持し、snapshotで省略してはならない（MUST）。`dahai` 後のreaction groupは、そのdahaiを原因とするrequestが全て終端化するまで次のstate eventを送信してはならない。`start_kyoku.scores` と `kyotaku` は直前の `end_kyoku.next` または `start_game` の確定値と一致しなければならない（MUST）。

## 11. visibility と mode

`welcome.mode` は次のいずれかでなければならない（MUST）。

| mode | 用途 | 秘匿 |
|---|---|---|
| `play` | 実対局 | 各座席 view に必要な情報だけ |
| `spectate` | 観戦 | `welcome.view` で公開viewを指定 |
| `replay` | 牌譜再生 | `welcome.view` で完全情報または座席viewを指定 |

`play` では、ホストは各接続の `seat` に応じて別の view を生成しなければならない（MUST）。他家のツモ牌は `null`、他家の配牌は `{"count":n}` とする。

`play` の `welcome.seat` は整数seatでなければならない（MUST）。`spectate` と `replay` では `seat` を `null` とし、`view` を `public`、`full` または `{ "seat": N }` のいずれかとする。`full` は `replay` でだけ使用できる（MUST）。`{"seat":N}` は当該seatのplay viewと同じ秘匿を適用する。

`public` viewは全ての `hands` を `{"count": n}`、全ての非公開 `tsumo.pai` を `null`、`self_state` と `pending_requests` を省略する投影である。`ankan_declared` と `ankan` の `consumed` は、当該actorのplay viewおよび `replay` の `full` viewだけが牌種を受け取り、それ以外のviewでは各要素を `null` とする。その他の非公開牌を含むeventも、Schemaが許す範囲で同じく `null` へ置換し、それ以外のevent memberと `end_kyoku` の精算値は保持する。`public` の定義にない情報を送信してはならない（MUST NOT）。snapshotの `state` も同じ投影を適用し、`mode == spectate` または `replay` では `pending_requests` と `self_state` を含めてはならない（MUST NOT）。

ホストは `spectate` または `replay` sessionへ `request` または `ack` を送信してはならない（MUST NOT）。当該sessionは `action` を送信してはならない（MUST NOT）。replayのeventは記録済みeventの順序で送信するが、replay sessionの `seq` はeventだけで1から振り直さなければならない（MUST）。振り直し前の記録messageの番号は各replay eventの必須 `original_seq` として保持する。replayではlive timeoutを適用しない。

mode を途中で変更してはならない（MUST NOT）。完全情報 replay を play クライアントへ送信してはならない（MUST NOT）。

## 12. エラー

版交渉前に送信するerrorは `kind`、`code`、`severity` および `message` を持ち、`yamai`、`session_id`、`game_id` および `seq` を持ってはならない（MUST NOT）。`welcome` 完了後にホストが送信するerrorは、第5節の共通envelopeに従わなければならない（MUST）。

プレイヤーが送信する error は `seq` を持ってはならず（MUST NOT）、版交渉後は `yamai`、`session_id` および `game_id` を持たなければならない（MUST）。host message に関連する error は `caused_by_seq`、request に関連する error は `request_id` を含むべきである（SHOULD）。

```json
{
  "yamai": "1.0-draft.5",
  "kind": "error",
  "session_id": "s_01J6...",
  "game_id": "g_01J6...",
  "seq": 45,
  "code": "invalid_action",
  "severity": "recoverable",
  "message": "action_id is not legal for this request",
  "request_id": "r_01J6..."
}
```

安定 error code:

| code | severity | 説明 |
|---|---|---|
| `unsupported_version` | fatal | 共通版がない |
| `unsupported_profile` | fatal | profile 非対応 |
| `profile_mismatch` | fatal | profile revisionまたはhash不一致 |
| `unsupported_capability` | fatal | required capability 非対応 |
| `unsupported_rules` | fatal | 必須ルール非対応 |
| `unsupported_limit` | fatal | 提示された資源上限を実装できない |
| `unsupported_frame` | fatal | transport frame の種別が非対応 |
| `invalid_frame` | fatal | フレーミング違反 |
| `invalid_json` | fatal | JSON 構文違反 |
| `invalid_message` | 下記規則 | Schema 違反 |
| `sequence_gap` | recoverable | event 欠落。snapshot/replay が必要 |
| `sequence_conflict` | fatal | 同じ seq の内容が異なる |
| `invalid_action` | recoverable | request/action の不一致 |
| `request_conflict` | recoverable | 同一 request へ異なる再送 |
| `resume_unavailable` | fatal | 要求された session を復旧できない |
| `resource_limit` | fatal | 上限超過 |
| `internal_error` | fatal | ホスト内部エラー |

`severity == "recoverable"` の error は、関連する不正 message を状態へ適用せず、session を継続できることを表す。`severity == "fatal"` の error を送信した endpoint は、当該 error の送信完了後に新しい application message を送信してはならず（MUST NOT）、transport を終了しなければならない（MUST）。

`invalid_message` は、プレイヤーからホストへの `action` が第8.2節の必須member `yamai`、`kind`、`session_id`、`game_id` および既知のrequest_idを正しい型で持ち、`action_id` またはその他のaction固有memberだけがSchema違反である場合に限りrecoverableとする。既知の解決済みrequestへのwell-formedなactionはSchema違反ではなく、第9節の `stale` または元ackとして処理しなければならない（MUST）。それ以外のHost → Player message、交渉message、ID不一致または状態変更messageのSchema違反はfatalとする（MUST）。

`message` は診断専用とし、プログラム分岐には `code` を使用しなければならない（MUST）。秘密情報、手牌、token または stack trace を `message` に含めてはならない（MUST NOT）。

## 13. 再接続と snapshot

### 13.1 Resume token

`resume` capabilityを交渉したsessionでは、第6.2節の `join.resume` と第6.3節の `welcome.resume` を使用する。tokenは128 bit以上の暗号学的乱数から生成し、URL-safe ASCIIで表現しなければならない（MUST）。tokenは1回の再開成功時に失効し、新しいtokenへrotateしなければならない（MUST）。

### 13.2 Replayによる再開

再開成功後、ホストは同じsessionについて `welcome.replay_from_seq` から送信済み最新 `seq` までをbyte-for-byteで再送しなければならない（MUST）。クライアントは `join.resume.last_seq` 以下を再適用してはならない（MUST NOT）。このsession resumeの再送は第11節のreplay modeとは異なり、seqを振り直してはならない。再送完了後、通常の次 `seq` からlive送信へ移行する。

transport切断はrequestの時計を停止しない。再開時までに期限切れとなったrequestをホストは `defaulted` として解決し、そのackと結果eventをreplay範囲へ含めなければならない（MUST）。replay範囲に未解決requestが残る場合、ホストはそのrequestを元のseq・内容でbyte-for-byte再送するか、`snapshot` capabilityを使用して第13.3節の再計算済み `pending_requests` を送信しなければならない（MUST）。再送されたrequestは同じ `request_id` の再提示として扱い、元のdeadlineを延長せず、新しいrequestを生成してはならない。snapshotで復元されたpending requestは元の `request_id`、`action_id`、legal action、defaultおよびgroup情報を保持し、切断前に送信済みだったactionは同じrequestの冪等規則で処理する（MUST）。

### 13.3 Snapshotによる再開

`snapshot` capabilityが有効な場合、ホストはreplayの代わりに次のmessageを送信できる（MAY）。

```json
{
  "yamai": "1.0-draft.5",
  "kind": "snapshot",
  "session_id": "s_01J6...",
  "game_id": "g_01J6...",
  "seq": 121,
  "replaces_through_seq": 120,
  "state": {
    "mode": "play",
    "seat": 0,
    "view": "seat",
    "players": [
      {"seat": 0, "name": "ExampleAI"},
      {"seat": 1, "name": "BotB"},
      {"seat": 2, "name": "BotC"},
      {"seat": 3, "name": "BotD"}
    ],
    "scores": [25000, 25000, 25000, 25000],
      "kyoku": {
      "bakaze": "E",
      "kyoku": 1,
      "honba": 0,
        "kyotaku": 0,
        "oya": 0,
        "extension_round": 0,
      "hands": [{"tiles": ["1m", "2m", "3m", "4m", "5mr", "6m", "7p", "8p", "9p", "2s", "3s", "4s", "E", "9s"]}, {"count": 13}, {"count": 13}, {"count": 13}],
      "rivers": [[], [], [], []],
      "melds": [[], [], [], []],
      "dora_markers": ["2p"],
      "wall_remaining": 69,
      "turn": {"actor": 0, "phase": "awaiting_action", "last_event_seq": 120, "last_event": {"type": "tsumo", "actor": 0, "pai": "9s"}},
      "reach_status": [
        {"state": "none", "double": false, "ippatsu": false},
        {"state": "none", "double": false, "ippatsu": false},
        {"state": "none", "double": false, "ippatsu": false},
        {"state": "none", "double": false, "ippatsu": false}
      ],
      "first_turn_eligible": [true, true, true, true],
      "kan_counts": [0, 0, 0, 0],
      "rinshan": false,
      "haitei": false,
        "pending_kan": null,
        "pao": [],
        "self_state": {"temporary_furiten": false, "riichi_furiten": false, "kuikae_forbidden": [], "time_bank_ms": 15000}
      },
    "pending_requests": [
      {
        "request_id": "r_01J6...",
        "seat": 0,
        "caused_by_seq": 120,
        "timeout_ms": 3000,
        "time_bank_ms": 15000,
        "legal_actions": [{"action_id": "a1", "action": {"type": "dahai", "actor": 0, "pai": "9s", "tsumogiri": true}}],
        "default_action_id": "a1"
      }
    ]
  }
}
```

`hands` はsnapshot時点の正確な手牌または非公開枚数を持たなければならない（MUST）。rulesは再開時の `welcome.rules` を権威とし、snapshotで変更してはならない（MUST NOT）。

`snapshot.replaces_through_seq` はsnapshot送信直前に当該sessionで使用済みの最大seqであり、`snapshot.seq` は `replaces_through_seq + 1` の新しい番号でなければならない（MUST）。`snapshot.replaces_through_seq` はresumeの `last_seq` およびsequence gapの `received_seq` 以上でなければならない。クライアントは、resumeまたは `sequence_gap` への応答として受信したsnapshotに限り、通常のgap検査を行わず受理できる（MAY）。受理時に既存game stateと未解決requestを全て破棄し、`state`で置換する。ただし `state.pending_requests` に含まれるrequestは同じID・候補・deadlineを持つ新しいactive representationとして復元し、最後に適用した番号を `snapshot.seq` とする。その次のhost messageは `snapshot.seq + 1` でなければならない（MUST）。

`kyoku` は局外なら `null`、局内なら上記memberを持つobjectとする。`rivers` の各要素は `{pai,tsumogiri,reach}`、`melds` の各要素は `chi`、`pon`、`daiminkan`、`ankan` または `kakan` eventと同じmemberを持つ。`pending_kan` は `null` または未解決の `ankan_declared`／`kakan_declared` eventである。

`turn.phase` は `awaiting_draw`、`awaiting_action`、`awaiting_responses`、`resolving` のいずれかである。`awaiting_action` または `awaiting_responses` では対応する `pending_requests` を少なくとも1個持ち、`awaiting_draw` または `resolving` ではpending requestを持ってはならない（MUST）。`last_event` は `last_event_seq` のevent payloadと同一でなければならない（MUST）。`reach_status[].state` は `none`、`declared`、`accepted` のいずれかであり、`double` と `ippatsu` は当該seatの現在の資格を表す。`first_turn_eligible`、`kan_counts`、`rinshan` および `haitei` は、途中流局・役・槓制限の判定に使用する現在値である。

`self_state` はplay sessionで必須とし、当該sessionのseatだけが知る一時状態を持つ。`temporary_furiten` は次の自摸までの同巡振聴、`riichi_furiten` はリーチ後の見逃しによる継続振聴、`kuikae_forbidden` は直後の打牌で禁止されるtile、`time_bank_ms` は現在の残り持ち時間である。これらを他seatのplay viewへ送信してはならない（MUST NOT）。spectateとreplayでは `self_state` を省略する。

`pending_requests` の各要素は `request_id`、`seat`、`caused_by_seq`、`timeout_ms`、`time_bank_ms`、`legal_actions` および `default_action_id` を持つ。envelope memberは含めない。`decision_group_id` を持つ要素は、`decision_group_members`、`decision_group_deadline_ms` および `decision_group_close` も必須とし、live requestと同じ値を保持しなければならない（MUST）。`decision_group_id` を持たない要素は、これらgroup memberを省略してよく、含めてはならない（MUST NOT）。`timeout_ms` はsnapshot作成時点のhost時計からの残り猶予であり、snapshot受信を起点に再開始してはならない（MUST）。hostはsnapshot送信中も元のdeadlineを監視し、期限切れなら `defaulted` のack/eventを通常のseqで送信する。play modeのsnapshotは第11節のvisibilityを越えてはならない（MUST NOT）。

## 14. 拡張

標準 member を別の意味で再利用してはならない（MUST NOT）。

- 任意の実験フィールド: `x_<owner>_<name>`
- 実験 capability: `x-<owner>-<name>`
- 実験 event/action type: `x-<owner>-<name>`

状態遷移に影響する拡張は capability 交渉を必須とする（MUST）。理解せず無視した場合に状態が変化する拡張を、単なる未知 member として送信してはならない（MUST NOT）。

標準objectは定義済みmemberと `x_<owner>_<name>` のnamespaced memberだけを持つ閉じたobjectである（MUST）。受信者は未知の標準memberを `invalid_message` として拒否し、未知のnamespaced memberは状態へ影響しない場合に限り無視してよい（MAY）。namespaced memberの値が状態または点数へ影響する場合、そのownerはcapabilityで意味と適用順を交渉しなければならない（MUST）。

minor 版は、既存実装が安全に無視できる任意 member または任意 capability だけを追加できる（MAY）。必須 member、状態遷移または既存値の意味を変更する場合、major 版を上げなければならない（MUST）。

## 15. 資源・安全要件

実装は次の制限を持たなければならない（MUST）。

| 項目 | 既定上限 |
|---|---:|
| 1メッセージ | 1 MiB |
| 受信frame／LF待ちバッファ | 1 MiB |
| JSON depth | 64 |
| ID length | 64 byte |
| 同一接続の未解決 request | 4 |
| `legal_actions` | request あたり512 |
| event 数 | 1ゲームあたり100,000 |
| 送信backlog | 8 MiB または1024 messageの小さい方 |
| 応答待ち | request に明示。無期限禁止 |

上限超過データを部分適用してはならない（MUST NOT）。受信側はJSONLでLFを待つ未完frameを1 MiBを超えて保持してはならず、WebSocketではmessage payload上限をhandshake時に設定しなければならない。送信側は送信backlogを超えてmessageをenqueueせず、backpressureを適用して順序とseqを維持しなければならない（MUST）。backlogが満杯の間は新しいrequestを発行せず、60,000msを超えてpeerがdrainしない場合は、送信可能なら `resource_limit` を通知してからtransportを閉じなければならない（MUST）。ログ出力は protocol transport と分離し、標準入出力 transport では診断を stderr へ出さなければならない（MUST）。

## 16. MJAI からの移行

| MJAI | YAMAI |
|---|---|
| `hello.protocol_version` | `hello.versions` と `join.version` |
| 全イベントへの `none` | `request` がある場合だけ action |
| `possible_actions` | 完全な `legal_actions` + `action_id` |
| arrival order の応答 | `request_id` |
| 暗黙 timeout | `timeout_ms`, `time_bank_ms`, default action |
| 逐次 `hora` | atomic `end_kyoku.result.wins[]` |
| `?` | `null` または `{"count":n}` |
| 独自 `aka_flag` 等 | profile の必須 `rules` |
| TCP切断で終了 | `end_game` event。接続寿命とは独立 |

MJAI から YAMAI への gateway は、入力方言とそのrevisionを明示的に設定しなければならない（MUST）。gatewayは少なくとも、object/array frameの境界、`possible_actions` から `legal_actions` への候補対応、複合reach・鳴きaction、`tehais`/秘匿牌の view、逐次`hora`から`wins[]`への順序、`fan`/`hora_points`/裏ドラ表示牌欄からYAMAIの `han`/`hand_points`/`ura_dora_markers`への対応を宣言しなければならない（MUST）。YAMAIの必須memberを入力方言から得られず、規範的に再計算もできない場合は、既定値で補わず変換を拒否しなければならない（MUST）。情報がないルールを推測した場合、変換結果へ `x_gateway_assumptions` を記録すべきである（SHOULD）。

逐次 `hora` を `end_kyoku` へ変換する gateway は、最後の累積 `scores` を確定点として使用し、各 `deltas` の和と一致するか検証しなければならない（MUST）。不一致を黙って補正してはならない（MUST NOT）。

gatewayの変換表、損失箇所および拒否条件は、同一release tagの規範Schema、registryおよび公式test vectorへ追跡可能でなければならない（MUST）。

## 17. 適合性

`YAMAI 1.0-draft.5 riichi-4p play` 適合を表明する実装は、少なくとも次の試験を通過しなければならない（MUST）。

1. 版不一致と未対応ルールの拒否
2. JSONL の分割・複数行一括受信
3. `seq` の重複・欠落・衝突
4. action の正常、遅延、重複、異なる再送
5. chi、pon、daiminkan、ankan、kakan、槍槓
6. リーチ複合 action と供託
7. 赤牌を含む consumed
8. ダブルロン、頭ハネ、三家和
9. 通常流局、九種九牌、途中流局
10. timeout と default action
11. play/replay の情報秘匿
12. message・depth・action 数上限
13. 暗槓・大明槓・加槓ごとの槓ドラ公開時点
14. `han`、`yakus`、`bonuses`、役満倍数の整合
15. `sequence_gap` 後の範囲再送とsnapshot置換
16. resume tokenのrotate、期限切れ、replay、snapshot
17. 同一transport上の複数sessionと `end_game.rankings`
18. snapshotの未使用 `seq`、一時振聴、一発、第一巡、手番、time bank
19. 未解決requestを含むresumeでのtimeout継続とsnapshot強制
20. 複数ロンの本場・供託配分と責任払いの端数
21. bankruptcy、連荘、アガリ止め、延長の評価順
22. play、spectate、replayのseat・request禁止・visibility
23. [YRC 0005] の全役・符・点数test vector
24. profile revision/hash、required/optional capability、mode/view/target交渉
25. decision groupの全member、共通deadline、close、優先順位および原子解決
26. pao決定履歴、責任seat、chombo payments、未解決requestのterminal化
27. notenのtenpai人数別payments、kyotaku本数の積算・配分・繰越・終局
28. 嶺上和了時の槓ドラ分岐、`ura_dora_markers` およびreplay `original_seq`
29. grace、deadline境界、同一seatの重複request、送信backpressure

Schema、公式test vectorおよびregistryは、本書と同じrepository・同じrelease tagで版管理しなければならない（MUST）。規範Schemaまたはtest vectorに互換性のない変更を行う場合、draft revisionまたはmajor versionを更新しなければならない（MUST）。

## 18. Security Considerations

### 18.1 入力検証

受信者は JSON 構文、message Schema、profile Schema および状態遷移を、状態へ適用する前に検証しなければならない（MUST）。型不正、範囲外 seat、不正 tile、未知の必須 capability または矛盾する `scores` を既定値へ変換して続行してはならない（MUST NOT）。

### 18.2 Resource Exhaustion

第15節の上限は、message length、JSON depth、候補数、未解決 request および game event 数による resource exhaustion を制限する。実装は上限へ達した入力を部分適用してはならない（MUST NOT）。timeout は無期限であってはならない（MUST NOT）。

### 18.3 Confidentiality

YAMAI 自体は暗号化または peer authentication を提供しない。信頼境界を越える接続は、TLS または同等の authenticated confidential transport を使用しなければならない（MUST）。

`play` mode のホストは、第11節の view 制約を守らなければならない（MUST）。ログ、error message、snapshot および tracing data も同じ制約を受ける。完全情報 replay、resume token、認証 token または他家の非公開牌を診断出力へ含めてはならない（MUST NOT）。

resume tokenはbearer credentialとして扱わなければならない（MUST）。ホストはtokenを平文で永続保存せず、one-way hashまたは同等の漏洩耐性を持つ形式で検証すべきである（SHOULD）。tokenを使用する再接続はauthenticated confidential transport上でのみ許可する（MUST）。

### 18.4 Integrity and Replay

TLS を使用しない transport は、message の改ざんと session hijacking に脆弱である。`session_id`、`game_id` および `request_id` は認証 token ではない。これらを possession proof として使用してはならない（MUST NOT）。

action replay は第9節の冪等規則で処理しなければならない（MUST）。同じ `request_id` へ異なる action を適用してはならない（MUST NOT）。

### 18.5 Fairness and Timing

`timeout_ms` と `time_bank_ms` は対局結果へ影響する。ホストは全 seat へ同じ rule で時間を計測し、transport latency を含むか否かを一貫させなければならない（MUST）。クライアントが送る elapsed time を権威として使用してはならない（MUST NOT）。

## 19. Registry Considerations

YAMAI Project は次の registry を本書と同じ repository で管理する。

| Registry | 初期値 |
|---|---|
| Protocol Versions | `1.0-draft.5` |
| Profiles | `riichi-4p@1.0-draft.3` (`sha256:` hashはrelease registryで確定) |
| Capabilities | required/optional交渉。初期optional: `resume`, `snapshot` |
| Message Kinds | `hello`, `join`, `welcome`, `event`, `request`, `action`, `ack`, `error`, `snapshot` |
| Event Types | 第7.4節の値（`pao`を含む） |
| Action Types | `none`, `dahai`, `chi`, `pon`, `daiminkan`, `ankan`, `kakan`, `reach`, `hora`, `ryukyoku` |
| ACK Status | 第9節の値 |
| Error Codes | 第12節の値（`profile_mismatch`, `unsupported_capability`を含む） |
| Rule Keys | 第7.2節の値（`reaction_priority`, `chombo`を含み、`return_points`を含まない） |
| Result Types | `hora`, `ryukyoku`, `penalty` |
| Result Reasons | `fanpai`, `kyushukyuhai`, `suufon_renda`, `suucha_riichi`, `suukan_sanra`, `sanchaho`, `illegal_action` |
| Yaku IDs | 下記の初期 Yaku IDs |
| Bonus IDs | `dora`, `uradora`, `akadora` |
| Double Yakuman Conditions | `kokushi_13_wait`, `suuankou_tanki`, `junsei_chuuren`, `daisuushii` |

安定値の登録方針は [RFC 8126] の **Specification Required** とする。YAMAI Projectは1名以上のDesignated Expertを公開registryのmaintainer欄に指名しなければならない（MUST）。申請者本人は自身の申請を承認してはならない（MUST NOT）。Designated Expertが未指名の間、新しい安定値を登録してはならない（MUST NOT）。

登録申請は公開pull requestとして、公開仕様、JSON Schema、最低1個の正例、最低1個の負例、状態遷移への影響、security considerationsおよび後方互換性を提示しなければならない（MUST）。最低14日間のpublic review後、申請者以外のDesignated Expertが、識別子衝突、仕様の永続性、独立実装可能性、後方互換性およびsecurity impactを審査する。承認・拒否・差戻しの理由はpull requestへ記録しなければならない（MUST）。異議申立てはYAMAI Project maintainerの過半数で裁定する。

`x-<owner>-<name>` capability および type、ならびに `x_<owner>_<name>` member は Private Use とし、登録を要求しない。実験値を安定値として依存させてはならない（MUST NOT）。

`riichi-4p` の初期Yaku IDsは次の値とし、成立条件と飜数は [YRC 0005] に従う。

```text
riichi, double_riichi, ippatsu, menzen_tsumo, tanyao, pinfu,
iipeikou, yakuhai_haku, yakuhai_hatsu, yakuhai_chun, seat_wind,
round_wind, rinshan_kaihou, chankan, haitei, houtei,
sanshoku_doujun, ikkitsuukan, chanta, chiitoitsu, toitoi,
sanankou, honroutou, sanshoku_doukou, sankantsu, shousangen,
honitsu, junchan, ryanpeikou, chinitsu, kokushi_musou,
suuankou, daisangen, shousuushii, daisuushii, tsuuiisou,
chinroutou, ryuuiisou, chuuren_poutou, suukantsu, tenhou, chiihou
```

和了形が `rules.double_yakuman` に登録されたconditionを満たす場合もyaku IDを変更せず、該当 `yakus[].value` を2、`yakus[].unit` を `yakuman` とする。それ以外の役満はvalue 1とする。役名の表示文字列をprotocol decisionに使用してはならない（MUST NOT）。

次の provisional identifier を使用する。IANA 登録が完了するまで、一般の Internet media type または WebSocket subprotocol として登録済みであると表明してはならない（MUST NOT）。

- JSON Lines media type: `application/yamai-jsonl`
- JSON message media type: `application/yamai+json`
- WebSocket subprotocol: `yamai.1.draft5`

`+json` は登録済みstructured syntax suffixである。一方、JSON Lines全体は単一JSON textではないため、未登録suffix `+jsonl` を使用してはならない（MUST NOT）。media typeの正式登録は [RFC 6838] のtemplateとreview手続に従う。

## 20. Normative References

- [YRC 0005] YAMAI Project, “YAMAI `riichi-4p` 役・符・点数規則 (1.0-draft.3)”.

- [BCP 14] Bradner, S., “Key words for use in RFCs to Indicate Requirement Levels”, BCP 14, RFC 2119, March 1997; Leiba, B., “Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words”, BCP 14, RFC 8174, May 2017.  
  https://www.rfc-editor.org/info/bcp14
- [RFC 8259] Bray, T., Ed., “The JavaScript Object Notation (JSON) Data Interchange Format”, STD 90, RFC 8259, December 2017.  
  https://www.rfc-editor.org/rfc/rfc8259
- [RFC 5234] Crocker, D., Ed. and P. Overell, “Augmented BNF for Syntax Specifications: ABNF”, STD 68, RFC 5234, January 2008.  
  https://www.rfc-editor.org/rfc/rfc5234
- [RFC 6455] Fette, I. and A. Melnikov, “The WebSocket Protocol”, RFC 6455, December 2011.  
  https://www.rfc-editor.org/rfc/rfc6455
- [RFC 8126] Cotton, M., Leiba, B., and T. Narten, “Guidelines for Writing an IANA Considerations Section in RFCs”, BCP 26, RFC 8126, June 2017.  
  https://www.rfc-editor.org/rfc/rfc8126
- [RFC 6838] Freed, N., Klensin, J., and T. Hansen, “Media Type Specifications and Registration Procedures”, BCP 13, RFC 6838, January 2013.  
  https://www.rfc-editor.org/rfc/rfc6838
- [RFC 8785] Rundgren, A. and M. Jordan, “JSON Canonicalization Scheme (JCS)”, RFC 8785, June 2020.  
  https://www.rfc-editor.org/rfc/rfc8785

## 21. Informative References

- [YRC 0001] YAMAI Project, “デファクト MJAI プロトコル記述仕様”.
- [YRC 0002] YAMAI Project, “MJAI プロトコルの設計上の欠陥”.
- [YRC 0004] YAMAI Project, “代表的 MJAI 実装プロファイル”.
- [GIMITE-MJAI] Gimite, “Mjai 麻雀AI対戦サーバ”, 2017-06-07.  
  https://gimite.net/pukiwiki/index.php?Mjai+%E9%BA%BB%E9%9B%80AI%E5%AF%BE%E6%88%A6%E3%82%B5%E3%83%BC%E3%83%90=
- [CRYOLITE-MJAI] Cryolite, “Standardization Project for mjai Format Specification”.  
  https://github.com/Cryolite/mjai
- [RIICHI-PROTOCOL-V2] smly, “Protocol v2: request_id, action_ack, and time bank are now live”, 2026-06-10.  
  https://github.com/smly/RiichiEnv/discussions/216

## Appendix A. セッション状態機械

```text
                    hello/join/welcome
  SESSION_IDLE ------------------------------> SESSION_ACTIVE
       ^                                             |
       |          new hello on same transport        | end_game
       +-------------------------------------- SESSION_ENDED
                                                     ^
                                                     |
                         request group               |
                  GROUP_OPEN --close--> GROUP_CLOSED
                       |                         |
                       +---- atomic resolve ----+

  Any session state -- fatal error --> TRANSPORT_CLOSED
```

transport stateとsession stateは独立である。`SESSION_IDLE` では `hello`、`join`、`welcome` および交渉用 `error` だけを送信できる。`SESSION_ACTIVE` では第3節のgame-scoped messageを送信できる。複数応答を待つときは `GROUP_OPEN` とし、全memberのterminal化または共通deadlineで `GROUP_CLOSED` へ遷移する。GROUP_CLOSEDから全ackを送った後に、優先順位を一度だけ評価して採用eventを原子的に送信する。`end_game` はsessionを `SESSION_ENDED` にするが、transportを閉じる必要はない。ホストは同じtransportで新しい `hello` を送信して次sessionを開始するか、transportを正常終了できる（MAY）。

未解決 `request` は session の部分状態である。`end_kyoku` または `end_game` を送信する前に、関連するすべての request を `accepted`、`passed`、`superseded`、`defaulted` または `stale` のいずれかで解決しなければならない（MUST）。`chombo` ではoffender以外を含む全ての未解決requestをterminal化してからpenaltyを確定する。

## Appendix B. 最小交換例

次の例は envelope の必須関係だけを示す。`rules` と配牌は説明のため省略しており、実際の message としては不適合である。

```text
H -> P  hello(versions=[1.0-draft.5], profiles=[{name:riichi-4p, revisions:[1.0-draft.3], hashes:{1.0-draft.3:sha256:...}}], capabilities={required:[],optional:[resume,snapshot]})
P -> H  join(version=1.0-draft.5, mode=play, view=seat, profile=riichi-4p, profile_revision=1.0-draft.3, profile_hash=sha256:..., capabilities={required:[],optional:[resume,snapshot]})
H -> P  welcome(seat=0, rules=...)
H -> P  event(seq=1, start_game)
H -> P  event(seq=2, start_kyoku)
H -> P  event(seq=3, tsumo(actor=0,pai=3m))
H -> P  request(seq=4, request_id=r1, legal_actions=[a0,a1])
P -> H  action(request_id=r1, action_id=a1)
H -> P  ack(seq=5, request_id=r1, action_id=a1, status=accepted)
H -> P  event(seq=6, dahai(actor=0,pai=3m,tsumogiri=true))
```
