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
- [YRC 0003: YAMAI Protocol Version 1 (1.0-draft.4)](docs/yamai-protocol.md)
  - 上記の問題を解決する新しいプロトコルを規定します。
- [YRC 0004: 代表的 MJAI 実装プロファイル](docs/mjai-implementations.md)
  - Gimite、Mortal、mjai.app、Akagi v3、RiichiLab、mjai-reviewerのwire差を比較します。
- [YRC 0005: YAMAI `riichi-4p` 役・符・点数規則 (1.0-draft.2)](docs/riichi-4p-rules.md)
  - 標準役、役満、ドラbonus、符、基本点、支払いを規範化します。

YRC 0001、YRC 0002、YRC 0004は既存プロトコルを記述・分析するInformational文書です。YRC 0003と、YRC 0003が各profileの規範参照として指定するStandards Track文書（現在の `riichi-4p` ではYRC 0005 `1.0-draft.2`）がYAMAI実装に対する規範要件を定めます。いずれも現在はDraftであり、IETF RFCではありません。

## 規範成果物

YRC 0003 `1.0-draft.4` と `riichi-4p` profile（YRC 0005 `1.0-draft.2`）の規範成果物は、次のSchema、registryおよびtest vectorです。

- [YRC 0003 message Schema](schemas/yrc-0003/1.0-draft.4/message.schema.json)
- [YRC 0005 `riichi-4p` rules Schema](schemas/yrc-0005/1.0-draft.2/riichi-4p-rules.schema.json)
- [YRC 0003 registry](registry/yrc-0003/1.0-draft.4/registry.json)、[YRC 0005 registry](registry/yrc-0005/1.0-draft.2/registry.json)
- [YRC 0003 test vector](test-vectors/yrc-0003/1.0-draft.4/vectors.json)、[YRC 0005 scoring test vector](test-vectors/yrc-0005/1.0-draft.2/scoring.json)

成果物の整合性は [`scripts/validate_artifacts.py`](scripts/validate_artifacts.py) で確認できます。

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

## 名前について

YAMAI は昔ながらの “Yet Another” 系命名であると同時に、既存プロトコルが抱える「病」を直すという意味を込めています。
