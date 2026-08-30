# YAMAI の Quint 抽象状態機械

このディレクトリは、YAMAI の規範文書にある一部の制御フローを有限状態機械へ抽象化し、Quint/TLC で静的検証するための補助モデルを収録する。いずれも限定的な制御フロー抽象モデルであり、JSON Schema、wire protocol実装、または `riichi-4p` の牌・役・点数エンジンの代替ではない。

## 5モデルの役割

| ファイル | 目的 | 主な抽象化 | 接続・探索範囲 |
|---|---|---|---|
| [`yamai_protocol_core.qnt`](yamai_protocol_core.qnt) | 規範Protocol Coreのcanonical modelとrefinement mapping | 最大4件のrequest/group map、単調時計、deadline/grace/timebank、immutable seq ledger、delivery/applied、resume/replay/snapshot、全ACK結果 | `MAX_SEQ`、`MAX_CLOCK`等の有限境界。規範状態への対応は`refinement_mapping`で検査 |
| [`yamai_protocol_core_bounded.qnt`](yamai_protocol_core_bounded.qnt) | canonical modelのCI用決定的refinement trace | 4 request slot、deadline/default/ACK、group linearization、ledger、delivery/applied、replay、snapshot | phase-driven traceを固定seedで最後まで実行 |
| [`yamai_protocol.qnt`](yamai_protocol.qnt) | 既存の小さな基準モデル | session、host seq、single/group request、per-member action/ACK/default、resume/snapshot、score/kyotaku | 接続切断を含む。1 transactionずつの有限シミュレーション |
| [`yamai_protocol_extended.qnt`](yamai_protocol_extended.qnt) | 規範要件を広く対応付ける拡張モデル | hello/join/welcome の version/profile/hash/capability、seq fresh/gap/duplicate/conflict/replay、pending resume/snapshot、request group、timeout、end_kyoku/end_game。disconnect/pending中もhost内部のtick・ACK/default・resolveを継続し、未配送結果をbacklog rangeへ保持 | `MAX_SEQ=8` などの有限境界。disconnect/resume の不安定な環境も到達可能 |
| [`yamai_request_liveness.qnt`](yamai_request_liveness.qnt) | request liveness の完全有限検査 | single/group、全member ACK、timeout/default、期限前 action の後着ACK、atomic resolve | `stable_connection=true` を不変条件とし、1 request/run。接続切断・wire・交渉は除外 |
| [`yamai_resume_delivery.qnt`](yamai_resume_delivery.qnt) | resume/delivery liveness の完全有限検査 | transport、backlog range、gap/replay、pending replay/snapshot、一度だけのdelivery、disconnect中のtick/default/ACK/resolve | `MAX_SEQ=6`、disconnect 1回、1 request/run。交渉・完全wire履歴・scoringは除外 |

`yamai_request_liveness.qnt` は、接続が最終的に安定した後の request scheduler を分離して調べるためのモデルである。`MAX_SEQ=6`、`MAX_TIMEOUT=2`、1 request/run と小さくし、single と group の初期分岐をTLCで完全列挙できるようにしている。`deadline == 0` では新規actionを受け付けず、defaultは `pending_members.exclude(responded_members)` だけへ適用する。extendedでは、同じ request 内部処理がdisconnect、`ReplayPending`、`SnapshotPending` でも止まらないことを別に表現する。

`yamai_resume_delivery.qnt` は、request内部の進行とpeer deliveryを分離するモデルである。接続が切れてもhostのdeadline、default、既受信actionのACK、resolveは進み、生成されたhost messageは `backlog_from_seq..backlog_to_seq` に保持される。replay/snapshotは一度だけbacklogを消費する。full extendedはこのモデルと重複するlivenessを探索せず、安全性と到達性witnessだけを検査する。

## 規範要件とモデルの対応

行番号は現在の規範文書の行番号である。wire上のJSON memberや暗号学的性質を、Quintの有限tag・整数・集合へ写像して検査している。

