# YAMAI 検証ガイド

本書は [YRC 0003](../docs/yamai-protocol.md) `1.0-draft.9` と `riichi-4p` profile `1.0-draft.7` の派生成果物について、検査方法と適合項目との対応を説明する。規範要件は YRC 0003 に従い、検査対象の版とファイルは [release manifest](../release-manifest.json) で確認する。

## 実行方法

コマンドはリポジトリのルートで実行する。次の検査には Python 3 の標準ライブラリだけを使用する。

```sh
python3 scripts/validate_artifacts.py
python3 scripts/test_validator.py
python3 scripts/test_scoring_reference.py
python3 scripts/test_session_contract.py
python3 scripts/test_game_contract.py
python3 tests/test_regressions.py
python3 scripts/score_oracle.py
```

[flake.nix](../flake.nix) と [flake.lock](../flake.lock) は Python、JSON Schema 検査実装、Quint、TLC、Java、Z3 を含む検証環境を固定する。Nix の flakes が有効な環境では、次のコマンドで上記の検査、独立した Draft 2020-12 検査、5つの形式モデルの検査を実行できる。

```sh
nix flake check path:. --no-update-lock-file
```

成果物の検査だけを固定環境で実行する場合は、次を使用する。`aarch64-darwin` は環境に応じて `x86_64-darwin`、`aarch64-linux`、`x86_64-linux` に置き換える。

```sh
nix build path:.#checks.aarch64-darwin.artifact-validator --no-link --print-out-paths
```

出力ディレクトリには `validate.log`、各回帰テストのログ、`jsonschema.log`、`score-oracle.log` が保存される。形式モデルを個別に実行する方法、有限境界、時間的性質と前提は [Quint モデルの説明](quint/README.md) を参照する。

## 検査の役割

| 検査実装 | 対象 |
|---|---|
| [validate_artifacts.py](../scripts/validate_artifacts.py) | JSON、Schema の参照と対応 keyword、registry、版、profile hash、本文 JSON 例、公式ベクトル |
| [check_jsonschema.py](../scripts/check_jsonschema.py) | 独立した Draft 2020-12 実装によるメタ Schema・正例・指定された負例の検査。全参照をローカルで解決する |
| [request_contract.py](../scripts/request_contract.py) | 全3 member の選択、競合解決、ACK、期限、再送、取消し |
| [session_contract.py](../scripts/session_contract.py) | 交渉、token、seq、再送、snapshot、transport の再利用、資源上限と時計 |
| [game_contract.py](../scripts/game_contract.py) | 完全な判断局面の合法候補、受信 event の牌数・牌山・鳴き・槓・リーチ・責任払い・精算・次局 |
| [scoring_reference.py](../scripts/scoring_reference.py) | 手牌の全分解からの役・符・bonus・点数・支払い・供託・確定点数の再計算 |
| [score_oracle.py](../scripts/score_oracle.py) | scoring_reference を使用して採点 fixture を検査する CLI |
| [test_regressions.py](../tests/test_regressions.py) | 初局、sessionごとのseq、再開時の状態保持、観戦snapshot、採点ID非依存性と入力変更 |
| [Quint モデル](quint/README.md) | 有限状態内の要求処理、時計、配送、再接続、snapshot、点数保存 |

stateful trace は1つの peer session の時刻付き message と不変の wire ledger を照合する。transaction 境界、request/ACK の終端、timeout、snapshot 置換、情報公開範囲を検査し、他 seat の選択は全3 member の request lifecycle trace と組み合わせて確認する。

採点検査は fixture の期待値や ID から結果を選ばず、手牌・ルール・event 投影を入力として計算する。`score_oracle.py` と `scoring_reference.py` は同じ採点実装である。

## 適合35項目との対応

