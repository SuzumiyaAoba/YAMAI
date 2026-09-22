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

実装は [YAMAI 仕様書](docs/yamai-protocol.md) を基準にしてください。draft 1 は実装と相互運用試験のための仕様であり、版、profile revision、profile hash を明示的に交渉します。

## ドキュメント

| 文書 | 内容 |
|---|---|
| [仕様案内](docs/README.md) | 対象範囲、通信の流れ、実装目的別の参照先 |
| [YAMAI 仕様書](docs/yamai-protocol.md) | 通信、状態遷移、麻雀ルール、採点、復旧、適合性の規範本文 |
| [成果物の仕様](docs/artifacts.md) | ファイル構成、版の一致、profile hash、検査層の関係 |
| [検証ガイド](verification/README.md) | 実行方法、適合35項目と検査の対応、検証範囲 |
| [形式モデル](verification/quint/README.md) | 5つの Quint モデル、有限境界、性質と前提 |
| [仕様レビュー](docs/spec-review.mdx) | 全体点検の発見事項、修正と再検査結果 |
| [認証・認可の実装計画](docs/auth-implementation-plan.mdx) | サービス層の認証・認可、resumeとの接続、実装順と受入条件 |

## 通信と対局の原則

- ホストは `hello` で版と機能を提示し、`join` を検査して `welcome` を返します。
- ホストは `event` で状態を確定し、`request` で合法候補と期限を通知します。
- プレイヤーは `request_id` と `action_id` を指定して選択を返します。
- ホストは競合する選択を確定し、`ack` と結果イベントを原子的に記録します。
- ホストメッセージは session ごとの `seq` で順序付け、再送で二重適用しません。
- 各受信者には許可された view を投影し、再接続でも選択と時計を保持します。

`riichi-4p` はルール宣言、牌山、合法手、フリテン、役・符・点数、複数ロン、供託・本場・責任払い、流局、次局と終局を定義します。3人麻雀、AI の内部処理、サービスのアカウント・認証方式・レーティングは対象外です。公開サービスの認可要件は [仕様書第18.6節](docs/yamai-protocol.md#186-サービスの認証認可との境界) を参照してください。

## 成果物

[release-manifest.json](release-manifest.json) が同一 release の対象ファイルを列挙します。

| 成果物 | Protocol | `riichi-4p` |
|---|---|---|
| JSON Schema | [メッセージ](schemas/protocol/1.0-draft.1/message.schema.json) | [ルール](schemas/riichi-4p/1.0-draft.1/riichi-4p-rules.schema.json)、[採点結果](schemas/riichi-4p/1.0-draft.1/scoring-result.schema.json) |
| Registry | [メッセージと識別子](registry/protocol/1.0-draft.1/registry.json) | [役・符・点数](registry/riichi-4p/1.0-draft.1/registry.json) |
| テストベクトル | [索引](test-vectors/protocol/1.0-draft.1/manifest.json)、[正例・負例](test-vectors/protocol/1.0-draft.1/vectors.json) | [採点入力と期待結果](test-vectors/riichi-4p/1.0-draft.1/scoring.json) |

## 検証と閲覧

Python 3 の標準ライブラリで成果物の整合性を検査できます。

```sh
python3 scripts/validate_artifacts.py
```

HTML の生成には Python 3、Node.js と npm を使用します。mdxr `0.2.0` を使って仕様書と検証ガイドを生成します。

```sh
python3 scripts/render_docs.py
```

生成後は `docs/index.html` から各文書を開けます。Markdown・MDXが原本であり、HTMLは同じ内容から生成する閲覧用成果物です。回帰テスト、独立したJSON Schema検査、形式検証の手順は[検証ガイド](verification/README.md)にあります。