| 規範要件 | Quint action / invariant / temporal / witness | 出典 |
|---|---|---|
| session と message kind の状態変更 | extended の `SessionState`、`send_hello`、`send_join_good`、`send_welcome`、`negotiation_invariant`、`witness_negotiated_active` | `docs/yamai-protocol.md:93-109` |
| version/profile/hash/capability の交渉と拒否 | `send_join_bad_version`、`send_join_bad_profile`、`send_join_bad_capability`、`reject_join`、`negotiation_invariant`、`witness_rejected_*` | `docs/yamai-protocol.md:195-240` |
| envelope seq、gap、同一seq再送、conflict、replay | extended の `host_emit_event`、`host_emit_gap_message`、`player_detect_gap`、`host_retransmit_exact`、`host_retransmit_conflict`、`player_accept_duplicate`、`player_reject_conflict`、`replay_progress`、`wire_invariant` と各 witness。gap delivery livenessは `yamai_resume_delivery.qnt` の `gap_replay_under_eventual_stable_connection` | `docs/yamai-protocol.md:165-193` |
| request の一意性・group member・timeout 境界 | extended の `open_single_request`、`open_decision_group`、per-member `request_ids`、`request_lifecycle_invariant`、`request_id_invariant`、`request_capacity_invariant` | `docs/yamai-protocol.md:577-618` |
| action、ACK、terminal/default、期限後の扱い | `submit_action`、`ack_single`、`ack_group_one/two`、`default_action`、`request_action/status_invariant`。小型モデルでも同じ性質を `request_data_invariant` と temporal で完全検査 | `docs/yamai-protocol.md:620-666`, `:701-735` |
| group の優先順位と atomic resolve | extended の `resolve_group`、`GroupOpen/Closed/Resolved`、seq reservation と request safety。内部 request liveness は `yamai_request_liveness.qnt` の `group_resolves_under_stable_connection`、優先順位そのもの（hora/pon/chi）は未モデル | `docs/yamai-protocol.md:668-683`, `Appendix A:1174-1194` |
| disconnect/resume と pending request の保存 | extended の `disconnect`、`resume`、`begin_resume_replay`、`finish_resume_replay`、`emit_pending_snapshot`、`finish_snapshot_resume`、`resume_pending_invariant`、`resume_snapshot_invariant`。`backlog_pending/backlog_from_seq/backlog_to_seq` が、wire送信不可のhost結果をresume replay/snapshotへ渡す有限抽象。delivery livenessは `yamai_resume_delivery.qnt` で検査 | `docs/yamai-protocol.md:888-898` |
| resume/replay の liveness | `yamai_resume_delivery.qnt` の `pending_delivery_under_eventual_stable_connection` と `gap_replay_under_eventual_stable_connection`。仮定は `weakFair(resume, state)`、delivery progress fairness、および `eventually(always(state.transport == Connected))` | `docs/yamai-protocol.md:894-898` |
| end_kyoku/end_game 前の terminal 化 | `end_kyoku`、`end_game`、`terminalization_invariant`、`terminal_state_is_quiescent`、`witness_terminalized_end` | `docs/yamai-protocol.md:460-465`, `:477-577`, `Appendix A:1192-1194` |
| score/kyotaku の全体保存則 | `score_conservation`、reach の抽象控除と `settle_kyotaku`。実際の点数計算は未モデル | `docs/yamai-protocol.md:460-465`; `docs/riichi-4p-rules.md:238-280` |

### liveness の環境仮定

extended の host内部 terminal化・resolve は transport delivery とは別の性質であり、disconnect、`ReplayPending`、`SnapshotPending` でも deadline tick、timeout/default、既受信actionのACK、group resolveを止めない。内部 request liveness は `yamai_request_liveness.qnt`、transport/backlog/gap/replay/snapshot delivery は `yamai_resume_delivery.qnt` へ分割して完全有限検査する。full extended は状態の組合せが大きいため、安全性と到達性witnessだけを保証し、livenessの結果はこの2分割モデルの結果として扱う。

resume-delivery 小型モデルの replay/snapshot delivery liveness は無条件ではない。無限に disconnect/resume を繰り返す環境では、peerへのdeliveryを保証できないため、次の property は式名のとおり `weakFair` に加えて「ある時点以後ずっと transport が `Connected`」という仮定を antecedent に含む。

- `gap_replay_under_eventual_stable_connection`
- `pending_delivery_under_eventual_stable_connection`

request liveness の `group_resolves_under_stable_connection`、`timeout_closes_under_stable_connection`、`late_ack_survives_default_under_stable_connection` は、接続を常時安定とした別モデルで検査する。

これは host process の稼働、scheduler の fairness、単調時計の進行、最終的な接続安定を仮定する。host crash、scheduler starvation、clock halt、恒久的なtransport不通や無限再切断での peer delivery は主張しない。現行の規範本文にもこの制限が明示されており（`docs/yamai-protocol.md:683`, `:898`）、このREADMEとモデルはその制限を `eventually(always(state.transport == Connected))` という検査可能な前提へ具体化している。

## モデル間の関係と限界

`yamai_protocol_core.qnt`を正準モデルとし、`refinement_session`、`refinement_requests`、`refinement_wire`、`refinement_resume`を合成した`refinement_mapping`で、有限化した具体状態が規範Protocol Coreの状態制約を満たすことを検査する。append-only ledger、delivery/applied順序、request map、group linearization、clock/deadline、ACK結果は`protocol_invariant`の構成要素である。

