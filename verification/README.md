# YAMAI 検証ガイド

本書は YAMAI draft 1 の検査方法、適合35項目との対応、各検査の範囲を定義する。Protocol Version と `riichi-4p` revision は `1.0-draft.1`。規範要件は [仕様書](../docs/yamai-protocol.md)、対象ファイルは [release manifest](../release-manifest.json) に従う。

## 実行方法

コマンドはリポジトリのルートで実行する。次の検査には Python 3 の標準ライブラリだけを使用する。

```sh
python3 scripts/validate_artifacts.py
python3 scripts/test_validator.py
python3 scripts/test_exact_decimal.py
python3 scripts/test_scoring_reference.py
python3 scripts/test_session_contract.py
python3 scripts/test_game_contract.py
python3 scripts/test_detached_contract.py
python3 scripts/test_resource_contract.py
python3 scripts/test_tooling.py
python3 tests/test_regressions.py
python3 scripts/score_oracle.py
```

[flake.nix](../flake.nix) と [flake.lock](../flake.lock) は Python、JSON Schema 検査実装、Quint、TLC、Java、Z3 を含む検証環境を固定する。Nix の flakes が有効な環境では、次のコマンドで上記の検査、独立した Draft 2020-12 検査、6つの形式モデルの検査を実行できる。

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
| [detached_contract.py](../scripts/detached_contract.py) | fatal終了seatの固定、内部準備barrier、既存選択と期限の保存、以後の自動応答とbank継続 |
| [resource_contract.py](../scripts/resource_contract.py) | session単位の累積control/ledger/replay予算、超過前の原子的拒否。実装全体のmemoryやschedulerの証明ではない |
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
| 24 | §5/6/14/19 | V63–V93/V127–V142/V423–V425 | revision対応表、hash、capabilityと拡張Schemaのsession分離 |
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

V418–V422は項目11・16・30を補完する。V418はホストが信頼境界内と判断した局所接続でのresumeを許可し、同じ条件をjoinの拡張memberで自己申告しても許可しない。traceの `secure_transport` と `trusted_local_transport` は検査用のホスト側入力であり、wire memberではない。回帰検査では拒否時のtoken・期限・接続所有権の保持も確認する。V419–V420は未来seqの配牌・自摸にも交渉済みviewを適用し、投影違反をsequence_gapより先に拒否する。V421–V422はplayer actionへのseq/original_seq付加をfatalとして検査し、action_idだけの不正に許可されるrecoverableとの区別を確認する。回帰検査は全8通りのmode/view、終局後、null・型不正も含む。

追加回帰では、Player→Hostの妥当なfatal通知もsessionを閉鎖し、その後の入力を適用せず新しいHost出力を拒否することを検査する。通常の通信断・再接続とは区別し、閉鎖時に既存の選択・時計・game状態を変更しない。request lifecycleとの照合では、同一seqの再配送を新しいACKと数えず、閉鎖後の内部終端記録を閉鎖sessionへのwire出力として要求しない。閉鎖前の診断・ACKの内容と順序、および存続sessionの完全な出力照合は維持する。visibilityの検査はsnapshot内の`last_event`が`start_kyoku`である場合にも手牌の再帰的な投影を適用し、公開・seat・fullの各viewで正常な投影と漏洩を区別する。局精算の回帰では、リーチ宣言打牌で成立する自動流局について、`reach_accepted`と供託を省略した精算を拒否し、ロン・三家和・チョンボの取消し例外を維持する。

Receiverとvalidatorの回帰テストは項目15・16・30を補完する。Receiverは、欠番後の検証済みfatal errorを終了診断として受理し、適用済みprefix・未解決request・時計を変更せず、buffer済みmessageも停止する。Receiver回帰では全8通りのmode/view、通常の連続fatal、recoverable error、ID・方向・Schema違反、保持済みseqの衝突、snapshot置換済み範囲を区別する。履歴を失っても正準snapshotによるresumeが可能な経路と、代替がない拒否経路も確認する。validatorはaction_idを許可する診断codeだけへ制限し、一般のrequest_idとrequest_conflict専用のoriginal_statusを区別する。

観戦初期化の追加回帰では、途中参加sessionの初期snapshot要件が欠番検出後も残り、start_gameで代用できないことを確認する。再開welcomeは新しいtokenを要求し、現在・過去のtoken再利用、および既に受信または告知されたseq上限の巻戻しを拒否する。拒否した再開はtoken履歴・適用位置・復旧状態を変更しない。V384–V388の正常な再開もtokenを更新し、V384–V387は初回の告知上限と後の再開範囲を一致させる。

