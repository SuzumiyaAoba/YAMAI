# YAMAI の Quint 検証モデル

このディレクトリはYRC 0003 draft.6の制御フローを有限状態へ射影した4モデルを収録する。JSON parser、麻雀の合法手・点数エンジン、認証実装を置き換えるものではない。実際のwireと採点の検査範囲は[検証ガイド](../README.md)および公式vectorを併せて確認する。

## 4モデルの範囲

| モデル | 検証する対象 | 有限境界と解釈 |
|---|---|---|
| [yamai_protocol.qnt](yamai_protocol.qnt) | session、選択固定→優先順位→ACK→結果、timeout、切断・再開、snapshot、供託の保存則 | seq上限12、期限2、2人の変動する反応者。3人目はnoneで確定済みとし、その定数の通信を射影から除く。seqはこの射影の通信順序を数える |
| [yamai_protocol_extended.qnt](yamai_protocol_extended.qnt) | version/profile/hash/capabilityの交渉、gap/重複/衝突、要求、未配送の履歴範囲、snapshot、終局 | seq上限8、2人の変動する反応者と1人の確定済みnone。交渉値・payload同一性は有限tag。安全性と到達性を検査する |
| [yamai_request_liveness.qnt](yamai_request_liveness.qnt) | 1人の自摸判断または**3人全員**の反応group、個別期限、3種類のron policy、ACK順序、原子的な結果公開 | 1 decision/run、放銃者0、他家1～3、各peerの相対seq 0～3、期限0～2、接続は常時安定 |
| [yamai_resume_delivery.qnt](yamai_resume_delivery.qnt) | 1peerの履歴・受信位置、有限replay範囲、追加backlog、snapshot、切断中の内部処理、一度だけの適用 | 最大6message、1decision、切断1回、gap1回、snapshot1回。他のgroup memberはothers_readyで要約する。履歴はmessage種別の固定tagを保持する |

baseline/extendedの2反応者は実対局の2人groupを許可する意味ではない。実対局ではYRC 0003に従って3人全員へ要求する。3人目の応答・期限も変動する場合と三家和はrequest_livenessで検査する。baseline/extendedのseqをそのまま4本のwireへ割り当ててはならない。peerごとのseqはrequest_liveness、実際にどこまで届いたかはresume_deliveryが扱う。

## 規範との対応

| 要件 | 対応するモデル上の操作・性質 | 規範 |
|---|---|---|
| 交渉の一致と拒否 | extendedのsend_hello/send_join_*/send_welcome/reject_join、negotiation_invariant | [YRC 0003](../../docs/yamai-protocol.md) §6 |
| seqの増加、欠落・重複・内容衝突 | extendedのwire_invariant、host_replay_range、player_*、各witness。deliveryのhistoryとapply_counts | YRC 0003 §5・§12 |
| 全選択を固定してからACKを生成 | baseline/extendedのGroupOpen→GroupClosed→decided、request_lifecycle_invariant。requestのOpen→Closed→Decided→Acked→Resolved | YRC 0003 §8・§9 |
| 個別期限、未応答だけのdefault、期限前の選択を保持 | requestのremaining/original_deadlines/selected_at、default_member、request_data_invariant、late_ack_* | YRC 0003 §8.1・§9.1 |
| 優先順位、頭ハネ、三家和、noneとsupersededの区別 | requestのexpected_chosen/expected_ack、sanchahoTest、selectionBeforeAckTest | YRC 0003 §8.4 |
| 切断で時計・ACK・内部解決を止めない | deliveryのinternal_progressとdisconnected_internal_invariant。baseline/extendedも内部操作をConnectedで制限しない | YRC 0003 §8.4・§13 |
| 新規メッセージを既存replayへ混ぜない | deliveryのfrozen_frontier/replay_through、extendedのreplay_to_seq。replay終了後のtailを残す | YRC 0003 §13.2 |
| hostの生成とpeerの適用を区別 | deliveryのprevious_applied_seq/last_delivery/apply_counts。hostだけの操作はapplied_seqを変えない | YRC 0003 §5・§13 |
| snapshotで選択済み要求を保持 | deliveryのsnapshot_phase/snapshot_choice、extendedのsnapshot_saved_*、具体的runテスト | YRC 0003 §13.3 |
| 終了済みsessionへ未配送結果を届ける | baselineのendedReplayTest、extendedのendedResumeTest | YRC 0003 §13.2 |
| 局結果・終局前に要求を解決 | extendedのround_result_available、end_kyoku/end_game、terminalization_invariant | YRC 0003 §7.5・Appendix A |
| 供託を含む全体の点数保存 | baseline/extendedのscore_conservation、reach控除とsettle_kyotaku | YRC 0003 §7.2・§7.6.8 |

