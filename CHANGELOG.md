# 変更履歴

この変更履歴は、YAMAI の規範文書、機械可読成果物およびリリース管理上の変更を記録する。現在のすべての項目は Draft であり、安定版を意味しない。

## 1.0-draft.5 / profile 1.0-draft.3 — 2026-08-30

### Changed

- YRC 0003 本文へ `riichi-4p` の全規範意味論を統合し、文書単体で自己完結する唯一の authority とした。Schema、registry、vector、oracle および Quint は派生成果物と明記し、本文優先、現行意味論不変および固定 profile hash（`sha256:811182d20eb1d33304913f3f9a91cfc68d9304a08230affff0ffb4ba21bdf5d5`）の責務を整理。
- YRC 0003 に、全 message の正準状態（Protocol Core）、原子的な `Apply` 契約、前後条件および検証層ごとの一意な error 選択規則を追加。
- host の wire ledger（seq、wire bytes、transaction）を正準化し、範囲 replay、resume、snapshot の `replaces_through_seq` と状態置換を byte-for-byte の規則として明文化。
- decision group の request 発行順、group/個別 deadline、単調時計の同値境界、lock 内 linearization、ACK と採用 event の原子 transaction を明文化。
- `play` の任意 seat 要求・最小空席割当と `welcome.seat` の関係、replay の game/recording target、reach 宣言取消しおよび延長局の通し番号・上限を修正。
- mode/view ごとの完全な visibility 射影を定義し、snapshot、`last_event`、pending request、self state および秘匿牌へ同じ投影を再帰適用する規則を追加。
- session全体を検査するstateful trace Schemaと正負vectorを追加し、join/welcome、seq ledger、request/ACK終端、group、timeout、snapshotおよびvisibilityをsemantic validatorで検査。
- 入力から役・符・点数・支払いを再計算する独立scoring oracleと、最大4件の並行request、単調時計、immutable ledger、resume/snapshotおよびrefinement mappingを持つCanonical Protocol Core Quintモデルを追加。
- YRC 0003 の Protocol Version を `1.0-draft.5`、`riichi-4p` profile の revision を `1.0-draft.3` へ更新。
- YRC 0003/YRC 0005 の規範本文、Schema、registry および test vector に非互換修正があるため、旧 draft と混在しないよう Protocol Version/profile revision を bump。
- 現行成果物に基づき、`riichi-4p` の profile hash を `sha256:811182d20eb1d33304913f3f9a91cfc68d9304a08230affff0ffb4ba21bdf5d5` へ更新。
- Schema、registry、vector の参照先を `1.0-draft.5` / `1.0-draft.3` のディレクトリへ移行。
- YRC 0003 の規範本文を、JSON Lines の frame 境界、mode 別の resume、`start_game`/`end_game` の順序、decision group の `group_start` と deadline、未解決 request の terminal 化、再接続時の時計進行および liveness の前提に合わせて明確化。
- YRC 0005 の規範本文を、meld の `open`、本場・供託を含む精算入力、切り上げ満貫・数え役満境界、親ツモ、責任払いおよびチョンボ精算に合わせて明確化。
- YRC 0003/YRC 0005 の Schema、registry、公式 vector を上記の規範変更へ同期し、spectate/replay target、重複 winner/bonus/pao、score の倍数制約、group deadline および scoring fixture の境界を検査対象へ追加。
- `scripts/validate_artifacts.py` に、Draft 2020-12 Schema の対応範囲を明示した検査、registry/Schema の discriminator 整合、release manifest と profile hash の整合、公式 vector の構文・意味検査を追加した。Quint/TLC の補助モデルは、request/group、再送・resume、timeout、terminal 化および score conservation の有限状態性質を検査するが、実装適合性や完全な scoring を保証しない。
- これらは未公開の現行 `yamai-1.0-draft.5` / Protocol `1.0-draft.5` / `riichi-4p` profile revision `1.0-draft.3` に対する同一 release 内の整合修正である。既存の draft5/draft3 bump が旧 draft4/draft2 との非互換境界を表しており、release manifest の `published` は `false` のため、今回さらに release ID、Protocol Version または profile revision を bump しない。公開 tag 後に同等の規範変更を行う場合は、仕様策定プロセスに従い新しい release ID と版を作成する。

### Added

- `scoring-vectors.schema.json` を `riichi-4p` の規範 Schema として現行 profile revision に追加。
- 規範成果物の権威関係、hash責務、互換性判定および公開手順を [`docs/specification-process.md`](docs/specification-process.md) に整理。

### Compatibility

- `1.0-draft.5` / `1.0-draft.3` は旧 `1.0-draft.4` / `1.0-draft.2` と互換とみなさない。Protocol Version、profile revision、profile hash および release tag の組を一致させること。
- 現行 release は、mode/target、seq・再送・request/ACK lifecycle、group deadline、scoring の境界および Schema の追加制約を含む。これらを実装していない旧実装は、hash が同じでない限り適合・互換と表明してはならず、現行 release の全公式 vector と validator を通過させる必要がある。

### Status

- 安定版の公開条件（公式 vector と二つ以上の独立相互運用実装）は未充足として扱う。
- 公開 release tag が付くまで、release manifest の `published` は `false` とする。

## 過去版: 1.0-draft.4 / profile 1.0-draft.2 — 2026-08-30

以下は過去版の履歴であり、現行成果物の参照には使用しない。

### Added

- YRC 0003 `1.0-draft.4` と `riichi-4p` profile revision `1.0-draft.2` の規範成果物を release manifest で一覧化。
- 規範本文、全 Schema、registry、test vector、validator の権威関係と適合性の境界を [`docs/specification-process.md`](docs/specification-process.md) に追加。
- Protocol Version、profile revision/hash、release ID/tag の責務と互換性判定を明文化。
- 認証方式が対象外であること、および public/spectate/replay には別の認可 profile が必要であることを明記。
- `scoring-vectors.schema.json` を `riichi-4p` の規範 Schema として追加。

### Changed

- YRC 0003/YRC 0005 の現在の成果物に合わせ、`riichi-4p` の profile hash を `sha256:e610bf40e4ad75e64a1d40a5add5a51f64151b22fa794e57382a906eec617824` に更新。

### Status

- 安定版の公開条件（公式 vector と二つ以上の独立相互運用実装）は未充足として扱う。
- 公開 release tag が付くまで、release manifest の `published` は `false` とする。
