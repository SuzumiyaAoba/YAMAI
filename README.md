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
- [YRC 0003: YAMAI Protocol Version 1 (1.0-draft.6)](docs/yamai-protocol.md)
  - 上記の問題を解決する新しいプロトコルを規定します。
- [YRC 0004: 代表的 MJAI 実装プロファイル](docs/mjai-implementations.md)
  - Gimite、Mortal、mjai.app、Akagi v3、RiichiLab、mjai-reviewerのwire差を比較します。
- [YRC 0005: YAMAI `riichi-4p` 役・符・点数規則 (1.0-draft.4)](docs/riichi-4p-rules.md)
  - 本文§7.6の採点規則を参照する派生資料です。

YRC 0003が、組込みriichi-4p（§7.6）を含む唯一の規範本文です。YRC 0001・0002・0004はInformational、YRC 0005は採点規則の派生資料です。現在はDraftであり、IETF RFCではありません。

仕様の権威関係、Protocol Version と profile hash の責務、互換性判定、変更・承認・公開手順は [仕様策定・リリースプロセス](docs/specification-process.md) に定めます。現在の release ID と、同一 Git tag に束ねる対象は [release manifest](release-manifest.json) で固定し、変更理由と互換性影響は [変更履歴](CHANGELOG.md) に記録します。

## 規範成果物と検証範囲

YRC 0003 `1.0-draft.6` 本文がProtocolとprofile `1.0-draft.4`を定義します。Schema・registry・vector・検査実装は派生成果物として[release manifest](release-manifest.json)で版管理し、本文に照合します。

- [YRC 0003 message Schema root](schemas/yrc-0003/1.0-draft.6/message.schema.json)（参照される全Schemaを含む）
- [YRC 0005 `riichi-4p` rules Schema](schemas/yrc-0005/1.0-draft.4/riichi-4p-rules.schema.json)
- [YRC 0005 scoring vectors Schema](schemas/yrc-0005/1.0-draft.4/scoring-vectors.schema.json)
- [YRC 0003 registry](registry/yrc-0003/1.0-draft.6/registry.json)、[YRC 0005 registry](registry/yrc-0005/1.0-draft.4/registry.json)
- [YRC 0003 vector manifest](test-vectors/yrc-0003/1.0-draft.6/manifest.json)
- [YRC 0003 test vector](test-vectors/yrc-0003/1.0-draft.6/vectors.json)、[YRC 0005 scoring test vector](test-vectors/yrc-0005/1.0-draft.4/scoring.json)

Protocol Version はmessage Schemaとその `$ref` 閉包、およびjoin-proposal Schemaを固定し、`profile_revision` と `profile_hash` は profile成果物を識別します。現在の `profile_hash` は RFC 8785 JCS で profile/rules/scoring-vectors Schema、hashを除くregistry、official/scoring vectorを入力とし、Protocol message Schema、manifestおよび規範本文は入力としません。これらはそれぞれ Protocol Version と release ID／同一 Git tag で固定します。

[`scripts/validate_artifacts.py`](scripts/validate_artifacts.py) は JSON、Schema、registry、hash および公式 vector の整合性を検査しますが、JSON Schemaだけではframe境界、重複JSON key、`seq`、状態遷移、visibility、冪等性、点数保存則およびtimingの全てを表現できません。validatorの成功だけで完全適合を表明せず、YRC 0003 第17節と公式 vector、独立相互運用試験を併せて確認してください。

```sh
rtk python3 scripts/validate_artifacts.py
rtk python3 scripts/test_validator.py
rtk python3 scripts/test_scoring_reference.py
rtk python3 scripts/test_session_contract.py
rtk python3 scripts/test_game_contract.py
rtk python3 scripts/score_oracle.py
```

点数fixtureは [`scripts/scoring_reference.py`](scripts/scoring_reference.py) で、手牌の全分解、役・符・bonus、責任払い、本場・供託と確定点数を入力から再計算します。採点用のイベント投影も直前状態から確認し、正例と意味違反の負例を検証します。合法手・全wire状態・フリテン生成の検証範囲は[仕様完成監査](docs/specification-audit.md)と各vectorの前提に従います。

[`scripts/session_contract.py`](scripts/session_contract.py) は、交渉、token更新、seqの重複・欠落、snapshot、同一transportの再利用、資源の時計とbackpressureを検査します。交渉の入力構造には[選択前のjoin Schema](schemas/yrc-0003/1.0-draft.6/negotiation/join-proposal.schema.json)を使い、版・profileの不一致を構造違反と区別します。

[`scripts/game_contract.py`](scripts/game_contract.py) は完全な判断局面から合法候補を列挙し、受信eventの牌数・牌山・鳴き・槓・リーチ・pao・局精算・次局を検査します。requestの時計と競合は [`scripts/request_contract.py`](scripts/request_contract.py) で検査します。検査範囲と第17節の35項目の対応は[仕様完成監査](docs/specification-audit.md)に記録しています。

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

本版は4人リーチ麻雀のplay・spectate・replay、再接続と状態同期を規定します。3人麻雀は対象外です。安定化には独立実装間の検証が必要です。

YAMAI は特定の麻雀エンジン実装、AIの内部アルゴリズム、対局サービスの認証方式、レーティング方式を規定しません。

認証・認可はYAMAIの範囲外です。`public`、`spectate`、`replay` または完全情報 `view` を公開運用する場合、対象ゲーム／牌譜、座席view、完全情報およびresume tokenのアクセス制御を別の認可profileで定義し、YAMAIのProtocol Versionやprofile hashだけを認証済みの根拠にしてはなりません。

## 名前について

YAMAI は昔ながらの “Yet Another” 系命名であると同時に、既存プロトコルが抱える「病」を直すという意味を込めています。

stateful traceは1つのpeer sessionの時刻付きmessageと不変ledgerを照合します。transactionを途中で分断せず、他seatの選択は全3memberのrequest lifecycleと対応する自分宛てACKで検査します。score_oracleのCLIは現行のscoring_referenceを使用し、fixture IDによる条件選択を行いません。
