# YAMAI — draft 1

**Yet Another Mahjong AI Interface**

YAMAI は、4人リーチ麻雀の対局ホストと AI プレイヤーが、対局イベント、行動要求、合法手の選択、局結果を交換するプロトコルです。このリポジトリは仕様書、JSON Schema、識別子 registry、テストベクトルと検査実装を提供します。

| 項目 | 値 |
|---|---|
| Protocol Version | `1.0-draft.1` |
| Profile | `riichi-4p@1.0-draft.1` |
| Release ID | `yamai-1.0-draft.1` |
| 通信形式 | UTF-8 JSON / JSON Lines / WebSocket |
| モード | `play` / `spectate` / `replay` |

現在は **未公開の draft** です（release manifest の `published: false`）。実装は [YAMAI 仕様書](docs/yamai-protocol.md) を基準にし、版、profile revision、profile hash を明示的に交渉します。検査実装の成功は、安定版の公開や独立実装間の相互運用性を意味しません。

## ドキュメント

初めて読む場合は、**仕様案内 → 目的に合う規範の節 → 成果物の仕様 → 検証ガイド** の順に進んでください。

| 文書 | 役割・読むとき |
|---|---|
| [仕様案内](docs/README.md) | 全体像、目的別の参照先、実装を進める順序 |
| [YAMAI 仕様書](docs/yamai-protocol.md) | 通信、状態遷移、麻雀ルール、採点、復旧、適合性の規範本文 |
| [成果物の仕様](docs/artifacts.md) | 原本と生成物、版の一致、profile hash、変更時に確認する関係 |
| [検証ガイド](verification/README.md) | 検査の実行順、適合35項目との対応、検査が保証しない範囲 |
| [形式モデル](verification/quint/README.md) | 6つの Quint モデルの実行方法、有限境界、性質と前提 |

## 通信と対局の原則

ホストが状態・合法候補・期限を提示し、プレイヤーが `request_id` と `action_id` で選択を返します。選択の確定、結果の記録、session ごとの配送、受信者ごとの情報公開を分けて扱います。[通信の流れ](docs/README.md#通信の流れ)と[状態と識別子](docs/README.md#状態と識別子)から全体像を確認できます。

`riichi-4p` は4人リーチ麻雀を対象とします。3人麻雀、AI の内部処理、サービスのアカウント・認証方式・レーティングは対象外です。公開サービスの認可要件は [仕様書第18.6節](docs/yamai-protocol.md#186-サービスの認証認可との境界)を参照してください。

## 成果物

[release-manifest.json](release-manifest.json) が同一 release の対象ファイルを列挙します。

| 成果物 | Protocol | `riichi-4p` |
|---|---|---|
| JSON Schema | [メッセージ](schemas/protocol/1.0-draft.1/message.schema.json) | [ルール](schemas/riichi-4p/1.0-draft.1/riichi-4p-rules.schema.json)、[採点結果](schemas/riichi-4p/1.0-draft.1/scoring-result.schema.json) |
| Registry | [メッセージと識別子](registry/protocol/1.0-draft.1/registry.json) | [役・符・点数](registry/riichi-4p/1.0-draft.1/registry.json) |
| テストベクトル | [索引](test-vectors/protocol/1.0-draft.1/manifest.json)、[正例・負例](test-vectors/protocol/1.0-draft.1/vectors.json) | [採点入力と期待結果](test-vectors/riichi-4p/1.0-draft.1/scoring.json) |

## 検証と閲覧

リポジトリのルートで実行します。まず Python 3 の標準ライブラリで成果物の整合性を確認できます。

```sh
python3 scripts/validate_artifacts.py
```

この1コマンドだけでは回帰テスト・独立した JSON Schema 検査・形式検証を完了しません。[検証ガイドの実行方法](verification/README.md#実行方法)に、追加のコマンドと必要な環境をまとめています。

HTML を生成する場合は、Python 3、Node.js と npm を用意して実行します。

```sh
python3 scripts/render_docs.py
```

生成後は `docs/index.html` を開きます。Git 管理する Markdown が原本で、HTML はローカルの閲覧用です。生成先、任意の MDX、mdxr の固定版は[文書の原本と生成物](docs/artifacts.md#文書の原本と生成物)を参照してください。
