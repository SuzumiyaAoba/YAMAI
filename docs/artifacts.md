# YAMAI draft 1 成果物の仕様

本書は、`yamai-1.0-draft.1` を構成する文書、Schema、registry、テストベクトルと検査実装の関係を定義する。通信・対局・採点の規範は [YAMAI 仕様書](yamai-protocol.md) に従う。

## 版と適用範囲

| 識別子 | 値 | 固定する対象 |
|---|---|---|
| Protocol Version | `1.0-draft.1` | メッセージ、交渉、状態遷移、message Schema と参照先 |
| Profile | `riichi-4p` | 4人リーチ麻雀のルールと採点 |
| Profile revision | `1.0-draft.1` | profile の規範と派生成果物 |
| Profile hash | `sha256:` に続く64桁の小文字hex | 下記7入力の正規化された内容 |
| Release ID / tag | `yamai-1.0-draft.1` | manifest に列挙した成果物一式 |
| WebSocket subprotocol | `yamai.1.draft1` | WebSocket 上で選択するプロトコル |

`profile_hash` の確定値は [release manifest](../release-manifest.json) と [protocol registry](../registry/protocol/1.0-draft.1/registry.json) に記録する。Protocol Version、profile 名、revision、hash の組を交渉で検査し、値を個別に照合しただけで対応する組とみなしてはならない。

release manifest の `published` は `false` であり、本成果物は draft 1 である。公開された組を取得する場合は manifest の `required_git_tag` に従い、全成果物を同じ commit と tag から取得する。

## 文書と検査の責務

| 成果物 | 役割 |
|---|---|
| [仕様書](yamai-protocol.md) | 通信と `riichi-4p` の規範本文。派生成果物と競合する場合の基準 |
| JSON Schema | 型、必須member、範囲、列挙、参照関係の機械検査 |
| Registry | profile、capability、message、event、action、error、rule、役・符の識別子と許可値 |
| 公式ベクトル | 正例と負例、交渉、状態遷移、再送、秘匿、要求、局進行の具体的入力と期待結果 |
| Stateful trace | 時刻付きメッセージ、wire ledger、request、ACK、transaction、snapshot の照合 |
| 採点 fixture | 完全な手牌・ルール・局面と、役・符・支払い・点数の期待結果 |
| Python 検査実装 | 構文、Schema、交渉、状態、候補、採点と成果物間の整合検査 |
| Quint モデル | 有限境界と環境仮定の下での安全性・到達性・時間的性質 |
| HTML | Markdown・MDXから生成する閲覧用文書。認証の実装計画・レビューは参考文書であり、規範本文を追加しない |

Schema は JSON の重複キー、frame 境界、全状態遷移、実時間、点数保存などを単独では保証しない。採点 CLI と artifact validator は同じ `scoring_reference.py` を使い、二つの独立した採点実装として数えない。

## 配置と参照

| 区分 | Protocol | `riichi-4p` |
|---|---|---|
| Schema ディレクトリ | `schemas/protocol/1.0-draft.1/` | `schemas/riichi-4p/1.0-draft.1/` |
| Registry | [registry.json](../registry/protocol/1.0-draft.1/registry.json) | [registry.json](../registry/riichi-4p/1.0-draft.1/registry.json) |
| テスト入力 | [manifest.json](../test-vectors/protocol/1.0-draft.1/manifest.json)、[vectors.json](../test-vectors/protocol/1.0-draft.1/vectors.json) | [scoring.json](../test-vectors/riichi-4p/1.0-draft.1/scoring.json) |

Schema ID は `urn:yamai:schema:protocol:1.0-draft.1:<name>` または `urn:yamai:schema:riichi-4p:1.0-draft.1:<name>`。registry ID は同じ名前空間の `urn:yamai:registry:...` とする。検査では `$ref` を同じ release のローカル Schema 集合から解決し、メッセージが指定する外部参照を取得しない。