番号はYRC 0003 §17と一致する。V番号は現行[公式vectors](../test-vectors/yrc-0003/1.0-draft.9/vectors.json)のID接頭辞であり、各行のvectorは正例と負例を持つ。採点fixtureは[scoring.json](../test-vectors/yrc-0005/1.0-draft.7/scoring.json)にある。本文だけの要件を、Schemaが全て検証したとは扱わない。

| §17 | 主な本文 | 正負vector・実行検査 | 検証する境界 |
|---|---|---|---|
| 1 | §6.3/6.4 | V63–V74、V133–V134/V330 | 構造・版・profile・ルール拒否の順序、新規replayの開始点 |
| 2 | §4.2 | V02/V06、test_validator | UTF-8/CRLFの分割、複数frame、EOF |
| 3 | §5/13 | V105–V108/V113 | 連続prefix、同一byte再送、衝突時の原子的拒否 |
| 4 | §8/9 | V35–V50/V100–V103/V331–V341 | 正常・後着・重複・別IDの再送、採用・不採用・取消しと結果 |
| 5 | §7.3/10 | V176–V196/V235–V246/V253/V258–V259/V272 | 全鳴き、宣言と成立、槍槓時の取消し、槍槓不可でも3seatの反応要求 |
| 6 | §8.3/10.3 | V197–V200/V247–V249 | 複合リーチ、鳴かれた打牌、供託の一回控除 |
| 7 | §7.3 | V192–V196/V256/V258–V260 | 赤牌の物理枚数、consumed multiset、同値牌の自摸切り区別 |
| 8 | §8.4 | V41–V43/V48/V334–V337/V340–V341、採点複数ロンfixture | 明示選択だけを数え、頭ハネ距離・三家和と優先順位を確定 |
| 9 | §7.2 | V201–V210/V227–V234/V245–V246 | 九種九牌、見逃し、途中流局の優先順、通常流局 |
| 10 | §8.5/9.1 | V35–V39/V44/V46/V50 | strict deadline、0期限、既定選択、rejected後の時計 |
| 11 | §11 | V22/V143–V150 | 自分・他家・public・fullでの牌の投影 |
| 12 | §4.1/7.2/15 | V05/V06/V70–V72/V119/V274–V275、N29、test_validator、全候補fixture | byte/深さ/512候補上限、切り詰め禁止、点数ルールの個別・組合せ上限 |
| 13 | §10.2 | V211–V226/V239–V244 | 槓種ごとの公開時点、保留ドラと連続槓 |
| 14 | §7.5、§7.6.3–7.6.8 | V14/V23、採点fixture・N30–N36 | 全分解からの役・符・bonus・役満・支払い、槍槓の物理在庫と共有局面 |
| 15 | §13.1/13.3 | V105–V115/V124 | 欠落で適用しない、有限再送、snapshot floor |
| 16 | §13.1/13.2 | V85–V93/V135–V140 | rotate/期限切れ/一回使用/失敗時非消費 |
| 17 | §7.4/11 | V17/V104/V123/V125 | 終局後の次session、旧action無視、同点順位 |
| 18 | §13.3 | V53–V62/V271、EventStateとsnapshot検査、test_session_contract | 未使用seq、手牌枚数、待ち要求、状態と残量、局間復元後の次局開始 |
| 19 | §9/13 | V19/V55/V60/V62/V109–V110、delivery形式モデル | 固定選択・元の時計、空範囲を含む再配送 |
| 20 | §7.6.7/7.6.8 | V20、採点multiple-ron/pao fixture | 本場配分、責任役満成分、供託の別会計 |
| 21 | §7.2 | V151–V175 | トビ・連荘・アガリ止め・延長の順序 |
| 22 | §6.2/11 | V28/V30/V51/V78–V82/V94–V95/V143–V150 | mode/view/target、観戦のrequest/ACK禁止 |
| 23 | §7.6 | scoring_reference、採点fixture・索引・回帰テスト | 全登録役、全符項目、限界点、支払いの再計算 |
| 24 | §6/14/19 | V63–V93/V127–V142 | revision対応表、hash、capabilityと拡張Schemaのsession分離 |
| 25 | §8.1/8.4 | V31–V50/V52/V60/V254–V256、request形式モデル | 他家3人、個別/共通期限、選択後に一度だけ競合解決 |
| 26 | §8.5/10、§7.6.7.3/7.6.8.2 | V26/V38/V250–V253/V338–V339、pao/penalty採点fixture | 公開meld順・責任seat、chomboによる取消しと結果、0点を含む支払い |
| 27 | §7.2/7.5、§7.6.8 | V27/V57–V58/V151–V175、noten_0–noten_4 | 聴牌人数、供託繰越・配分・終了時残本数 |
| 28 | §10.2/11、§7.6.5 | V211–V226/V124、裏ドラ採点fixture | 嶺上和了前の公開、裏ドラ枚数、元記録cursor |
| 29 | §9/15 | V31–V50/V116–V122/V139/V141、test_game_contract | 猶予、切り捨て前期限、予約済み出力、再送で二重課金しない |
| 30 | §3.1/6.4/12 | V63–V103/V108/V261/V273、Receiver回帰 | Applyの原子性、errorの方向・優先順、同一seqの衝突と不正payloadの競合 |
| 31 | §3.2/5/13 | V105–V115/V261–V262 | wire ledger、再送byte、transactionの非交錯 |
| 32 | §8.1.1/8.4/9 | V35–V50/V263/V267/V331–V341 | 全3選択、strict deadline、ACKと結果の同一transaction・優先順位 |
| 33 | §6.2/13 | V85–V93/V136/V266/V268–V270 | 明示seat、最小空席、resumeでの再指定禁止、replay target |
| 34 | §7.2/10.3 | V151–V175/V241–V249 | 連続槓・リーチの取消し、延長の上限と通し番号 |
| 35 | §11/13.3 | V53–V62/V143–V150/V264–V265 | snapshot・last_eventを含む全viewの非漏洩 |

