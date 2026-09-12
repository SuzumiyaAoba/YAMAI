# 仕様完成監査

本書は仕様完成作業の監査記録であり、wire の規範要件を追加しない。対象は YRC 0003、YRC 0005、両文書の Schema・registry・公式 vector、release 管理、および補助状態モデルである。安定版公開や第三者の承認を、文書の完成と同一視しない。

2026-09-12 の開始時点は `57ad4dc`、`main`、Protocol draft.5/profile draft.3、作業ツリーに差分なし。既存 validator は Schema 19、protocol vector 34、点数計算 vector 14、scoring fixture 59 で成功した。しかし以下の本文矛盾と検査漏れがあるため、この成功は仕様完成の証拠にならない。

| ID | 対象・完了に必要な条件 | 開始時点で確認した不備 | 状態・証拠 |
|---|---|---|---|
| A01 | JSON・transport・上限（YRC 0003 §4,15,18） | JSONL の最大 payload と CRLF を含む buffer 上限が衝突。深さの数え方、未完 frame の時間上限、byte 同一性の対象が不明確 | 本文・検査完了。V02/V06/V07/V116–V122/V139/V141、parser回帰20件でbyte境界・深さ・60秒時計・予約済みbackpressureを確認。 |
| A02 | envelope・交渉・error・拡張（§5,6,12,14,19） | replay の event 専用 seq と error/snapshot の共通 seq が衝突。capability 有効集合、join/welcome 対応、版とエラーの検査順の不足 | 本文・検査完了。V63–V103/V127–V142、join-proposal Schema、版/profile行列、全17error codeの方向・段階・severity照合を追加。 |
| A03 | 合法手・牌山・フリテン・リーチ・喰い替え（§7,8,10; YRC 0005 §2） | 牌山初期値、嶺上補充、リーチ後の暗槓、フリテン成立・解除、喰い替えの具体的規則が不足 | 本文・検査完了。game_contractが完全情報の判断局面から候補を再生成。V176–V210/V257–V260で全action、赤牌、待ちの全分解、フリテン、喰い替えと供託・牌山境界を確認。 |
| A04 | request・group・ACK・期限・再送（§8,9, Appendix A） | 終端ACK後に優先順位を決める循環、deadline の `<=` と timeout 優先の衝突、default の競合結果、rejected 後の計時、chombo offender の終端化が曖昧 | 本文・検査完了。V35–V50を他家3seatのgroupへ同期し、V52/V60/V254–V256で原因seatと集合・重複候補を検査。request_contractと全3seatのrequest形式モデルの規範対応も確認。 |
| A05 | 鳴き・槓・リーチ・途中流局の event 順序（§7,10） | 複合鳴き打牌と追加打牌 request が衝突。槍槓時の dora、連続槓、鳴かれたリーチの成立、pao の挿入位置に不足・矛盾 | 本文・検査完了。V211–V253の全種槓ドラ・連続槓・宣言取消し・四槓散了・鳴かれたリーチ・pao順序を実行。EventStateが可視手牌、公開meld、136枚の保存、供託と局進行を検査。 |
| A06 | 局進行・精算・最終順位（§7; YRC 0005 §7,8） | end_game の next.kyotaku が表から欠落。親連荘時の延長上限、penalty の本場・親流れ、pao と対象外役満の配分が曖昧 | 本文・検査完了。V151–V175で閾値の等号、トビ優先、連荘、アガリ止め、延長上限・風の一周、penalty、本場・終了時供託を確認。精算本体はA09の入力から再計算。 |
| A07 | visibility・spectate・replay（§11） | 観戦の途中参加手順が未定義。全体 phase に対し受信seat以外の pending request を要求。暗槓・pao からの秘匿情報の導出範囲が不明確 | 本文・検査完了。V143–V150でplay/public/full/全座席replayの投影、V111–V113で初回観戦、V250–V253で公開meldだけによるpaoを確認。 |
| A08 | resume・snapshot（§13） | 局外の供託・進行状態が欠落。残り期限が timeout_ms の上限を超え得る。未ACK選択の保持、復元された原因 event、group・meld・pao の構造不足 | 本文・検査完了。V53–V62/V85–V93/V105–V115/V124–V125/V135–V140でtoken、選択保持、有限prefix、初回snapshot、終了済みsessionを検査。snapshotと後続eventの状態もReceiverへ接続。 |
| A09 | 全役・符・点数と実行可能fixture（YRC 0005 全節） | Appendix A.6 が清一色・二盃口を落としている。A.7 の「役なし」例に一気通貫がある。fixture は役と符を手牌から再計算していない | 採点監査完了。期待値を含まない入力、全分解と役・符・bonus・pao・供託・scoresの再計算、直前状態からの採点event投影を追加。76正例・28負例、14算術/参照索引、6不変条件テストが成功。全wire状態・合法手・フリテン生成はA03/A05で別途確認する |
| A10 | Schema・registry・hash・validator | replay seat view の dict を set と比較して例外になる。replay game target を本文に反して拒否。snapshot.lastEvent が start_kyoku を表せない。JCS の UTF-16 key 順と小数禁止を未検査 | 本文・検査完了。Schema 20の全参照・assertion対応範囲・registry列挙・版pin・hash・本文JSON例を照合。Nix gateへ独立したDraft 2020-12メタSchema/instance検査も追加。 |
| A11 | 形式モデルと規範対応 | 既存 model は group の各ACK後に close するため、新しい競合解決の規範と対応を再検証する必要がある。古い行番号参照あり | 4モデルを更新し、READMEの対応を節番号へ変更。個別TLCでbaseline安全性・5時間的性質、extended約969万状態の安全性、request安全性・4時間的性質、delivery約33万状態の安全性・3時間的性質が成功。runは計14件成功。4つのNix witness gateも成功し、形式モデルの同期を完了 |
| A12 | 版・成果物・適合項目・完成監査（§16,17; process） | §17 の29項目について本文・正負 vector・検査範囲の対応がない。規範変更の版更新・hash・release manifest・CHANGELOG の同期が必要 | draft.6/profile draft.4の本文・Schema・registry・260正負vector・補助検査を同期。下表で適合29項目を対応付け。最終stdlib検査、独立JSON Schema検査、Nixの全gateが成功し、全12監査項目の対応確認を完了した。 |

