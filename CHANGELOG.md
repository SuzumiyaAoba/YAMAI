# 変更履歴

YAMAI の仕様と成果物の変更、および版間の互換性を記録する。各版は Draft であり、現行成果物は [release manifest](release-manifest.json) に従って取得する。

## 未公開の追補 — 2026-09-16

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
