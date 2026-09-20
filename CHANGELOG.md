# 変更履歴

YAMAI の仕様と成果物の変更、および版間の互換性を記録する。各版は Draft であり、現行成果物は [release manifest](release-manifest.json) に従って取得する。

## draft.9への未公開の追補 — 2026-09-20

- 再開replayの `original_seq` 禁止規則が参照する節番号を、存在しない `11.2` から replay mode を規定する `11` へ修正した。
- snapshotの `turn.phase == "resolving"` を、decision groupのlinearization point記録から全memberの終端ACKと結果event列のtransaction確定までの卓の状態と定義した。この期間にrequestが未終端のplay seat向けには、終端ACKが対応するrequestを欠くため、transactionの確定までsnapshotを生成してはならないことを明記した。
- replay snapshotの `state.original_seq` をmember名として明示した。wire・Schema・vectorの意味は変更していない。

## 1.0-draft.9 / profile 1.0-draft.7 — 2026-09-17（未公開）

- 途中流局のtenpaiをnull、点差を全seatゼロとし、直前の点数と供託を維持することを規定した。Schemaと意味検査を揃え、illegal_actionはpenaltyだけに制限した。
- accepted/defaulted ACKを元の候補へ対応付け、別の合法手や複合候補内の異なる打牌への置換を拒否する。槓宣言、和了・九種九牌、所定位置のdora・reach_accepted・paoも検査する。
- snapshotによる確定済みselectionの変更、OPENへの巻き戻し、終端requestの復活を拒否する。復元時にも元のdeadlineとbank消費式を適用し、不正なsnapshotを部分適用しない。
- 公式ベクトルV282〜V288と回帰テストを追加し、Protocol Version、profile revision/hash、Schema ID、registry、release manifestを更新した。

`1.0-draft.8` / profile `1.0-draft.6` とは非互換である。実装は成果物一式を更新し、WebSocket subprotocolに`yamai.1.draft9`を使用する。

## 1.0-draft.8 / profile 1.0-draft.6 — 2026-09-16（未公開）

- 履歴中のsnapshotを連続したseqで再送できるよう、欠落範囲を飛び越える復旧用snapshotと受信条件を分離した。古いsnapshotで今回の再送終端を覆う必要はなく、終端まで回復待ちを維持する。新規生成の理由はホスト側で制限・監査する。
- IDのSchemaを文字列全体へ一致させ、末尾の改行などを候補外actionやchomboとして扱う前にSchema違反として拒否する。
- 終局前に受け付けた後着actionへのstale通知は、end_game後にも一度だけ記録できることを明記した。終局後の新規actionは破棄し、次sessionの開始前に保留通知をcommitする。
- 参照受信実装の拡張event判定を仕様の`x-<owner>-<name>`へ合わせ、交渉済み拡張を含むsession全体を検査する。
- 有効だが未対応のmode/viewはunsupported_viewへ統一し、profile非対応・構造違反との検査順を定義した。
- hello.profilesは本文どおり1件以上とし、既存の正例を修正した。公式ベクトルV276〜V281、回帰テスト、独立Schema負例検査、終了後の保留通知を扱う有限モデルを追加した。
- Protocol Version、profile成果物のrevision/hash、Schema ID、registry、release manifestを一式更新した。profileの役・符・点数式は変更していない。

`1.0-draft.7` / profile `1.0-draft.5` とは非互換である。実装は成果物一式を更新し、WebSocket subprotocolに`yamai.1.draft8`を使用する。受信側は連続したsnapshotで回復待ちを早期終了せず、送信側は新規snapshotの生成理由と終局後の通知範囲を守る必要がある。

## 1.0-draft.7 / profile 1.0-draft.5 — 2026-09-16（未公開）

- 再開時の `start_game.scores` は開始点、`welcome.scores` は同期対象時点の点数とし、一致比較を新規sessionだけに限定した。
- 槍槓できない暗槓も、他家3seat全員への反応要求・選択・終端ACKを経て成立することを状態前後条件表へ統一した。
- 保持済みseqのbyte衝突判定をpayload Schema検査より先に行うよう、エラー優先順の表と本文を統一した。
- 局間snapshotの `next_kyoku` に `type` を要求していた参照実装を修正し、復元後の次局開始・自摸、座標不一致、早すぎる終局を回帰検査する。
- 開始点・本場・供託・流局・penaltyを含むゲーム全体の点数上界を定義した。Schemaの個別上限と意味検査の組合せ条件を満たさないrulesを、ゲーム開始前に拒否する。
- 公式ベクトルV271〜V275、採点負例N29と回帰テストを追加し、Schema、registry、profile hash、release manifestおよび現行版の参照先を更新した。

