# YAMAI 検証ガイド

本書は YAMAI draft 1 の検査方法、適合35項目との対応、各検査の範囲を定義する。Protocol Version と `riichi-4p` revision は `1.0-draft.1`。規範要件は [仕様書](../docs/yamai-protocol.md)、対象ファイルは [release manifest](../release-manifest.json) に従う。

## 実行方法

コマンドはリポジトリのルートで実行する。次の検査には Python 3 の標準ライブラリだけを使用する。

```sh
python3 scripts/validate_artifacts.py
python3 scripts/test_validator.py
python3 scripts/test_scoring_reference.py
python3 scripts/test_session_contract.py
python3 scripts/test_game_contract.py
python3 scripts/test_tooling.py
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
| [test_tooling.py](../scripts/test_tooling.py) | 採点CLIの外部ファイル入力・JSON出力・ID重複・エラー通知、文書生成でのコード例とリンクの保持 |
| [Quint モデル](quint/README.md) | 有限状態内の要求処理、時計、配送、再接続、snapshot、点数保存 |

stateful trace は1つの peer session の時刻付き message と不変の wire ledger を照合する。transaction 境界、request/ACK の終端、timeout、snapshot 置換、情報公開範囲を検査し、他 seat の選択は全3 member の request lifecycle trace と組み合わせて確認する。

採点検査は fixture の期待値や ID から結果を選ばず、手牌・ルール・event 投影を入力として計算する。`score_oracle.py` と `scoring_reference.py` は同じ採点実装である。

## 適合35項目との対応

番号はYAMAI 仕様書 §17と一致する。V番号は現行[公式vectors](../test-vectors/protocol/1.0-draft.1/vectors.json)のID接頭辞であり、各行のvectorは正例と負例を持つ。採点fixtureは[scoring.json](../test-vectors/riichi-4p/1.0-draft.1/scoring.json)にある。本文だけの要件を、Schemaが全て検証したとは扱わない。

| §17 | 主な本文 | 正負vector・実行検査 | 検証する境界 |
|---|---|---|---|
| 1 | §6.3/6.4 | V63–V74、V133–V134/V330 | 構造・版・profile・ルール拒否の順序、新規replayの開始点 |
| 2 | §4.2/4.3/12 | V02/V06/V417、test_validator | UTF-8/CRLFの分割、複数frame、EOF、空のWebSocket text messageはinvalid_json |
| 3 | §5/13 | V105–V108/V113 | 連続prefix、同一byte再送、衝突時の原子的拒否 |
| 4 | §8/9 | V35–V50/V100–V103/V331–V341/V414 | 正常・後着・重複・別IDの再送、採用・不採用・取消しと結果、後着attemptごとのstale通知は一度だけ |
| 5 | §7.3/10 | V176–V196/V235–V246/V253/V258–V259/V272 | 全鳴き、宣言と成立、槍槓時の取消し、槍槓不可でも3seatの反応要求 |
| 6 | §8.3/10.3 | V197–V200/V247–V249 | 複合リーチ、鳴かれた打牌、供託の一回控除 |
| 7 | §7.3 | V192–V196/V256/V258–V260 | 赤牌の物理枚数、consumed multiset、同値牌の自摸切り区別 |
| 8 | §8.4 | V41–V43/V48/V334–V337/V340–V341、採点複数ロンfixture | 明示選択だけを数え、頭ハネ距離・三家和と優先順位を確定 |
| 9 | §7.2 | V201–V210/V227–V234/V245–V246 | 九種九牌、見逃し、途中流局の優先順、通常流局 |
| 10 | §8.5/9.1 | V35–V39/V44/V46/V50 | strict deadline、0期限、既定選択、rejected後の時計 |
| 11 | §11 | V22/V143–V150 | 自分・他家・public・fullでの牌の投影 |
| 12 | §4.1/7.2/15 | V05/V06/V70–V72/V119/V274–V275、N29、test_validator、全候補fixture | byte/深さ/512候補上限、切り詰め禁止、点数ルールの個別・組合せ上限 |
| 13 | §10.2 | V211–V226/V239–V244 | 槓種ごとの公開時点、保留ドラと連続槓 |
| 14 | §7.5、§7.6.3–7.6.8 | V14/V23/V343–V350/V359–V360/V374–V383/V389–V391/V394–V408、採点fixture・N30–N36 | 全分解からの役・符・bonus・役満・支払い、槍槓の物理在庫、役IDの重複・排他・必要面子数と牌種条件・有効ルール・公開履歴、複合役満の両立、和了牌・可視手牌・公開副露との牌種照合、公開字牌面子で確定する役の欠落禁止と小三元・小四喜の雀頭、20符・25符の双方向条件と公開面子からの符下限、槓子数と三槓子・四槓子の一致、大四喜・四暗刻ロンの倍化 |
| 15 | §13.2/13.3 | V105–V115/V124 | 欠落で適用しない、有限再送、snapshot floor |
| 16 | §13.1/13.2/13.3 | V85–V93/V135–V140 | rotate/期限切れ/一回使用/失敗時非消費 |
| 17 | §7.4/11 | V17/V104/V123/V125 | 終局後の次session、旧action無視、同点順位 |
| 18 | §13.3 | V53–V62/V271/V351–V358/V367–V370/V384–V388/V393、EventStateとsnapshot検査、test_session_contract | 未使用seq、手牌枚数、待ち要求、状態と残量、局間復元後の次局開始、連続prefixと同じ原因eventの欠落回復での確定状態・IDの保持、未受信requestの復元、game単位のbank非増加、元記録位置・終局結果の保持、原因eventの巻戻し・別payload・非event参照の拒否 |
| 19 | §9/13 | V19/V55/V60/V62/V109–V110/V361–V366/V371–V373、delivery形式モデル | 固定選択・元の時計、空範囲を含む再配送、snapshotの残期間と後続selection/ACKの経過時間が逆行しないこと、rejected後も観測した経過時間を保持、個別・group時計が同じ固定時点を表しselectionがその時点以前であること |
| 20 | §7.6.7/7.6.8 | V20、採点multiple-ron/pao fixture | 本場配分、責任役満成分、供託の別会計 |
| 21 | §7.2 | V151–V175 | トビ・連荘・アガリ止め・延長の順序 |
| 22 | §6.2/11 | V28/V30/V51/V78–V82/V94–V95/V143–V150/V412–V413/V415–V416 | mode/view/target、観戦のrequest/ACK禁止、初期snapshotとwelcomeの点数一致・seq=1 |
| 23 | §7.6 | scoring_reference、採点fixture・索引・回帰テスト、N37–N41 | 全登録役、全符項目、限界点、支払いの再計算。投影省略時も第一巡と全seatの槓数、嶺上と一発、通常流局の全手牌と槓数の整合を検査 |
| 24 | §6/14/19 | V63–V93/V127–V142 | revision対応表、hash、capabilityと拡張Schemaのsession分離 |
| 25 | §8.1/8.4 | V31–V50/V52/V60/V254–V256、request形式モデル | 他家3人、個別/共通期限、選択後に一度だけ競合解決 |
| 26 | §8.5/10、§7.6.7.3/7.6.8.2 | V26/V38/V250–V253/V338–V339、pao/penalty採点fixture | 公開meld順・責任seat、chomboによる取消しと結果、0点を含む支払い |
| 27 | §7.2/7.5、§7.6.8 | V27/V57–V58/V151–V175、noten_0–noten_4 | 聴牌人数、供託繰越・配分・終了時残本数 |
| 28 | §10.2/11、§7.6.5 | V211–V226/V124、裏ドラ採点fixture | 嶺上和了前の公開、裏ドラ枚数、元記録cursor |
| 29 | §9/15 | V31–V50/V116–V122/V139/V141、test_game_contract | 猶予、切り捨て前期限、予約済み出力、再送で二重課金しない |
| 30 | §3.3/6.3/6.4/8.1/12 | V63–V103/V108/V261/V273/V342/V392/V411–V413、Receiver回帰 | Applyの原子性、初期化前の非fatal診断拒否、errorの方向・優先順、途中観戦の必須capabilityとlimitの競合、同一seqの衝突と不正payloadの競合、mode違反を欠番より先に拒否、同内容の過去eventへの原因参照 |
| 31 | §3.2/5/13 | V105–V115/V261–V262 | wire ledger、再送byte、transactionの非交錯 |
| 32 | §8.1.1/8.4/9 | V35–V50/V263/V267/V331–V341 | 全3選択、strict deadline、ACKと結果の同一transaction・優先順位 |
| 33 | §6.2/13 | V85–V93/V136/V266/V268–V270 | 明示seat、最小空席、resumeでの再指定禁止、replay target |
| 34 | §7.2/10.3 | V151–V175/V241–V249 | 連続槓・リーチの取消し、延長の上限と通し番号 |
| 35 | §11/13.3 | V53–V62/V143–V150/V264–V265 | snapshot・last_eventを含む全viewの非漏洩 |

V409–V410は項目18のsnapshot復元を補完する。嶺上手番と、その嶺上牌から連続槓を宣言した反応待ちの両方で、他seatの一発資格を復活させる負例を拒否する。正常な一発なしの復元と、拒否時の状態・ledger・適用seqの原子性も回帰検査する。N37–N41の対照として、第一自摸、一発なしのリーチ嶺上和了、暗槓を含む通常流局の正例を使用する。未成立の槓宣言への槍槓では一発・第一巡資格を誤って失効させないことも確認する。

V411–V417は、初期化・mode別のエラー優先順・後着通知・観戦初期snapshot・WebSocketの空payloadを補完する。Receiver回帰ではplay/spectate/replay、現在と未来のseq、同一byteの再送と別seqの再通知、resume/snapshotを挟む同一attempt、観戦初期snapshotを欠落後に再送する経路を区別する。WebSocketの実frame、Closeの送信、失敗後のdata停止はtransport実装での結合試験対象であり、このpayload検査だけではRFC 6455の接続終了を実証しない。

完全候補の照合では期待集合の一部だけを検査せず、順序とconsumedの並びを除いた集合全体を比較する。現行coreの候補数には余裕がある。自摸番は打牌15以下・リーチ15以下・暗槓3以下・加槓4以下・和了1・九種九牌1の合計39以下、反応はchiの3順子×4赤牌消費パターン×13打牌、ponの3消費パターン×13打牌、daiminkan4消費パターン、hora/none各1の合計201以下という保守的上限があり、512を超えない。実際の牌枚数・喰い替えはこれをさらに制限する。私的拡張は独自の上限・fixtureと第14節の契約を必要とする。

## 検証の範囲

- 標準ライブラリの validator は、この版で使用する JSON Schema assertion の部分集合を実装する。未対応 keyword と nested `$id` は拒否する。Nix の検査では固定した JSON Schema 実装によるメタ Schema と正例の検査も行う。
- game_contractは完全な1判断局面の候補生成、精算済み条件からの次局、受信者が観測できるevent状態を検査する。和了では役IDの重複・排他、複合役満の両立、門前／副露、適用ルール、公開されたリーチ・和了原因と20符・25符の例外条件との整合を検査してから、符・飜・役満による金額、本場・供託・責任払いを含む差額を照合する。和了牌・可視手牌・公開副露の牌種条件、字牌面子の残り枠、公開面子で確定する役の欠落と符の下限も検査する。他家の非公開手牌やホストの牌山順列は復元しない。これらの必要条件の通過だけで非公開部分の役・符の成立を証明したとは扱わず、完全な判定にはホストの完全情報と採点fixtureが必要である。
- event 投影の fixture は event payload と前後状態を検査する。envelope、request、ACK の配送と時計は wire trace、request contract、形式モデルで扱い、Receiver で観測可能な部分を接続する。
- 5つの形式モデルは、それぞれの有限境界と環境仮定の下で性質を検査する。牌の全組合せ、任意の拡張・ネットワーク、認証サービス、実装コードとの refinement 証明、モデル間の合成証明は対象外である。

## 境界条件の検査

| 対象 | 主な検査 |
|---|---|
| 交渉と識別子 | version/profile/hash の組、mode/view の拒否理由、文字列全体の制約、session ごとの拡張Schema |
| JSON と方向 | 不正JSON、frame、未交渉の kind、送信方向。同じ seq や置換済み範囲でも必要な構造検査を行う |
| 要求とACK | 全3人の選択、期限ちょうどの既定選択、選択元と計時の固定、優先結果、不採用・見送り・取消しと結果eventの対応 |
| 再接続とsnapshot | 不変の再送範囲、範囲内のsnapshot、未使用seq、終端要求を復活させないこと、transaction途中の置換拒否、連続prefixの状態照合、残時間・選択・ACKの時計の単調性 |
| 牌と公開履歴 | 河と副露の一致、赤五を含む加槓、槓宣言牌の在庫、リーチ成立、複合打牌、槓ドラ公開時点 |
| 局進行 | 通常・途中流局、次局、延長の場風循環、配牌回数、局内・局間・終局での復元 |
| 採点と精算 | 全和了fixture、複数ロンの共有局面、役・符・本場・供託・責任払い、表裏表示牌を含む物理牌在庫 |
| 原子性 | 不正入力の拒否で既存状態を部分更新しないこと、失敗した再開でtokenを消費しないこと |
| hash | identity hashだけを正規化し、同じ文字列を持つ注釈・配列・nested member・wireの他のbyteを保持すること |

公式ベクトルの `schema_negative: true` は、独立した JSON Schema 検査でも拒否すべき入力を指定する。残りの意味的な不正入力は、状態検査や採点検査で拒否を確認する。完結した wire trace では、見送りや不採用の ACK に対応する結果も検査する。

検査の成功は各検査層の範囲内の結果である。適合表明は [仕様書第17節](../docs/yamai-protocol.md#17-適合性) に従い、独立実装間の相互運用性は別途検証する。
