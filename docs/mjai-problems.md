# YRC 0002: MJAI プロトコルの設計上の欠陥

| 項目 | 値 |
|---|---|
| 文書系列 | YAMAI Request for Comments (YRC) |
| 文書番号 | YRC 0002 |
| 表題 | Design Defects in the MJAI Protocol |
| 分類 | Informational |
| 状態 | Draft |
| 版 | 1.0-draft.3 |
| 発行日 | 2026-08-30 |
| 更新対象 | なし |
| 廃止対象 | なし |

## Abstract

MJAI はリーチ麻雀 AI と牌譜処理系の相互運用に大きく貢献した。一方、MJAI の仕様は、通信境界、版交渉、要求と応答の対応、ルール記述、終局精算、エラー復旧および資源上限を規範的に定義していない。これらの不足は、互換性のない方言、遅延応答の誤適用、複数和了の不正精算、非公開情報の漏洩および入力起因のサービス不能を生じさせる。

本書は、公開仕様、公開実装および報告済みの運用障害を根拠として、12個のプロトコル設計欠陥を識別する。各欠陥について、観測事実、失敗条件、影響および後継仕様が満たすべき性質を定義し、[YRC 0003] と、同文書がprofileの規範参照として指定するStandards Track文書（現在の `riichi-4p` では [YRC 0005] `1.0-draft.2`）の規範要件への追跡可能性を提供する。

## Status of This Memo

本書は YAMAI Project が管理する Informational 文書であり、IETF Internet Standard ではない。本書は MJAI 実装に新しい適合要件を課さない。

本書中の「必要条件」は、欠陥を解消する後継仕様に必要な性質を表す。`MUST`、`SHOULD` および `MAY` を用いる規範要件は [YRC 0003] と、同文書がprofileの規範参照として指定するStandards Track文書が定義する。

## Table of Contents

1. 目的
2. 要約
3. 欠陥の詳細
4. YAMAI への要求
5. Security Considerations
6. Registry Considerations
7. References
Appendix A. 要求追跡表

## 1. 目的

本書は、[YRC 0001] に記述したデファクト MJAI プロトコルの設計上の問題を、互換性、正しさ、運用および安全性の観点から整理する。特定実装のバグ一覧ではなく、複数実装で同種の欠陥を誘発するプロトコル上の不足を対象とする。

[CRYOLITE-MJAI] も、原典仕様が全ケースを網羅せず、各プロジェクトの独自拡張が牌譜変換と相互運用を阻害していると指摘する。

### 1.1 欠陥の認定基準

本書は、次のいずれかを満たすものをプロトコル設計欠陥として認定する。

1. 2つ以上の適合を意図した実装が、同じ入力に対して互換性のない挙動を示す。
2. 正常な transport の分割、結合、遅延または再送だけで状態が不正になる。
3. 正しい挙動を決定するために必要なルールまたは状態が wire 上に存在しない。
4. 不正入力を安全に拒否するための境界またはエラー意味論が存在しない。
5. 実運用で障害が報告され、その原因が実装固有ではなく要求・応答モデルにある。

単なる機能不足、実装のプログラミングミスまたは特定 AI の判断誤りは、上記条件を満たさない限り本書の対象外である。

### 1.2 記述形式

各欠陥は安定した識別子 `P-nn` を持つ。「観測」は根拠資料から確認できる事実、「失敗条件」は欠陥が表面化する最小条件、「影響」は外部から観測可能な結果、「必要条件」は後継仕様に要求される性質を表す。

## 2. 要約

| ID | 問題 | 主な影響 | YAMAI の解決策 |
|---|---|---|---|
| P-01 | 正規仕様と版管理がない | 同じ「MJAI対応」でも接続不能 | 意味的バージョンと交渉 |
| P-02 | line-by-line と batch が未区別 | デッドロック、ストリームずれ | transport と message semantics を分離 |
| P-03 | 要求と応答を対応付けられない | 遅延応答が次の手として誤採用 | `request_id` と `ack` |
| P-04 | 全イベントへ応答する | 不要な同期、timeout競合 | `request` 受信時だけ応答 |
| P-05 | 合法手通知が非標準 | クライアントごとに合法性が不一致 | `action_id` 付き合法手列挙 |
| P-06 | スキーマと意味検証が不完全 | crash、状態破壊、方言差 | 規範 Schema と fail-closed 検証 |
| P-07 | ルール情報が不足 | 同じイベント列の意味が変わる | 必須 `rules` object |
| P-08 | 対局表示と牌譜表示が混在 | 情報漏洩、`?` 処理バグ | mode と visibility を明示 |
| P-09 | 終局精算が非原子的 | 複数ロンの点数・親継続が曖昧 | 単一 `end_kyoku` で確定 |
| P-10 | エラー・再送・切断規則がない | hang、二重行動、復旧不能 | エラー分類、冪等性、`seq` |
| P-11 | 3人麻雀が未標準 | `kita`、座席、配列長が非互換 | 明示的 profile |
| P-12 | 資源上限がない | メモリ・待機時間 DoS | フレーム・期限・キュー上限 |