`1.0-draft.6` / profile `1.0-draft.4` とは非互換である。実装はProtocol Version、profile revision/hash、Schema・registry・vectorを一式で更新する。WebSocket subprotocolは `yamai.1.draft7` を使用する。通常の点数設定とメッセージ構造は維持するが、整数範囲を保証できない点数ルールは受理しない。

## draft.6への未公開の追補 — 2026-09-16

- draft.6の検査実装・公式ベクトル拡充と仕様レビューの修正を統合した。
- 初局の開始条件、複合リーチの宣言、槍槓判定が不要な槓の成立条件を状態前後条件表に反映した。
- 採点ID・期待値に依存しない計算、入力変更、sessionごとのseq、再開時の状態保持を回帰テストで検査する。
- 独立したsession ledgerと取消し後の状態を扱う有限モデルを追加し、5モデルをNixの検査対象にした。

## 1.0-draft.6 / profile 1.0-draft.4 — 2026-09-12

- プロトコルと採点規則を YRC 0003 にまとめ、唯一の規範本文とした。
- request を `OPEN`、`SELECTED`、`TERMINAL` に分離し、全選択の固定、競合判定、終端 ACK、結果 event の順序を定義した。decision group は他家3人を含み、`all_selected_or_deadline` で閉じる。
- deadline ちょうどは timeout を優先し、期限前の選択は後着 ACK でも有効とした。不正 action の再試行、選択後の再送、後着 action、chombo による取消しと time bank の扱いを固定した。
- 牌山、嶺上補充、合法手、フリテン、リーチ後の暗槓、喰い替え、連続槓の順序を明文化した。リーチ打牌が鳴かれてもリーチは成立し、一発を失う。暗槓の4枚は全 view で公開する。
- 延長上限、親のアガリ止め、penalty での同局やり直し、本場・供託の繰越を定義した。`next` は次局の `bakaze`・`kyoku` を含み、`end_game` の場合も `kyotaku` を必須とする。
- snapshot に局外の供託・次局・終局順位・time bank、鳴かれた捨て牌、槓ドラ保留、選択済み行動と残り期限を追加した。公開 view と他家の自摸番における pending request の条件を修正した。
- `welcome` に有効 capability 集合と `replay_through_seq` を追加した。観戦の途中参加、初回 snapshot、全 application message を含む replay の seq、token の一回使用・失効を定義した。
- profile revision と Protocol Version の対応表、選択前の join Schema、交渉の拒否順序、session 内の拡張 Schema 合成を定義した。新規 join の座席割当、resume の座席固定、replay target を明確化した。
- stateful trace で wire ledger、transaction、timeout、全 view の再帰的な情報投影を検査する。公式ベクトルには合法候補全体、牌136枚の保存、局進行、資源上限の検査を含めた。
- 採点入力から手牌の全分解、役・符・点数・支払いを再計算する形式を定義した。責任払いは該当役満の点数成分に適用し、本場の端数配分と供託の別会計を固定した。
- Schema、registry、公式ベクトル、検査実装、要求・配送・再開を扱う4つの有限形式モデルを同じ版として管理する。

`1.0-draft.5` / profile `1.0-draft.3` とは非互換である。実装は Protocol Version、profile revision、Schema、registry、ベクトル、hash を一緒に更新する。

## 1.0-draft.5 / profile 1.0-draft.3 — 2026-08-30

- Protocol Core の正準状態、message の適用前後条件、不変な wire ledger、transaction 境界を定義した。
- request の同時到着、timeout、線形化、ACK と採用 event の原子的な処理を明文化した。
- JSON Lines の frame 境界、mode 別の resume、session の開始・終了、decision group の期限、再接続中の時計進行を明確化した。
- 座席割当、game/recording の replay target、延長局の番号、snapshot を含む情報公開範囲を定義した。
- 採点の副露、本場・供託、切り上げ満貫、数え役満、責任払い、チョンボを明確化した。
- session を通した stateful trace、採点の再計算、Schema・registry・hash・公式ベクトルの整合性検査を追加した。

`1.0-draft.4` / profile `1.0-draft.2` とは非互換である。対応する版の成果物一式を使用し、異なる版を混在させない。

## 1.0-draft.4 / profile 1.0-draft.2 — 2026-08-30

- 規範本文、Schema、registry、テストベクトル、validator を release manifest で一覧化した。
- 成果物の権威関係、Protocol Version・profile revision/hash・release ID/tag の責務、互換性判定と公開手順を定義した。
- 採点ベクトルの Schema を追加した。
- 認証方式を対象外とし、公開の spectate/replay に別の認可 profile を要求した。

過去版の成果物は Git 履歴から取得する。変更提案と公開の手順は [仕様策定・リリースプロセス](docs/specification-process.md) を参照する。
