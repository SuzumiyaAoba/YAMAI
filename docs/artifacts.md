# YAMAI draft 1 成果物の仕様

本書は、`yamai-1.0-draft.1` を構成する文書、Schema、registry、テストベクトルと検査実装の関係を定義する。通信・対局・採点の規範は [YAMAI 仕様書](yamai-protocol.md) に従う。

[仕様案内](README.md) / [規範本文](yamai-protocol.md) / [検証ガイド](../verification/README.md)

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

release manifest の `published` は `false` であり、本成果物は draft 1 である。開発中の draft を比較・検査するときは対象 commit を記録する。`required_git_tag` は公開時に必要となる tag 名を示し、tag や release が既に公開済みであることの証明ではない。公開された組を取得する場合は、全成果物をその同じ commit と tag から取得する。

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

## 文書の原本と生成物

Git 管理する Markdown を編集し、HTML は [render_docs.py](../scripts/render_docs.py) で再生成する。生成 HTML を直接編集しても原本には反映されない。MDX と HTML は [.gitignore](../.gitignore) により Git 管理対象外で、release manifest にも含めない。

| 原本 | 生成先 | 位置づけ |
|---|---|---|
| [docs/README.md](README.md) | `docs/index.html` | 仕様案内・閲覧の入口 |
| [docs/yamai-protocol.md](yamai-protocol.md) | `docs/yamai-protocol.html` | 規範本文の閲覧用 |
| [docs/artifacts.md](artifacts.md) | `docs/artifacts.html` | 成果物の関係と更新手順 |
| [verification/README.md](../verification/README.md) | `verification/index.html` | 検証ガイド |
| [verification/quint/README.md](../verification/quint/README.md) | `verification/quint/index.html` | 有限モデルの説明 |
| ローカルにある `docs/auth-implementation-plan.mdx` | `docs/auth-implementation-plan.html` | 任意の認証実装計画。規範本文ではない |
| ローカルにある `docs/spec-review.mdx` | `docs/spec-review.html` | 任意のレビュー。規範本文ではない |

リポジトリのルートで、Python 3、Node.js、npm を使用して実行する。既定では mdxr `0.2.0` を npm 経由で呼び出すため、未取得ならパッケージの取得が必要になる。

```sh
python3 scripts/render_docs.py
```

同じ版の mdxr をローカルに用意している場合は `python3 scripts/render_docs.py --mdxr /path/to/mdxr` も使用できる。生成後は `docs/index.html` を開く。文書間のリンクは生成先に合わせて変換し、Schema・registry・script へのリンクは元ファイルを参照するため、HTML だけを別ディレクトリへ移すと相対リンクが失われる。

## 変更時の確認手順

1. 規範の変更か、説明・導線の整理かを区別する。規範の意味は [仕様書](yamai-protocol.md) を基準とし、参考文書だけで変更しない。
2. 対象の Markdown と関連する Schema・registry・vector・検査を照合する。規範の節名や anchor は参照元があるため、変更が必要ならリンクも点検する。
3. [profile hash の7入力](#profile-hash)を変えた場合は、正規化規則と対応する hash を照合する。Markdown や HTML のみの変更を理由に hash を作り直さない。
4. [検証ガイド](../verification/README.md#実行方法)に従って、成果物検査と変更箇所に対応する回帰・独立検査を実行する。実行した対象 commit、環境、成功・失敗・未実行の範囲を区別して記録する。
5. HTML を再生成し、原本と生成物の両方でリンク・コード例・表を確認する。文書を追加・移動した場合は renderer の対応表と manifest の対象も点検する。

文書整理だけでは protocol の版、profile revision、公開状態は変えない。公開・適合表明の条件は[規範本文第16–17節](yamai-protocol.md#16-成果物と版の一致)と本書の[検証と適合表明](#検証と適合表明)に従う。

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

namespaced memberの内部にmessage形のobjectや `wire` 文字列があっても、それは交渉identityではないため正規化しない。Schema入力全体と拡張fixtureの `schema` / `message_schemas` も不透明なSchemaデータとしてhashへ含め、const・default・exampleの変更を保持する。

規範本文、release manifest、protocol message Schema は profile hash の入力に含めない。protocol Schema とその参照先は Protocol Version で固定し、文書を含む組全体は release manifest と同一 commit/tag で特定する。hash の一致は、認証・認可や相互運用性の成立を意味しない。

## Manifest の契約

[release-manifest.json](../release-manifest.json) は、規範文書、案内文書、全 Schema、registry、テストベクトル、validator と補助ファイル、採点 CLI、形式モデルを列挙する。`artifact_contract` は本書を参照する。

[vector manifest](../test-vectors/protocol/1.0-draft.1/manifest.json) は、protocol/profile の版とhash、参照する成果物、および全公式ケースのID・検査領域・期待する正負結果を列挙する。各IDは `vectors.json` のキーと一対一で対応する。

検査は、版とhashの一致、重複ID、存在しないファイル、repository外の参照、Schemaの未解決参照と未対応keyword、registryとSchemaの不一致を拒否する。規範本文の JSON 例も同じ Schema と意味検査へ通す。

公式vectorの `negative_variants` は、messageならSchemaとmessage意味検査、`trace` wrapperなら主正例・主負例と同じtrace意味検査へ通す。trace wrapperがmessage Schemaに適合しないことを、そのtraceの意味上の不正を確認した証拠にしてはならない。

## 検証と適合表明

```sh
python3 scripts/validate_artifacts.py
```

このコマンドは JSON、Schema、registry、manifest、profile hash、公式ベクトル、採点 fixture と本文例を検査する。独立した Draft 2020-12 検査、回帰テスト、形式モデルの実行手順と適合項目との対応は [検証ガイド](../verification/README.md) にある。

完全適合を表明するには [仕様書第17節](yamai-protocol.md#17-適合性) の要件を満たす。有限モデルの検査結果は境界と前提を伴って示し、実装間の相互運用試験も対象の版・profile・capability とともに確認する。