各行は本文、Schema、正例・負例と実行結果を確認してから完了へ変更する。既存検査にない要件を「検査成功」だけで完了にしない。仕様上の選択は本文に一意に固定し、補助モデルの有限性、scoring の検証範囲、独立実装試験の有無は区別して記録する。

## 2026-09-12 の検証記録

- `rtk python3 scripts/validate_artifacts.py`: 成功。Schema 19、protocol正負vector 62組、点数計算vector 14、scoring fixture 59、本文JSON例15。scoringの結果は既存の構造・精算整合検査であり、手牌からの全役・符の再計算ではない。
- `rtk python3 scripts/test_validator.py`: 15件成功。UTF-8/CRLF分割、1 MiBちょうどと超過、空containerの深さ、重複key、整数表記、小数の丸め回避、Unicode、Schemaの型別同値性、JCSのUTF-16順と対象外数値拒否。
- 初回修正時のprofile hash: `sha256:3dd0e1117861128cc04a183b31da6d558d977b5880ad272aa4e76a9fbb1d10c8`。
- `rtk git diff --check`: 成功。ブランチはmainのまま。commit・tag・公開は行っていない。

## 採点と要求モデルの追加検証

採点監査時のprofile hash: `sha256:82d33811fe4cf76c8c23bf35bb43762bc2697e0611a02899442d98b2cb99d50c`。

- 全成果物validatorは、Schema 19、protocol正負vector 62組、算術/参照索引14、採点正例76・負例28、本文JSON例15で成功した。採点は期待値から条件を取らず、全役・符・配分を入力から再計算している。
- `rtk python3 scripts/test_scoring_reference.py`: 6件成功。牌順に依存しない最大解釈、役なし聴牌と5枚目除外、ロン暗刻と四暗刻、役満のbonus除外、フリテン自摸、pao無効時の支払いを検証した。
- `yamai_request_liveness.qnt`: Quint 0.32.0でtypecheck、TLC安全性、4時間的性質、5件のrunが成功。ランダム探索では11witnessを確認し、三家和の到達性は具体的なsanchahoTestで確認した。
- `rtk proxy nix --option eval-cache false build path:.#checks.aarch64-darwin.quint-request-liveness-witnesses --no-link --print-out-paths`: 成功。出力は `/nix/store/j5rs1yzsvzk1ngb62xhynip14jd8vcxw-yamai-quint-request-liveness-witnesses`。関連モデルのgateを独立させ、各モデルのparse→safety→temporal→witnessは維持した。全モデルを実行するflake checkの対象は減らしていない。
- 検証ツールのTLC backendを確認した。TLCはq_init/q_stepの有限状態空間を検査し、CLIのmax-stepsによるシミュレーションを完全検査の代わりに使用していない。runはTLCへ混ぜず別途実行する。

## 配送・セッションモデルの更新