## 3. 欠陥の詳細

### P-01: 正規仕様、版番号、互換交渉がない

原典ページの例は `protocol_version: 1` であるが、Gimite のサーバ実装は `3` を送信する [YRC 0001]。番号ごとの機能差、後方互換性および非対応版の拒否方法は規定されていない。

標準化案にも不一致がある。例えば、`start_game.id` は本文では `0` から `4`、JSON Schema では最大 `3` である。

結果として、クライアントはバージョンを無視するか、特定サーバの挙動を暗黙に仮定する。実装を更新しただけで、同じ設定の対局が変化する可能性がある。

**必要条件:** ホストが対応版を提示し、クライアントが1版を選び、共通版がなければゲーム開始前に終了すること。

### P-02: line-by-line と batch の意味論が定義されていない

原典は1イベントを送信するたびに、4プレイヤーから1応答を待つ。Mortal本体は1行1 event objectを読むstream interfaceである。一方、mjai.appおよびAkagi v3は、複数イベントをJSON arrayとして送信し、行動可能地点で1回だけ応答するbatch interfaceを使用する [MORTAL-ENGINE] [MJAI-APP] [AKAGI-BOT]。詳細は [YRC 0004] に記述する。

[CRYOLITE-MJAI] においても、この区別は TODO である。

受信側が1行1 object を期待する場合、array を解釈できない。逆に、ホストがイベントごとの `none` を待つ一方で、クライアントが batch 末尾にだけ応答する場合、両者はデッドロックする。

**必要条件:** 1 transport frame が何メッセージか、どのメッセージに何応答必要かを独立して規定すること。

### P-03: 要求と応答を対応付けられない

原典の応答には要求識別子がない。timeout 後に到着した合法な打牌が、次の鳴き受付への応答として読み取られる可能性がある。

この問題は実運用でも発生し、RiichiLab は2026年6月に `request_id`、`action_ack` および time bank を導入した [RIICHI-PROTOCOL-V2]。

ただし、後方互換のため `request_id` なしも許容されており、MJAI 全体の解決には至っていない。

**必要条件:** すべての行動要求と応答を一意なIDで結び、古い・重複・未来の応答を区別すること。

### P-04: 行動不能なイベントにも応答を要求する

line-by-line 方式では、`start_kyoku`、他家の `tsumo`、`dora` および `hora` などにも `none` を返す。これにより、次の問題が生じる。

- 往復回数が多い
- クライアントの処理停止が、意思決定を必要としない箇所でも対局全体を止める
- 「通知」と「要求」の型が同じ
- イベント先読みや並列配信と相性が悪い

**必要条件:** 通知イベントと行動要求を異なる message kind にし、要求された場合だけ応答すること。

### P-05: `possible_actions` が非標準である

原典文書のイベント例に `possible_actions` は存在しない。Gimite v3 は自分の `tsumo`、他家の `dahai` および `kakan` に付加するが [YRC 0001]、Schema、候補の完全性、順序、未知行動ならびに候補と実際の合法性の関係は規定されていない。

クライアント自身の合法手計算とホストのルールが異なる場合、クライアントが合法と判断した行動が拒否される。候補がない古い方言を「行動不能」と誤解する実装も生じる。

**必要条件:** ホストを合法性の権威とし、要求ごとに選択可能な具体的行動を不透明な `action_id` 付きで列挙すること。

### P-06: 構文 Schema と意味検証が不完全である

既存 Schema はすべてのイベントを網羅しておらず、存在する Schema も次を十分に検証できない。

- `actor` と `target` の関係
- `consumed` が実際に手牌に存在するか
- `pai` と副露牌が同じ牌種か
- `tsumogiri` と直前ツモの一致
- リーチ後の打牌制約
- イベント順序
- 点数の `scores[n] = previous[n] + deltas[n]`

JSON の欠落値または型不正をゼロもしくは空文字として読む実装は、異常を拒否せず状態を破壊する。

**必要条件:** JSON Schema に加え、状態機械による意味検証を適合要件にすること。不正入力は状態へ適用する前に拒否すること。

### P-07: ルールがイベント列から決定できない

`start_game` だけでは、少なくとも次の規則を決定できない。