[message.schema.json](../schemas/protocol/1.0-draft.1/message.schema.json) は9種類の標準メッセージの入口である。[join-proposal.schema.json](../schemas/protocol/1.0-draft.1/negotiation/join-proposal.schema.json) は、版の選択値を固定せず交渉入力の構造を検査する。ホストは構造検査の後で、版、profile、hash、mode/view、capability、limit、target/resume の順に交渉条件を検査する。

## Profile hash

`profile_hash` は次の7 member を持つ object を作り、RFC 8785 の JCS で正規化して、UTF-8 byte 列に SHA-256 を適用した値である。入力ファイルは vector manifest の `profile_hash_inputs` と release manifest の `profile_hash_scope.inputs` にも記録する。

| 投影member | 入力ファイル |
|---|---|
| `profile_schema` | [profile/riichi-4p.schema.json](../schemas/protocol/1.0-draft.1/profile/riichi-4p.schema.json) |
| `rules_schema` | [riichi-4p-rules.schema.json](../schemas/riichi-4p/1.0-draft.1/riichi-4p-rules.schema.json) |
| `scoring_vectors_schema` | [scoring-vectors.schema.json](../schemas/riichi-4p/1.0-draft.1/scoring-vectors.schema.json) |
| `protocol_registry` | [protocol registry](../registry/protocol/1.0-draft.1/registry.json) |
| `scoring_registry` | [`riichi-4p` registry](../registry/riichi-4p/1.0-draft.1/registry.json) |
| `official_vectors` | [vectors.json](../test-vectors/protocol/1.0-draft.1/vectors.json) |
| `scoring_vectors` | [scoring.json](../test-vectors/riichi-4p/1.0-draft.1/scoring.json) |

自己参照を避けるため、計算前に次の正規化を行う。

1. protocol registry の `profiles[].hash` を除外する。
2. 投影中の `hello.profiles[].hashes` の値、`join.profile_hash`、`welcome.profile_hash` のうち、正しい `sha256:` 付き64桁小文字hex文字列をゼロhashへ置換する。
3. `wire` に保存した JSON でも、同じ位置の identity hash の文字列tokenだけを置換する。復号したmember名と配列・object内の位置で対象を判定する。
4. 他のmember、注釈、配列、空白、順序、escape表記を維持する。Schema の `properties` と、否定試験に含まれる型不正な値は置換しない。

規範本文、release manifest、protocol message Schema は profile hash の入力に含めない。protocol Schema とその参照先は Protocol Version で固定し、文書を含む組全体は release manifest と同一 commit/tag で特定する。hash の一致は、認証・認可や相互運用性の成立を意味しない。

## Manifest の契約

[release-manifest.json](../release-manifest.json) は、規範文書、案内文書、全 Schema、registry、テストベクトル、validator と補助ファイル、採点 CLI、形式モデルを列挙する。`artifact_contract` は本書を参照する。

[vector manifest](../test-vectors/protocol/1.0-draft.1/manifest.json) は、protocol/profile の版とhash、参照する成果物、および全公式ケースのID・検査領域・期待する正負結果を列挙する。各IDは `vectors.json` のキーと一対一で対応する。

検査は、版とhashの一致、重複ID、存在しないファイル、repository外の参照、Schemaの未解決参照と未対応keyword、registryとSchemaの不一致を拒否する。規範本文の JSON 例も同じ Schema と意味検査へ通す。

## 検証と適合表明

```sh
python3 scripts/validate_artifacts.py
```

このコマンドは JSON、Schema、registry、manifest、profile hash、公式ベクトル、採点 fixture と本文例を検査する。独立した Draft 2020-12 検査、回帰テスト、形式モデルの実行手順と適合項目との対応は [検証ガイド](../verification/README.md) にある。

完全適合を表明するには [仕様書第17節](yamai-protocol.md#17-適合性) の要件を満たす。有限モデルの検査結果は境界と前提を伴って示し、実装間の相互運用試験も対象の版・profile・capability とともに確認する。