- deliveryを不変のmessage履歴、host_seq、applied_seq、有限replay frontierへ分離。snapshot置換・1回の切断・gap・選択済み要求と、再送開始後の追加分を扱う。安全性は330,932 distinct states、3時間的性質もTLCで成功した。
- baseline/extendedはdefaultを選択固定だけに変更し、全選択の後で一度だけ優先順位を決めてACKを生成する。切断中にapplied_seqを進めない。baselineの終了済みsessionへの再送も修正した。
- extendedの空replay範囲がMAX_SEQ+1を使う境界、重複end_kyoku、配送待ちで終局処理が止まる経路を修正した。snapshot/replayの終了後に新しいbacklogを残し、終了済みsessionへも配送する。最新安全性は9,692,180 distinct states、残りqueue 0で完了した。
- baseline 3件、extended 3件、request 5件、delivery 3件の具体的runが成功した。終了後のresume、選択保持snapshot、空の再送範囲を閉じた後の追加配送を含む。
- Nixの4つのwitness gateは全て成功した。baselineは7、extendedは13、requestは11、deliveryは5種類のwitnessが1回以上到達し、runは計14件成功した。
- Nix出力: baseline `/nix/store/j67nn8wmlb75rr2m1cjbg6nb4ww9p67i-yamai-quint-model-witnesses`、extended `/nix/store/0l6i1rxz3n05567qhq5f86xk72w498wv-yamai-quint-model-extended-witnesses`、request `/nix/store/xv07nak3g1yq0pbf4z8h3sm3yjnqd6z2-yamai-quint-request-liveness-witnesses`、delivery `/nix/store/gck601p5z12jdch43mgwkb147cskkc2v-yamai-quint-resume-delivery-witnesses`。exec session 38711はexit 0で完了している。


これ以降の確認は下記の対応表と最終統合gateにまとめる。

## 適合35項目との対応

番号はYRC 0003 §17と一致する。V番号は現行[公式vectors](../test-vectors/yrc-0003/1.0-draft.6/vectors.json)のID接頭辞であり、各行のvectorは正例と負例を持つ。採点fixtureは[scoring.json](../test-vectors/yrc-0005/1.0-draft.4/scoring.json)にある。本文だけの要件を、Schemaが全て検証したとは扱わない。