- 東風、東南、その他のゲーム長
- 赤五の枚数
- 喰いタン
- 開始点・返し点
- トビ終了
- 頭ハネ、ダブルロン、三家和
- 四風連打、四家立直、四槓散了など途中流局
- 親継続、聴牌連荘、アガリ止め
- パオやローカル役

`aka_flag`、`num_players` などは独自拡張である。同じイベントを生成できても、合法手と精算が一致する保証はない。

**必要条件:** ルールプロファイルと必須ルール値をゲーム開始前に確定し、理解できない必須ルールをクライアントが拒否できること。

### P-08: in-game と replay の構造が混在する

対局中は他家の手牌およびツモを `?` にするが、牌譜では全情報を公開できる。両者は同じイベント名とほぼ同じ構造を使用する。

このため、牌譜をそのままプレイヤーへ送信する情報漏洩、`?` を実牌として処理する範囲外アクセス、および他家手牌枚数を正しく追跡できない問題が生じる。

**必要条件:** mode と受信者の view を明示し、非公開情報の表現を Schema で分けること。

### P-09: 終局結果と複数和了が非原子的である

Gimite 実装のダブルロンは複数の `hora` を逐次配信し、後の `scores` は前の和了を含む累積点である [GIMITE-CODE]。しかし、「各 `hora.scores` が差分か累積か」、「供託・本場を誰へ付けるか」および「和了者に親が含まれる場合に連荘するか」は、独立仕様として明記されていない。

`hora` と `ryukyoku` の結果フィールドにも次の差がある。

- 裏ドラ表示牌欄の名称（YAMAIでは `ura_dora_markers` へ正規化）
- `yakus` の名前と形式
- `hora_points`、`fan`、`fu` の有無
- `tehais`、`tenpais` の公開範囲
- `ryukyoku.reason` の値

**必要条件:** 全和了、流局理由、点数差分、確定点、次局情報を1個の原子的な `end_kyoku` に格納すること。

### P-10: エラー、再送、切断、再接続が規定されていない

原典は、不正 JSON、違法行動、timeout および EOF の扱いを規定しない。即時切断、エラーイベント、チョンボおよび既定行動などの処理はサーバごとに異なる。

通信切断後に同じ action を再送した場合に二重適用されるか、古いイベントを再配信できるか、およびクライアントが適用済みの位置を確認する方法は規定されていない。

**必要条件:** エラーコード、fatal/recoverable、既定行動、action の冪等性、イベント `seq`、再同期手順を定義すること。

### P-11: 3人麻雀が共通仕様にない

原典は4人固定である。派生実装は `num_players` または `kita` を追加するが、座席番号、欠番、`tehais` の長さ、チー禁止、萬子構成、北抜きおよび点数計算は統一されていない。

**必要条件:** `riichi-4p` と `riichi-3p` を別 profile とし、profile が異なる接続を暗黙変換しないこと。

### P-12: 資源上限と安全要件がない

最大フレーム長、JSON の深さ、1ゲームのイベント数、応答期限、接続時間および未処理要求数は規定されていない。改行を送らない巨大入力または応答を読み取らない peer により、メモリおよび待機時間を枯渇させることが可能である。

**必要条件:** フレーム上限、JSON上限、timeout、同時要求数を hello で交換し、最低保証値を仕様で定めること。

## 4. YAMAI への要求

YAMAI は少なくとも次を満たす必要がある。

| 要求ID | 要求 |
|---|---|
| Y-01 | 安定した major/minor 版と決定的な版交渉 |
| Y-02 | transport に依存しない message boundary |
| Y-03 | host message の単調増加 `seq` |
| Y-04 | 一意な `request_id` と冪等な action |
| Y-05 | `action_id` 付き完全な合法手集合 |
| Y-06 | 通知と要求の分離 |
| Y-07 | ルール profile と必須 rule object |
| Y-08 | atomic な `end_kyoku` |
| Y-09 | play/replay と visibility の分離 |
| Y-10 | 構文・意味 Schema、未知拡張の規則 |
| Y-11 | timeout、default、ack、error の規定 |
| Y-12 | フレーム・JSON・キューの資源上限 |
| Y-13 | MJAI との損失箇所を明示した変換規則 |

これらを具体化した規範仕様は、[YRC 0003] と、同文書がprofileの規範参照として指定するStandards Track文書（現在の `riichi-4p` では [YRC 0005]）からなる。

Y-10およびY-13の適合性は、[YRC 0003] が要求する同一release tagの規範Schema、registryおよび公式test vector（profileのYRC 0005 scoring vectorを含む）まで追跡可能でなければならない。