完全候補の照合では期待集合の一部だけを検査せず、順序とconsumedの並びを除いた集合全体を比較する。現行coreの候補数には余裕がある。自摸番は打牌15以下・リーチ15以下・暗槓3以下・加槓4以下・和了1・九種九牌1の合計39以下、反応はchiの3順子×4赤牌消費パターン×13打牌、ponの3消費パターン×13打牌、daiminkan4消費パターン、hora/none各1の合計201以下という保守的上限があり、512を超えない。実際の牌枚数・喰い替えはこれをさらに制限する。私的拡張は独自の上限・fixtureと第14節の契約を必要とする。

## 検証の範囲

- 標準ライブラリの validator は、この版で使用する JSON Schema assertion の部分集合を実装する。未対応 keyword と nested `$id` は拒否する。Nix の検査では固定した JSON Schema 実装によるメタ Schema と正例の検査も行う。
- game_contract は完全な1判断局面の候補生成、精算済み条件からの次局、受信者が観測できる event 状態を検査する。和了では公開された符・飜・役満から金額を再計算し、本場・供託・責任払いを含む各winの差額を照合する。他家の非公開手牌やホストの牌山順列は復元しない。非公開情報を要する役の判定には、ホストの完全情報と採点 fixture が必要である。
- event 投影の fixture は event payload と前後状態を検査する。envelope、request、ACK の配送と時計は wire trace、request contract、形式モデルで扱い、Receiver で観測可能な部分を接続する。
- 5つの形式モデルは、それぞれの有限境界と環境仮定の下で性質を検査する。牌の全組合せ、任意の拡張・ネットワーク、認証サービス、実装コードとの refinement 証明、モデル間の合成証明は対象外である。

draft.8のV276〜V281は、履歴中snapshotと再送終端、ID全体の文字種、終局後の保留stale通知、交渉済み拡張のsession受信、mode/viewの拒否コード、helloのprofile最小件数を検査する。回帰テストではsnapshotがgapの観測終端の前後にある場合、未要求のseq飛越し、未交渉snapshot、不正IDとchomboの分離も検査する。独立Schema検査は`schema_negative: true`と明記した公式負例も拒否することを確認する。