| §17 | 主な本文 | 正負vector・実行検査 | 検証する境界 |
|---|---|---|---|
| 1 | §6.4 | V63–V74、V133–V134 | 構造・版・profile・ルール拒否の順序 |
| 2 | §4.2 | V02/V06、test_validator | UTF-8/CRLFの分割、複数frame、EOF |
| 3 | §5/13 | V105–V108/V113 | 連続prefix、同一byte再送、衝突時の原子的拒否 |
| 4 | §8/9 | V35–V50/V100–V103 | 正常・後着・重複・別IDの再送 |
| 5 | §7.3/10 | V176–V196/V235–V246/V253/V258–V259 | 全鳴き、宣言と成立、槍槓時の取消し |
| 6 | §8.3/10.3 | V197–V200/V247–V249 | 複合リーチ、鳴かれた打牌、供託の一回控除 |
| 7 | §7.3 | V192–V196/V256/V258–V260 | 赤牌の物理枚数、consumed multiset、同値牌の自摸切り区別 |
| 8 | §8.4 | V41–V43/V48、採点複数ロンfixture | 明示選択だけを数え、頭ハネ距離・三家和を確定 |
| 9 | §7.2 | V201–V210/V227–V234/V245–V246 | 九種九牌、見逃し、途中流局の優先順、通常流局 |
| 10 | §8.5/9.1 | V35–V39/V44/V46/V50 | strict deadline、0期限、既定選択、rejected後の時計 |
| 11 | §11 | V22/V143–V150 | 自分・他家・public・fullでの牌の投影 |
| 12 | §4.1/7.2/15 | V05/V06/V70–V72/V119、test_validator、全候補fixture | byte/深さ/512候補上限、切り詰め禁止 |
| 13 | §10.2 | V211–V226/V239–V244 | 槓種ごとの公開時点、保留ドラと連続槓 |
| 14 | §7.5、YRC 0005 §3–8 | V14/V23、採点76正例・28負例 | 全分解からの役・符・bonus・役満・支払い |
| 15 | §13.1/13.3 | V105–V115/V124 | 欠落で適用しない、有限再送、snapshot floor |
| 16 | §13.1/13.2 | V85–V93/V135–V140 | rotate/期限切れ/一回使用/失敗時非消費 |
| 17 | §7.4/11 | V17/V104/V123/V125 | 終局後の次session、旧action無視、同点順位 |
| 18 | §13.3 | V53–V62、EventStateとsnapshot検査 | 未使用seq、手牌枚数、待ち要求、状態と残量 |
| 19 | §9/13 | V19/V55/V60/V62/V109–V110、delivery形式モデル | 固定選択・元の時計、空範囲を含む再配送 |
| 20 | YRC 0005 §7/8 | V20、採点multiple-ron/pao fixture | 本場配分、責任役満成分、供託の別会計 |
| 21 | §7.2 | V151–V175 | トビ・連荘・アガリ止め・延長の順序 |
| 22 | §6.2/11 | V28/V30/V51/V78–V82/V94–V95/V143–V150 | mode/view/target、観戦のrequest/ACK禁止 |
| 23 | YRC 0005 全節 | scoring_reference、76正例・28負例・14索引・6回帰 | 全登録役、全符項目、限界点、支払いの再計算 |
| 24 | §6/14/19 | V63–V93/V127–V142 | revision対応表、hash、capabilityと拡張Schemaのsession分離 |
| 25 | §8.1/8.4 | V31–V50/V52/V60/V254–V256、request形式モデル | 他家3人、個別/共通期限、選択後に一度だけ競合解決 |
| 26 | §8.5/10、YRC 0005 §7.3/8.2 | V26/V38/V250–V253、pao/penalty採点fixture | 公開meld順・責任seat、取消し、0点を含む支払い |
| 27 | §7.2/7.5、YRC 0005 §8 | V27/V57–V58/V151–V175、noten_0–noten_4 | 聴牌人数、供託繰越・配分・終了時残本数 |
| 28 | §10.2/11、YRC 0005 §5 | V211–V226/V124、裏ドラ採点fixture | 嶺上和了前の公開、裏ドラ枚数、元記録cursor |
| 29 | §9/15 | V31–V50/V116–V122/V139/V141、test_game_contract | 猶予、切り捨て前期限、予約済み出力、再送で二重課金しない |
| 30 | §3.1/6.4/12 | V63–V103/V108/V261、Receiver回帰 | Applyの原子性、errorの方向・優先順 |
| 31 | §3.2/5/13 | V105–V115/V261–V262 | wire ledger、再送byte、transactionの非交錯 |
| 32 | §8.1.1/8.4/9 | V35–V50/V263/V267 | 全3選択、strict deadline、ACKと結果の同一transaction |
| 33 | §6.2/13 | V85–V93/V136/V266/V268–V270 | 明示seat、最小空席、resumeでの再指定禁止、replay target |
| 34 | §7.2/10.3 | V151–V175/V241–V249 | 連続槓・リーチの取消し、延長の上限と通し番号 |
| 35 | §11/13.3 | V53–V62/V143–V150/V264–V265 | snapshot・last_eventを含む全viewの非漏洩 |

完全候補の照合では期待集合の一部だけを検査せず、順序とconsumedの並びを除いた集合全体を比較する。現行coreの候補数には余裕がある。自摸番は打牌15以下・リーチ15以下・暗槓3以下・加槓4以下・和了1・九種九牌1の合計39以下、反応はchiの3順子×4赤牌消費パターン×13打牌、ponの3消費パターン×13打牌、daiminkan4消費パターン、hora/none各1の合計201以下という保守的上限があり、512を超えない。実際の牌枚数・喰い替えはこれをさらに制限する。私的拡張は独自の上限・fixtureと第14節の契約を必要とする。

## 検証の範囲

- stdlib validatorは、このreleaseで使用するJSON Schema assertionの部分集合を実装する。未対応keywordやnested $idを無視せず拒否する。Nix gateでは固定したjsonschema実装によるDraft 2020-12メタSchema・正例の独立検査も行う。
- game_contractは完全な1判断局面の候補生成、精算済み条件からのnext、観測可能なevent状態を検査する。受信者に見えない他家手牌やホストの山順列まで復元したとは主張しない。隠れた役の判定はホストの完全情報と採点fixtureに依存する。
- event投影のfixtureは完全なevent payloadを検証するが、envelope/request/ACKの全配送を毎回重複記録しない。配送と時計は別のwire trace・request contract・形式モデルで検証し、Receiverで観測可能な部分を接続する。
- 4形式モデルはREADMEに記載した有限抽象化であり、牌の全組合せ、任意拡張、任意ネットワーク、認証サービス、実装コードとのrefinement証明を対象にしない。
- Draft本文と整合成果物の完成、外部reviewerによる公開承認、独立相互運用試験、安定版releaseは区別する。本作業でcommit・tag・公開は行わず、publishedはfalseである。

## 最終統合検証