既存4モデルは、canonical modelの特定側面をより小さい状態空間で調べる補完モデルである。canonical modelから既存4モデルへの機械的なtrace inclusion/composition theoremは定義していないため、既存モデルのTLC成功だけからcanonical modelや実装の適合性を導出してはならない。また、`refinement_mapping`の成功も下記の有限境界内の主張であり、無制限状態や実装コードへの証明ではない。

特に、複数の同時 request、複数 decision group、group間の優先順位、複数 pending snapshot/replay、複数回の disconnect/resume、wire backlog と request state の全組合せを一つのモデルで網羅していない。今回の検査は、full extended の safety/witness、request liveness の小型モデル、resume/delivery liveness の小型モデルという分割ごとの主張に限定される。

## 抽象化・非対象

### `yamai_protocol.qnt`

- hello/join/welcome の version、profile revision/hash、capability negotiation
- wire上の gap/duplicate/conflict、byte-for-byte replay、実際のID文字列
- pending request を含む本格的な resume/replay/snapshot
- visibility、seat別秘匿、認証、TLS、frame parser、JSON Schema
- `superseded`、`stale`、`rejected` ACKの全ポリシー
- MJAIの牌姿、legal actionの完全集合、役・符・ドラ・複数ron・pao・noten・chombo・ranking
- 実時間と単調時計。`deadline` は有限tickへ抽象化する

### `yamai_protocol_extended.qnt`

- negotiation のwire JSON、JCS/hash計算、Protocol Versionの文字列互換性の完全な検証
- capabilityごとの実装能力、receive limit、mode/view/target、visibility projection、seat秘匿
- wire seq の全履歴、実際の gap range、全 message のbyte-for-byte内容、pending resume/replay履歴の永続化
- 1つの pending snapshot と有限の replay progress のみ。複数game・複数session・tokenの暗号学的乱数・期限検証
- `superseded`、`stale`、`rejected` の全ACK状態、invalid-action policyの全分岐
- 実時間、flush/frame分割、backpressure、latency、host crash、scheduler/clock故障
- `riichi-4p` の合法性、役・符・ドラ・支払・複数ron・pao・noten・chombo・ranking の完全scoring。`score_conservation` は全体保存則だけ
- `end_kyoku`/`end_game` payload、優先順位、next policyの全意味論

### `yamai_resume_delivery.qnt`

- negotiation、profile/capability、JSON Schema、実際のmessage payloadとbyte-for-byte履歴
- 複数request/member、実際のrequest/action ID、複数回の切断・resume、token期限・認証
- snapshotの完全なstate projection、replay範囲の全message、実時間のdeadline/time bank
- gapは1回、resume deliveryは1回の有限抽象。`backlog_from_seq..backlog_to_seq` は未配送host結果の存在と一度だけの消費を表す
- scoring、visibility、transport frame/backpressure、host crashやscheduler故障

### `yamai_request_liveness.qnt`

- session/negotiation/wire seq/resume/snapshot/end_game
- 接続切断・遅延・再接続。モデル内では `stable_connection` が常に true
- 実時間の millisecond、time bankの数値計算、schedulerの実装。`deadline` は2 tickへ縮小
- 実際の legal action object、JSON member、request/action ID文字列、priority/scoring
- 1 runで1 requestだけ。requestを連続発行する資源競合・複数gameは扱わない

したがって、これらのモデルの成功は、有限境界と上記の環境仮定の下での制御フロー性質だけを示す。実装適合には JSON Schema、registry、公式vector、frame parser、semantic validator および独立した相互運用試験を併用すること。

## Nix 環境での実行

ルートの `flake.nix` が提供するNix環境を使用する。通常は次のように実行する。

```sh
nix develop --command quint --version
```

### parse / typecheck

```sh
nix develop --command quint parse verification/quint/yamai_protocol_core.qnt
nix develop --command quint typecheck verification/quint/yamai_protocol_core.qnt
nix develop --command quint parse verification/quint/yamai_protocol_core_bounded.qnt
nix develop --command quint typecheck verification/quint/yamai_protocol_core_bounded.qnt
nix develop --command quint parse verification/quint/yamai_protocol.qnt
nix develop --command quint typecheck verification/quint/yamai_protocol.qnt
nix develop --command quint parse verification/quint/yamai_protocol_extended.qnt
nix develop --command quint typecheck verification/quint/yamai_protocol_extended.qnt
nix develop --command quint parse verification/quint/yamai_request_liveness.qnt
nix develop --command quint typecheck verification/quint/yamai_request_liveness.qnt
nix develop --command quint parse verification/quint/yamai_resume_delivery.qnt
nix develop --command quint typecheck verification/quint/yamai_resume_delivery.qnt
```