draft.9のV282〜V288は、途中流局の点差・tenpai・reason、ACKと採用eventの対応、snapshotの固定選択と計時式を検査する。回帰テストではdefaulted打牌、複合リーチ、槓ドラ・リーチ供託・責任払いの挿入、selectionの全固定member、期限ちょうどのuser/default境界、終端requestの復活、途中で切れたACK結果列、拒否時の原子的な状態保持も確認する。

draft.9の未公開追補で追加したV289〜V296は、resolving中の継続、取引途中を原因とするsnapshotの拒否、pao履歴の完全一致、槓宣言を原因とするsnapshot、複合打牌フロー、延期ドラ表示と連続槓の窓を検査する。

仕様全体レビューで追加したV297〜V304は、赤五を含む加槓のsnapshot表現、副露列途中の加槓後の復元・継続、延長戦の場風循環・配牌回数による座標上限、拡張名・resume token・交渉識別子の文字列全体一致を検査する。§17の7・18・21・24・30・35に対応し、V301〜V303の負例は独立したJSON Schema検査でも拒否を確認する。回帰検査では通常五と赤五の鳴き牌／追加牌の入替え、東風・東南両方の場風循環、局内／局間の復元を扱う。

再レビューで追加したV305〜V319は、局内・局間・終局snapshotの点数と供託の保存、重複seqおよびsnapshot置換済み範囲より先に行うdirection/kind検査、唯一の自摸牌の手出し偽装、固定選択とACKのsource一致、policyと期限に応じたdefault/rejected、単独decision・取消し・後着ACKの区別を検査する。§17の3・4・7・10・18・19・26・27・29・30・32に対応する。回帰検査では供託棒の値0/1000/2000、赤牌と通常牌・同一表記牌の有無、不正kindの型、3種類の不正action policy、期限ちょうどと直前、拒否時の非破壊も検査する。

公開情報の再点検で追加したV320〜V329は、鳴かれた河牌と副露の出現回数の一致、門前リーチ・宣言牌の一意性、非公開手牌から公開された槓宣言牌の在庫、和了の金額再計算、複数ロンの裏表示牌列と物理在庫を検査する。§17の7・11・14・18・20・23・24・28・30・35に対応する。回帰検査では同一表記の2枚を使った2回のチー、可視／非公開の加槓、赤五の内訳、全和了採点fixtureの本場・供託・責任払い、拒否時の状態保持を確認する。hashの回帰検査では、wire内のidentityと同じ文字列を持つ注釈・配列・nested memberを正規化せず、identityのescapeされたキーと複数profileのhashだけを置換することも確認する。

ACK結果と採点境界の再点検で追加したV330〜V341は、新規replayの開始点、不採用の反応から自分の鳴き・和了を生成しないこと、三家和に必要な明示horaと合法な反応窓、chomboによる取消しとpenalty、supersededの優先結果を検査する。N30〜N36は、槍槓の4枚と表裏表示牌の重複、山・槓容量の不足、複数ロンのpending_kan・last_tile不一致を拒否する。正当な複数槍槓・複数河底の正例も追加した。§17の1・4・5・8・9・14・20・23・26・30・31・32に対応する。回帰検査では次の正常な自摸、取消しと後着通知の区別、固定選択sourceを維持した取消し、拒否時の原子的な状態保持を確認する。完結したwire captureは、見送り・不採用のACKに対応する結果も欠落させられない。

検査の成功だけでは YRC 0003 第17節の完全適合や、独立実装間の相互運用性を保証しない。実装の適合表明と安定版の公開条件は [仕様本文](../docs/yamai-protocol.md) と [仕様策定・リリースプロセス](../docs/specification-process.md) に従う。