## 実行方法と検査結果

環境はrootの[flake.nix](../../flake.nix)と[flake.lock](../../flake.lock)で固定する。リポジトリのルートで全検査を実行する。

```sh
nix flake check path:. --no-update-lock-file
```

各モデルは独立に検査できる。各gateはartifact validatorとtoolchainに依存し、そのモデルのparse/typecheck、安全性、必要な時間的性質、run/witnessを順に実行する。モデル間に任意の成功依存を作らない。aarch64-darwin以外ではsystem名を環境に合わせる。

```sh
nix build path:.#checks.aarch64-darwin.quint-model-witnesses --no-link
nix build path:.#checks.aarch64-darwin.quint-model-extended-witnesses --no-link
nix build path:.#checks.aarch64-darwin.quint-request-liveness-witnesses --no-link
nix build path:.#checks.aarch64-darwin.quint-resume-delivery-witnesses --no-link
```

個別の安全性検査と具体的操作列は次のように実行する。他モデルもファイル名を置き換える。

```sh
nix develop --command quint parse verification/quint/yamai_resume_delivery.qnt
nix develop --command quint typecheck verification/quint/yamai_resume_delivery.qnt
nix develop --command quint verify --backend tlc verification/quint/yamai_resume_delivery.qnt --invariant protocol_invariant
nix develop --command quint test verification/quint/yamai_resume_delivery.qnt
```

Nixの各出力にはquint-verify.log、時間的性質がある場合はquint-verify-temporal.log、quint-tests.log、quint-witness.logを残す。checkの正確なコマンドと不変条件一覧は[flake.nix](../../flake.nix)に固定する。

TLC backendはq_init/q_stepの有限到達状態を検査する。CLIのmax-stepsを用いたランダムシミュレーションの成功と混同しない。runはTLCの状態機械から除かれ、quint testで別途実行する。

## 時間的性質と前提

| モデル | 時間的性質 |
|---|---|
| baseline | host_seq_bounded、ended_state_is_quiescent、group_resolves_under_weak_fairness、timeout_closes_under_weak_fairness、resume_returns_under_weak_fairness |
| request | stable_connection_is_preserved、group_resolves_under_stable_connection、timeout_closes_under_stable_connection、late_ack_survives_default_under_stable_connection |
| delivery | internal_terminalization_under_fairness、gap_replay_under_eventual_stable_connection、pending_delivery_under_eventual_stable_connection |

hostの内部進行は公平なschedulerを、期限の進行は公平なtickを前提とする。deliveryの配送保証はさらに `eventually(always(transport == Connected))` を仮定する。恒久切断、host停止、時計停止、scheduler starvationでの配送は主張しない。host処理とpeer配送は別の義務であり、切断中も前者は進む。

TLCだけで前提状態の到達性は保証されないため、witnessと具体的runを併用する。requestのsanchahoTestは三家和へ至る操作列を検査する。deliveryのfrozenFrontierTailTestは空のreplay範囲を確定した後にACK/結果を生成し、その追加分を後から正常配送できることを確認する。

## 検証の限界

4モデルを合成する形式的なrefinement/composition theoremは定義していない。モデル同士の名前が似ていることから、相互の性質を自動的に導いてはならない。各モデルの有限境界と前提の下での結果として扱う。

実際のJSON/UTF-8/frame、byte-for-byteの全履歴、暗号学的token、認証・認可、全visibility、実時間・ネットワークlatency、host crash、複数game、無限の切断再接続、全てのエラー・冪等性・chombo、牌の所有と合法手は完全にはモデル化していない。baseline/extendedの点数は25点×4人、供託1点の保存則だけであり、役・符・本場・責任払いの計算はscoring_referenceと公式fixtureで検査する。

形式検査の成功を、独立実装同士の相互運用試験や安定版の公開条件の充足と表明してはならない。

## 並列実行時の検証サーバー

Nix環境のquint wrapperは、verify/compileごとに未使用のloopback portを選び、Apalacheのserver-endpointを分離する。複数の検査が同じサーバーを共有することを防ぐためであり、明示した `--server-endpoint` は尊重する。