### canonical Protocol Core のbounded safety/refinement

full canonical modelのTLC完全探索は状態空間が急増するため、CIでは同じ主要状態射影を持つphase-driven bounded modelの決定的traceを最後まで実行する。

```sh
nix develop --command quint run \
  --main yamai_protocol_core_bounded \
  verification/quint/yamai_protocol_core_bounded.qnt --max-steps 24 \
  --max-samples 1 --seed 0x79616d61695f636f \
  --invariants protocol_invariant refinement_mapping \
  --witnesses witness_complete
```

これはbounded modelの決定的conformance traceであり、状態空間の完全探索、full canonical model、無制限実装の完全証明ではない。リリース証拠にはtrace結果と、full canonical modelで完走・打切りした探索範囲を区別して記録する。

### extended の safety

```sh
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_protocol_extended.qnt \
  --invariants type_ok host_seq_non_decreasing negotiation_invariant \
  wire_invariant request_invariant resume_snapshot_invariant \
  resume_pending_invariant terminalization_invariant score_conservation
```

extended は状態組合せが大きいため、上の safety invariant と witness だけを実行する。liveness は次の2分割モデルで検査する。

### request liveness の完全検査

```sh
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_request_liveness.qnt \
  --invariants type_ok host_seq_non_decreasing capacity_invariant \
  request_lifecycle_invariant request_data_invariant protocol_invariant
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_request_liveness.qnt \
  --temporal stable_connection_is_preserved
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_request_liveness.qnt \
  --temporal group_resolves_under_stable_connection
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_request_liveness.qnt \
  --temporal timeout_closes_under_stable_connection
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_request_liveness.qnt \
  --temporal late_ack_survives_default_under_stable_connection
```

### resume delivery の完全検査

```sh
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_resume_delivery.qnt \
  --invariants type_ok host_seq_non_decreasing backlog_invariant \
  gap_invariant request_invariant pending_saved_invariant \
  disconnected_internal_invariant protocol_invariant
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_resume_delivery.qnt \
  --temporal internal_terminalization_under_fairness
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_resume_delivery.qnt \
  --temporal gap_replay_under_eventual_stable_connection
nix develop --command quint verify --backend tlc \
  verification/quint/yamai_resume_delivery.qnt \
  --temporal pending_delivery_under_eventual_stable_connection
```

### witness による到達性確認

temporal formula の antecedent が到達不能であることによる vacuous truth を避けるため、次の witness をシミュレーションで確認する。

```sh
nix develop --command quint run \
  verification/quint/yamai_protocol_extended.qnt \
  --main yamai_protocol_extended \
  --invariants type_ok host_seq_non_decreasing negotiation_invariant \
  wire_invariant request_invariant resume_snapshot_invariant \
  resume_pending_invariant terminalization_invariant score_conservation \
  --witnesses witness_negotiated_active witness_rejected_version \
  witness_rejected_profile witness_rejected_capability witness_gap \
  witness_duplicate witness_conflict witness_replayed witness_request_group \
  witness_deadline_boundary witness_pending_resume witness_pending_snapshot \
  witness_terminalized_end --max-steps 32 --max-samples 2000

nix develop --command quint run \
  verification/quint/yamai_request_liveness.qnt \
  --main yamai_request_liveness \
  --invariants type_ok host_seq_non_decreasing capacity_invariant \
  request_lifecycle_invariant request_data_invariant protocol_invariant \
  --witnesses witness_single_open witness_group_open witness_group_closed \
  witness_timeout_boundary witness_defaulted witness_late_ack_pending \
  witness_late_ack_acked witness_single_resolved witness_group_resolved \
  --max-steps 20 --max-samples 1000

nix develop --command quint run \
  verification/quint/yamai_resume_delivery.qnt \
  --main yamai_resume_delivery \
  --invariants type_ok host_seq_non_decreasing backlog_invariant \
  gap_invariant request_invariant pending_saved_invariant \
  disconnected_internal_invariant protocol_invariant \
  --witnesses witness_disconnect_default witness_disconnect_resolve \
  witness_reconnect_replay witness_snapshot_replacement witness_gap_recovery \
  --max-steps 24 --max-samples 5000
```

`quint run --witnesses` は各 predicate が少なくとも1 traceで成立したことを報告する。TLC temporal の成功だけでは到達性を保証しないため、witness結果を併記する。