統合前profile hash: `sha256:f3812dcb0e554cf0e59b5559b15f9a1d490c120d71656a705c9b06632dc264cd`。Protocol正負vector 260組、Schema 20、点数の算術/参照索引14、採点正例76・負例28、本文JSON例15。2026-09-12、以下の全検査が成功し、Draft仕様と整合成果物の完成監査を完了した。

- 初回の最終flake checkで、成果物gateと独立JSON Schema検査（Schema20・instance517）は成功した。形式検証の並列compileで既定Apalache portの共有によりRPC CANCELLEDが発生した。Quint 0.32.0の接続・親終了時の停止処理を確認し、Nix wrapperでverify/compileごとに未使用loopback portを選ぶ修正を追加した。反例を無視したり、検査対象を減らしたりする修正ではない。

- stdlib成果物validatorは上記件数で成功した。parser20・採点6・session6・game8の計40回帰検査も成功した。牌136枚の保存、座席回転、手牌順、再送の二重課金防止、bank_scope、必要requestの省略拒否を含む。
- `rtk proxy nix --option eval-cache false flake check path:. --no-update-lock-file --keep-going`: exit 0、all checks passed。実行platformはaarch64-darwinであり、他の3platformでの実行結果を主張しない。型・安全性・時間的性質・14 run・36種類のwitnessが成功した。
- 独立jsonschema検査: Draft 2020-12のSchema20件と正例instance517件が成功。全$refをローカルregistryで解決し、暗黙のネットワーク取得を使用していない。
- 相対リンク43件と適合29行のvector ID参照を確認し、欠落なし。`git diff --check`も成功した。
- 最終Nix出力（artifact）: `/nix/store/z37pi5gh0gd7bz4lc6i8zra5z4bk5lpd-yamai-artifact-validator`。
- 最終Nix出力（baseline）: `/nix/store/k01fs9sx1m23paj33ipzng083m5rvmb2-yamai-quint-model-witnesses`。
- 最終Nix出力（extended）: `/nix/store/sw6j2icghd95gkf0rr4q9f0l2585v0xf-yamai-quint-model-extended-witnesses`。
- 最終Nix出力（request）: `/nix/store/zjhr1522p2vi4rlg4zj22kngsm6fara9-yamai-quint-request-liveness-witnesses`。
- 最終Nix出力（delivery）: `/nix/store/4hv49wj40ygfbsy2kzas0da1l1n5gb89-yamai-quint-resume-delivery-witnesses`。

公開承認と独立実装間の相互運用試験は今後の安定版release条件であり、このDraft完成作業では実施していない。既知の監査未完了項目はなく、main上の未commit変更として成果物を残した。

## 上流mainとの統合

commit & push時に、上流のc3450cb・b9d7dd8・858488cを検出した。これらを履歴へ残してdraft.6をrebaseし、Mermaid図と本文の自己完結化を保持した。採点定義を本文§7.6へ同期し、YRC 0005を派生資料にした。

旧版の要求・採点規則を混在させず、stateful traceをdraft.6へ移植した。新しいV261–V270はwire ledger、transaction、timeout、観戦snapshot、再帰投影、replay target、全3seatの競合とACK、seat割当を検査する。score_oracleのCLIは、旧版のfixture ID依存と供託の支払混在を解消したscoring_referenceを使用する。同一計算機への別CLIであり、二つの独立採点実装とは数えない。

旧Protocol Coreの2モデルは歴史的なdraft.5抽象化として保持し、そのbounded regression gateも維持した。draft.6の現行4モデルの検証を置き換えず、旧版の4並行decisionを現行のrefinement証明と呼ばない。

統合後はSchema21、Protocol正負vector270組、適合35項目である。profile hash: `sha256:43bd887586659141bb0b0f5de65aa308548533cb1625e3ef7b1053f2c91a6d48`。

- 成果物validatorはSchema21、Protocol270組、採点正例76・負例28、算術/参照14、本文JSON例15で成功した。parser20・採点6・session7・game8の計41回帰検査も成功した。
- score_oracle CLIは現行の76正例・28負例を再計算して成功した。独立JSON Schema実装によるSchema21件・instance524件の検査も成功した。
- `rtk proxy nix --option eval-cache false flake check path:. --no-update-lock-file --keep-going` はaarch64-darwinでexit 0、all checks passedとなった。現行4モデルのgateと、旧Core bounded traceの回帰gateを含む。
- 成果物gateの出力は `/nix/store/xvb0dvzhqf0fx12c1hb0qxwffj3jh0yj-yamai-artifact-validator`。相対リンク43件・適合35行・diffの空白検査も成功した。
- 上流の3コミットを親履歴へ保持し、mainへのrebaseを完了した。公開tagとpublishedの変更は行っていない。
