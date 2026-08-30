# YAMAI

**Y**et **A**nother **M**ahjong **AI** Interface

YAMAI は、リーチ麻雀 AI 間で対局イベントと行動を交換するための、明確に版管理されたプロトコルを設計するプロジェクトです。

MJAI は麻雀 AI と牌譜交換のデファクト標準として広く使われていますが、原典仕様、サーバ実装、牌譜形式、標準入出力・WebSocket を使う派生仕様の間に差があります。YAMAI は MJAI のイベント語彙と資産を尊重しつつ、版交渉、要求と応答の対応付け、ルール記述、終局精算、エラー処理を仕様として固定します。

> [!IMPORTANT]
> 現在の YAMAI は設計ドラフトです。実装間の相互運用性が確認されるまで、安定版とは扱いません。

## YAMAI Request for Comments

- [YRC 0001: デファクト MJAI プロトコル記述仕様](docs/mjai-protocol.md)
  - Gimite 由来の4人リーチ麻雀用イベントモデルと、主要な通信方言を整理します。
- [YRC 0002: MJAI プロトコルの設計上の欠陥](docs/mjai-problems.md)
  - 仕様の曖昧さ、実装差、運用上発生した障害と、YAMAI に必要な要件をまとめます。
- [YRC 0003: YAMAI Protocol Version 1 (1.0-draft.5)](docs/yamai-protocol.md)
  - 上記の問題を解決する新しいプロトコルを規定します。
- [YRC 0004: 代表的 MJAI 実装プロファイル](docs/mjai-implementations.md)
  - Gimite、Mortal、mjai.app、Akagi v3、RiichiLab、mjai-reviewerのwire差を比較します。
- [YRC 0005: YAMAI `riichi-4p` 役・符・点数規則 (1.0-draft.3)](docs/riichi-4p-rules.md)
  - 標準役、役満、ドラbonus、符、基本点、支払いを規範化します。

YRC 0001、YRC 0002、YRC 0004は既存プロトコルを記述・分析するInformational文書です。YRC 0003と、YRC 0003が各profileの規範参照として指定するStandards Track文書（現在の `riichi-4p` ではYRC 0005 `1.0-draft.3`）がYAMAI実装に対する規範要件を定めます。いずれも現在はDraftであり、IETF RFCではありません。

仕様の権威関係、Protocol Version と profile hash の責務、互換性判定、変更・承認・公開手順は [仕様策定・リリースプロセス](docs/specification-process.md) に定めます。現在の release ID と、同一 Git tag に束ねる対象は [release manifest](release-manifest.json) で固定し、変更理由と互換性影響は [変更履歴](CHANGELOG.md) に記録します。

## 規範成果物と検証範囲

YRC 0003 `1.0-draft.5` と `riichi-4p` profile（YRC 0005 `1.0-draft.3`）は、文書と機械可読成果物を組み合わせて一つの規範セットを構成します。全対象ファイルは [release manifest](release-manifest.json) に列挙されています。

- [YRC 0003 message Schema root](schemas/yrc-0003/1.0-draft.5/message.schema.json)（参照される全Schemaを含む）
- [YRC 0005 `riichi-4p` rules Schema](schemas/yrc-0005/1.0-draft.3/riichi-4p-rules.schema.json)
- [YRC 0005 scoring vectors Schema](schemas/yrc-0005/1.0-draft.3/scoring-vectors.schema.json)
- [YRC 0003 registry](registry/yrc-0003/1.0-draft.5/registry.json)、[YRC 0005 registry](registry/yrc-0005/1.0-draft.3/registry.json)
- [YRC 0003 vector manifest](test-vectors/yrc-0003/1.0-draft.5/manifest.json)
- [YRC 0003 test vector](test-vectors/yrc-0003/1.0-draft.5/vectors.json)、[YRC 0005 scoring test vector](test-vectors/yrc-0005/1.0-draft.3/scoring.json)

Protocol Version は message Schema とその `$ref` 閉包を固定し、`profile_revision` と `profile_hash` は profile成果物を識別します。現在の `profile_hash` は RFC 8785 JCS で profile/rules/scoring-vectors Schema、hashを除くregistry、official/scoring vectorを入力とし、Protocol message Schema、manifestおよび規範本文は入力としません。これらはそれぞれ Protocol Version と release ID／同一 Git tag で固定します。

[`scripts/validate_artifacts.py`](scripts/validate_artifacts.py) は JSON、Schema、registry、hash および公式 vector の整合性を検査しますが、JSON Schemaだけではframe境界、重複JSON key、`seq`、状態遷移、visibility、冪等性、点数保存則およびtimingの全てを表現できません。validatorの成功だけで完全適合を表明せず、YRC 0003 第17節と公式 vector、独立相互運用試験を併せて確認してください。

```sh
rtk python3 scripts/validate_artifacts.py
```

## 設計原則

1. **要求と応答を明示する** — 行動要求には一意な `request_id` を付けます。
2. **合法手を列挙する** — クライアントはホストが発行した `action_id` を選択します。
3. **状態遷移を決定的にする** — ホストイベントには単調増加する `seq` を付けます。
4. **ルールを暗黙にしない** — 東風・東南、赤牌、複数ロンなどをゲーム開始時に宣言します。
5. **終局を原子的に精算する** — 複数和了を単一の `end_kyoku` にまとめます。
6. **通信方式とイベントモデルを分離する** — JSON Lines と WebSocket の同じ意味論を定義します。
7. **安全に失敗する** — 不正 JSON、未知の必須機能、期限切れ応答、再送を区別します。
8. **MJAI から移行できる** — 牌表記と主要イベント名を可能な限り維持します。

## 対象範囲

最初の適合プロファイルは4人リーチ麻雀です。3人麻雀、牌譜専用モード、再接続・状態同期は拡張可能な形を規定しますが、安定化には実装検証が必要です。

YAMAI は麻雀ルールエンジンそのもの、AI API、対局サービスの認証方式、レーティング方式を規定しません。

認証・認可はYAMAIの範囲外です。`public`、`spectate`、`replay` または完全情報 `view` を公開運用する場合、対象ゲーム／牌譜、座席view、完全情報およびresume tokenのアクセス制御を別の認可profileで定義し、YAMAIのProtocol Versionやprofile hashだけを認証済みの根拠にしてはなりません。

## 名前について

YAMAI は昔ながらの “Yet Another” 系命名であると同時に、既存プロトコルが抱える「病」を直すという意味を込めています。