拡張Schemaの回帰では、交渉済みerror制約をHost/Player両方向のapplication入口へ適用し、方向固有のenvelope制約とsession分離を保持する。独立したDraft 2020-12実装でも合成結果を検査する。検査fixture自身の回帰では、JSONL chunk・WebSocket fragmentの型不正を拒否し、資源カウンタにbooleanを受理しない。これらは検証ハーネスの入力検査であり、実transport実装の検証を代替しない。manifestのrepository外参照は、symlinkを経由する場合も拒否する。

数値の回帰テストは、有限な小数の受信、拡張候補の数値同値性、`multipleOf` の厳密な判定を検査する。binary64への変換やDecimal contextの精度によって結果を変えず、巨大な指数差でもその指数分の整数を展開しない。要求の回帰では、交渉済みgraceを含むgroup期限を現在・未来seqの両方で検査し、拒否されたattemptと固定済みselectionの時計を分離する。未知requestのrecoverable診断やreject-policyのACK・errorを入力ごとに照合し、診断ID省略、再送、期限後と終局後を区別する。公開手牌の回帰では、全4面子から否定できる役の申告を拒否し、正しい隣接形と非公開部分が残る局面を区別する。

単独requestの起点と入力履歴が揃うcaptureでは、診断応答の過不足も検査する。groupの診断はrequest lifecycleと組み合わせ、snapshotで省略された過去の診断は復旧検査の範囲として扱う。受信側に記録がないrequest IDを、ホストにも未知であると推測してはならない。

private actionの候補検査は、名前付き引数・配列順・入れ子objectを保持した構造比較で完全同値の重複を拒否する。所有者が注釈として定めたmemberを除いた意味的同値性や、core action上の状態を変えるnamespaced memberの意味検査は、所有capabilityの実装が別途行う。この参照検査の通過だけで、意味検査を持たないendpointがその拡張に対応していると表明してはならない。

完全候補の照合では期待集合の一部だけを検査せず、順序とconsumedの並びを除いた集合全体を比較する。現行coreの候補数には余裕がある。自摸番は打牌15以下・リーチ15以下・暗槓3以下・加槓4以下・和了1・九種九牌1の合計39以下、反応はchiの3順子×4赤牌消費パターン×13打牌、ponの3消費パターン×13打牌、daiminkan4消費パターン、hora/none各1の合計201以下という保守的上限があり、512を超えない。実際の牌枚数・喰い替えはこれをさらに制限する。私的拡張は独自の上限・fixtureと第14節の契約を必要とする。

追加回帰では、自摸番の単独requestにchi・pon・daiminkanが混在することを、原因eventを持たない部分captureでも拒否する。暗槓宣言の見逃しは、国士無双による槍槓がルール上許可された場合だけフリテン遷移の対象とし、単独のfuriten検査とevent適用で同じ条件を使用する。リーチ後の暗槓宣言を含むsnapshotでは、手牌が公開されている場合に待ち・面子構成の不変条件をevent適用と同様に検査し、非公開手牌の構成は推測しない。snapshotで元の終端ACKが省略された要求でも、一度観測した後着stale ACKの時計を別attemptや再接続後に変更できないことを検査する。河底の非空event投影は、最後の自摸とその直後の同一actorの打牌を要求し、原因自摸の欠落・別actorの打牌・二度目以降の打牌を拒否する。最後の自摸後の手出しは引き続き許可する。

V423–V425は安定capabilityの閉じた列挙を検査する。`hello` / `join` のrequired・optionalと `welcome.capabilities` で、登録済みの `resume` / `snapshot` および妥当な実験値を正例とし、未登録の安定値をSchemaと意味検査の両方で `invalid_message` として拒否する。交渉回帰ではjoin-proposal入口、双方の同じ未知値の提示、版・profile・view・limit違反との優先順と拒否時の非変更を確認する。登録済み必須機能のpeer不対応と未知の必須実験値は従来どおり `unsupported_capability`、片側だけの任意実験値は無視し、交渉済み拡張Schemaの検査も維持する。registry・Schema・実装の登録済み安定値の一致をrelease検査で確認する。

## 検証の範囲