→ [YAMAI Protocol 1.0 Draft 4](yamai-protocol.md)

## 5. Security Considerations

P-03、P-06、P-08、P-10 および P-12 は、直接的な security impact を持つ。

- P-03 は、古い正当な action を異なる request へ適用し、対局結果を改ざんする。
- P-06 は、型不正または範囲外値を通じて crash、memory corruption または不正状態を誘発し得る。
- P-08 は、replay の完全情報を play view へ混入させ、秘密の手牌を漏洩させる。
- P-10 は、再送による action の二重適用または復旧不能を生じさせる。
- P-12 は、受信メモリ、CPU、未解決 request および接続時間を枯渇させる。

MJAI を loopback 以外へ公開することは、transport authentication と confidentiality が別途提供されない限り推奨されない。TLS を追加しても、上記の application-layer defect は解消されない。

## 6. Registry Considerations

本書は registry を新設せず、新しい protocol value を割り当てない。P-01、P-07 および P-11 を解消するため、[YRC 0003] は version、profile、capability、rule、event、action、error および result reason の registry を定義する。

## 7. References

### 7.1 Informative References

- [YRC 0001] YAMAI Project, “デファクト MJAI プロトコル記述仕様”.
- [YRC 0003] YAMAI Project, “YAMAI Protocol Version 1 (1.0-draft.4)”.
- [GIMITE-MJAI] Gimite, “Mjai 麻雀AI対戦サーバ”, 2017-06-07.  
  https://gimite.net/pukiwiki/index.php?Mjai+%E9%BA%BB%E9%9B%80AI%E5%AF%BE%E6%88%A6%E3%82%B5%E3%83%BC%E3%83%90=
- [GIMITE-CODE] Gimite, “mjai”, source repository.  
  https://github.com/gimite/mjai
- [CRYOLITE-MJAI] Cryolite, “Standardization Project for mjai Format Specification”.  
  https://github.com/Cryolite/mjai
- [MORTAL-EVENT] Equim-chan, “Mortal MJAI Event”.  
  https://github.com/Equim-chan/Mortal/blob/main/libriichi/src/mjai/event.rs
- [MORTAL-ENGINE] Equim-chan, Mortal `mortal.py`.  
  https://github.com/Equim-chan/Mortal/blob/main/mortal/mortal.py
- [MJAI-APP] smly, “mjai.app”.  
  https://github.com/smly/mjai.app
- [AKAGI-BOT] Shinkuan, “Writing an mjai bot for Akagi”.  
  https://github.com/shinkuan/Akagi/blob/v3/mjai_bot/README.md
- [RIICHI-PROTOCOL-V2] smly, “Protocol v2: request_id, action_ack, and time bank are now live”, 2026-06-10.  
  https://github.com/smly/RiichiEnv/discussions/216
- [RFC 8259] Bray, T., Ed., “The JavaScript Object Notation (JSON) Data Interchange Format”, STD 90, RFC 8259, December 2017.  
  https://www.rfc-editor.org/rfc/rfc8259
- [YRC 0004] YAMAI Project, “代表的 MJAI 実装プロファイル”.
- [YRC 0005] YAMAI Project, “YAMAI `riichi-4p` 役・符・点数規則 (1.0-draft.2)”.

## Appendix A. 要求追跡表

| 欠陥 | 導出要求 | Standards Track文書の主節（YRC 0003 / profile規範参照） |
|---|---|---|
| P-01 | Y-01 | 6. 版・機能交渉、17. 適合性 |
| P-02 | Y-02, Y-06 | 3. プロトコルモデル、4. JSON と transport |
| P-03 | Y-03, Y-04, Y-11 | 5. 共通 envelope、8. 行動要求、9. ACK と timeout |
| P-04 | Y-06 | 3. プロトコルモデル |
| P-05 | Y-05 | 8. 行動要求 |
| P-06 | Y-10 | 4. JSON、7. profile、17. 適合性 |
| P-07 | Y-07 | 6.3 `welcome`、7.2 必須ルール、[YRC 0005] |
| P-08 | Y-09 | 11. visibility と mode |
| P-09 | Y-08 | 7.5 `end_kyoku` |
| P-10 | Y-03, Y-04, Y-11 | 9. ACK、12. エラー、13. 再接続 |
| P-11 | Y-07, Y-10 | 6. profile 交渉、19. Registry Considerations |
| P-12 | Y-12 | 4. JSON、15. 資源・安全要件 |
| P-02, P-05, P-07, P-08, P-09, P-10, P-11 | Y-13 | 16. MJAI からの移行、17. 適合性（同一release tagのSchema、registry、公式test vector） |
