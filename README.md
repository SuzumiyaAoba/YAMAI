# YAMAI

**Y**et **A**nother **M**ahjong **AI** Interface

YAMAI は、4人リーチ麻雀の対局ホストと AI プレイヤーが、対局イベント・行動要求・局結果を交換するためのプロトコル仕様提案です。MJAI の牌表記と主要イベント名を引き継ぎ、版交渉、要求と応答の対応、ルール、終局精算、再接続、エラー処理を定義します。

現在の提案は **Protocol `1.0-draft.6` / `riichi-4p` profile `1.0-draft.4`** です。実装と相互運用試験を目的とする Draft であり、安定版の公開には二つ以上の独立実装による検証が必要です。

## 仕様と背景資料

実装・レビューは [YRC 0003: YAMAI Protocol Version 1](docs/yamai-protocol.md) から始めてください。同文書が、通信と状態遷移、組込み `riichi-4p` の採点規則（第7.6節）、適合要件（第17節）を含む唯一の規範本文です。

| 文書 | 内容 | 位置付け |
|---|---|---|
| [YRC 0003: YAMAI Protocol Version 1](docs/yamai-protocol.md) | プロトコルと4人リーチ麻雀の規則 | Standards Track Draft |
| [YRC 0001: デファクト MJAI プロトコル記述仕様](docs/mjai-protocol.md) | 既存のイベントモデルと通信方言 | Informational |
| [YRC 0002: MJAI プロトコルの設計上の欠陥](docs/mjai-problems.md) | 曖昧さ・実装差・運用障害と設計要求の対応 | Informational |
| [YRC 0004: 代表的 MJAI 実装プロファイル](docs/mjai-implementations.md) | 主要実装の通信形式と相互運用上の差 | Informational |

YRC（YAMAI Request for Comments）は本プロジェクトの文書系列であり、IETF RFC ではありません。成果物の権威関係、版管理、変更提案と公開の手順は [仕様策定・リリースプロセス](docs/specification-process.md)、版ごとの変更と互換性は [変更履歴](CHANGELOG.md) を参照してください。

## 設計原則

1. **要求と応答を明示する** — 行動要求には一意な `request_id` を付けます。
2. **合法手を列挙する** — プレイヤーはホストが発行した `action_id` を選択します。
3. **状態遷移を決定的にする** — ホストメッセージには単調増加する `seq` を付けます。
4. **ルールを暗黙にしない** — 東風・東南、赤牌、複数ロンなどをゲーム開始時に宣言します。
5. **終局を原子的に精算する** — 複数和了を単一の `end_kyoku` にまとめます。
6. **通信方式とイベントモデルを分離する** — JSON Lines と WebSocket に同じ意味論を定義します。
7. **安全に失敗する** — 不正 JSON、未知の必須機能、期限切れ応答、再送を区別します。
8. **MJAI から移行できる** — 牌表記と主要イベント名を可能な限り維持します。

## 機械可読成果物

Schema、registry、公式テストベクトル、検査実装は規範本文に照合する派生成果物です。[release manifest](release-manifest.json) が同じ版として取得するファイルを列挙します。

| 成果物 | プロトコル | `riichi-4p` のルール・採点 |
|---|---|---|
| JSON Schema | [メッセージと参照先](schemas/yrc-0003/1.0-draft.6/message.schema.json) | [ルール](schemas/yrc-0005/1.0-draft.4/riichi-4p-rules.schema.json)、[採点結果](schemas/yrc-0005/1.0-draft.4/scoring-result.schema.json) |
| Registry | [識別子と許可値](registry/yrc-0003/1.0-draft.6/registry.json) | [役・符・点数](registry/yrc-0005/1.0-draft.4/registry.json) |
| テストベクトル | [索引](test-vectors/yrc-0003/1.0-draft.6/manifest.json)、[正例と負例](test-vectors/yrc-0003/1.0-draft.6/vectors.json) | [採点入力と期待結果](test-vectors/yrc-0005/1.0-draft.4/scoring.json) |

Protocol Version はメッセージ Schema とその参照先を固定します。`profile_revision` と `profile_hash` は profile 成果物を識別し、hash の対象と正規化方法は release manifest に記載します。採点成果物のパスと Schema ID には `yrc-0005` 名前空間を使用します。

## 検証

リポジトリのルートで、Python 3 の標準ライブラリだけを使って成果物の整合性を検査できます。

```sh
python3 scripts/validate_artifacts.py
```

回帰テスト、独立した JSON Schema 検査、Quint/TLC による形式検証の実行方法と、第17節の適合項目との対応は [検証ガイド](verification/README.md) にまとめています。検査は JSON・Schema・hash、状態遷移、合法候補、採点、有限モデル上の性質を扱い、実装間の相互運用試験は別途必要です。

## 対象範囲

本提案は4人リーチ麻雀の `play`・`spectate`・`replay`、再接続と状態同期を対象とします。3人麻雀、特定の麻雀エンジン、AI の内部アルゴリズム、対局サービスの認証方式、レーティング方式は対象外です。

公開サービスでのゲーム・牌譜・座席 view・完全情報・resume token へのアクセス制御は、別の認可 profile で定義します。詳細は仕様本文第18節と [仕様策定・リリースプロセス](docs/specification-process.md) 第6節を参照してください。

## 名前について

YAMAI は昔ながらの “Yet Another” 系命名であると同時に、既存プロトコルが抱える「病」を直すという意味を込めています。