- 標準ライブラリの validator は、この版で使用する JSON Schema assertion の部分集合を実装する。未対応 keyword と nested `$id` は拒否する。Nix の検査では固定した JSON Schema 実装によるメタ Schema と正例の検査も行う。
- game_contractは完全な1判断局面の候補生成、精算済み条件からの次局、受信者が観測できるevent状態を検査する。和了では役IDの重複・排他、複合役満の両立、門前／副露、適用ルール、公開されたリーチ・和了原因と20符・25符の例外条件との整合を検査してから、符・飜・役満による金額、本場・供託・責任払いを含む差額を照合する。和了牌・可視手牌・公開副露の牌種条件、字牌面子の残り枠、公開面子で確定する役の欠落と符の下限も検査する。他家の非公開手牌やホストの牌山順列は復元しない。これらの必要条件の通過だけで非公開部分の役・符の成立を証明したとは扱わず、完全な判定にはホストの完全情報と採点fixtureが必要である。
- event 投影の fixture は event payload と前後状態を検査する。envelope、request、ACK の配送と時計は wire trace、request contract、形式モデルで扱い、Receiver で観測可能な部分を接続する。
- 6つの形式モデルは、それぞれの有限境界と環境仮定の下で性質を検査する。牌の全組合せ、任意の拡張・ネットワーク、認証サービス、実装コードとの refinement 証明、モデル間の合成証明は対象外である。

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

## 資源・transport境界の追加検査

`python3 scripts/test_resource_contract.py` は有限予算のちょうどの上限と超過、別IDの後着attempt、無応答の重複input、累積replayの消費と原子性を検査する。counterはsessionが所有し、queue排出やsnapshot、再接続で新しいSessionBudgetを生成してはならない。参考値はサービス設定の出発点であり、wireの受信上限の追加ではない。

`test_session_contract.py` は送信圧迫状態の微量drain、予約された結果の配送期限、およびWebSocketからJSONLへの復旧時に元payloadを変更せずsnapshotまたは拒否へfallbackする境界を検査する。replay_plan traceの省略可能なtarget_transportはwebsocket（既定）またはjsonlを指定する。`snapshot=True` は、呼出し側が交渉と内容を検証したsnapshotを提供できることを表し、helper自体によるsnapshot生成・妥当性証明ではない。

## Fatal終了seatの継続

§8.1.2のDETACHEDは新しいwire messageではなく、host内部のseat制御状態である。request_contractとdetached_contractの回帰は、閉鎖前の選択保存、元deadlineでのdefault、以後の単独/全3member判断、通常のbank消費、閉鎖sessionのwire停止と再開/途中交代の拒否を検査する。通常の通信断はDETACHEDにせず再配送履歴を保持する。閉鎖sessionの資源予算を内部game進行へ再利用しないが、game全体の資源枯渇やhost停止を解決するものではない。

## 復旧・局結果の対照回帰

resumeのcaptureでは、元のrequest発行・選択と、同じledgerを再配送した時刻を区別する。過去のaccepted/defaulted ACKや診断を再送するために、新しい接続で元のactionを再入力してはならない。OPEN requestへ再開後に入力する場合も元の時計を使い、再配送でG/T/Bを付与し直さない。交渉順序とreplay範囲の完了前に送られたactionも別途検査する。

再開後の新しい入力・終端処理の時計を照合するcaptureでは、ホストが保持した元の状態を `context.resume_state.request_states` に記録する。request IDをキーとし、値は元の `issued_at_ms` と `selection`（OPENならnull、固定済みなら `action_id`・`source`・`elapsed_ms`・`time_bank_ms`）を持つ。sourceはuserまたはdefaultであり、wireへ追加するmemberではない。この注釈を使用する場合、元の発行時刻、resume checkpointの `context.now_ms` とcaptureの `at_ms` は同じ単調時計を使い、checkpointはjoinとwelcomeの間に置く。

過去のledgerを再送するだけなら、この元入力の注釈は不要である。過去のOPEN snapshotがcheckpoint時点の固定済み選択より前である場合も、元の履歴をそのまま検査する。新しいsnapshotについてはcheckpoint以後・capture以前という範囲を照合し、配送時刻そのものをsnapshot固定時刻と推測しない。元時計を必要とする新しい処理に十分な証拠がないcaptureは不合格とし、再配送を新しい発行として補わない。これは検査用captureの条件であり、注釈がないことだけを理由にwireの相手へerrorを送る要件ではない。

group descriptorはseatとrequest IDの対応を保持し、配列順だけの変更を別groupやrequestの変更と判定しない。lifecycle fixture内のrequest payloadにも、wireのrequest Schemaと同じ型・数値範囲・member制約を適用する。意味検査だけの通過や、期待値が同じ計算実装に従うことを、構造検査の代わりにしない。

ledgerの時計検査では、開始前にbufferされたactionに対するaccepted・defaulted・rejected ACKを、元のrequest開始時刻より前に発行できないことを検査する。全memberのlifecycle注釈がないgroupにも、観測できる開始時刻とACK時点までの経過時間の上限を適用する。開始時刻ちょうどのelapsed=0のACKと、過去のimmutable ACKの再配送は引き続き許可する。

リーチ宣言打牌の反応待ちsnapshotにも、供託を支払える点数・4枚以上のlive wall・公開手牌の聴牌という宣言時条件を適用する。成立済みリーチは、公開された13枚相当の手牌が聴牌を保つことを検査する。点数・山残量の宣言時条件を再適用せず、非公開手牌の聴牌や14枚相当の手牌の元の形は推測しない。チョンボの局精算は、現在の判断に要求を持つseatだけを対象とし、自摸前や嶺上自摸前の要求がない時点を拒否する。単独要求の固定済みselection、group共通期限の到達、または全選択が確定したresolving状態をsnapshotで観測した場合も、その後のチョンボによる取消しを拒否する。反応groupの別seatにOPEN要求が残り得る場合の取消しは許可するが、既にSELECTEDと観測したseatを違反者にできない。

役IDの追加回帰では、小三元に必要な三元牌の役牌2種と、3種全てが大三元を確定させる場合を区別する。門前自摸と対々和が四暗刻を確定させる場合、混老頭に対々和または七対子が必要なこと、混老頭と清一色の不両立も検査する。一盃口と三色同順または一気通貫の併記には4順子が必要であり、公開刻子や役牌の面子をさらに加えられない。純全帯と清一色の併記では、雀頭と異なる老頭牌だけが刻子になれるため、その面子数は公開面子を含め1以下である。槓子があれば、その老頭牌は使い切られ、残り3順子と雀頭にもう一方の老頭牌が5枚必要になるため、暗槓・大明槓・加槓はいずれも成立しない。場風と自風が同一か異なるかも含め、申告された役の必要面子が4組へ収まることを検査し、必要牌が全て字牌と確定する小三元の申告では字一色の役満を省略できない。これらは非公開牌を補う検査ではなく、既に申告された役と公開局面だけから確定する条件である。

数値は有限なJSON値を保存し、指数がPythonのDecimalの範囲を超える場合も、係数と指数を入力長に比例する表現で保持して比較する。指数に比例する整数や0列は展開しない。0の巨大指数表記、有限小数、数値同値性、大小比較、multipleOfとmessage上限の対照回帰を含む。この表現は検査用の比較・整除判定に限定し、任意精度の一般的な算術エンジンとしては扱わない。

`test_exact_decimal.py` は標準ライブラリDecimalのC実装とPython実装を別processで検査する。整数文字列の変換上限を小さくした環境でも、大きな係数・指数の有限小数、比較、multipleOfを同じ結果として扱い、検査実装がprocess全体の変換上限を変更しないことを確認する。profile hashは計算時に自己参照fieldを除外するが、registryの `profiles[].hash` 自体の存在・型・形式と計算結果との一致を別途検査する。交渉済み拡張のSchemaがactorを制限しない場合でも、coreの整数seat・所有者制約を維持する。

request IDの回帰は、自分宛ての要求とgroup descriptorで観測した他seatの要求を区別する。観測済みIDをseat・原因event・groupへ結び付け、別判断や別seatでの再利用をsnapshot・resume後も拒否する。同じpending requestのsnapshot復元とdescriptor配列の並べ替えは許可し、他seatのIDを観測しただけで自分宛てのstale ACKを許可しない。snapshotで省略された過去要求へのstale ACKにも、既知の所有者と終端IDの再利用禁止を適用する。

局結果の回帰では、最終通常自摸のsnapshotが海底資格を保持すること、同じリーチ宣言打牌へのpenaltyで供託を控除しないこと、全手牌が可視の国士無双に必要な13種が揃うことを検査する。最終嶺上、リーチ成立後の次手番のpenalty、非公開手牌が残るviewは合法な対照例として区別する。これらは観測可能な必要条件の検査であり、受信者による非公開手牌の復元を要求しない。

## 公開履歴から確定するsnapshotと三家和の必要条件

追加回帰では、無副露のsnapshotについて河枚数と親からの手番順を照合する。初回の子自摸、未成立暗槓、複数巡、resolvingを区別し、鳴きによる手番飛ばしへ通常巡の式を適用しない。四家立直・四風連打が採用されていれば必須流局後の局内snapshotを拒否し、4人目の反応待ちとルール無効時の継続は許可する。成立済みリーチの宣言牌より後の河は自摸切りを保持し、宣言牌自体の手出しは許可する。拒否時には受信位置・ledger・対局状態を保持する。

三家和では、打牌に対する可視手牌が和了形を成すという必要条件を既存の待ち計算で検査する。public・full・座席replayを比較し、非公開手牌は推測しない。これは完全な役・フリテン・選択履歴の証明ではなく、それらには既存の採点fixtureとrequest lifecycle検査が必要である。暗槓の国士無双専用条件も維持する。
