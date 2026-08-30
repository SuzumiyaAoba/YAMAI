# YRC 0005: YAMAI `riichi-4p` 役・符・点数規則

| 項目 | 値 |
|---|---|
| 文書系列 | YAMAI Request for Comments (YRC) |
| 文書番号 | YRC 0005 |
| 表題 | YAMAI Riichi Mahjong Four-Player Scoring Rules |
| 分類 | Standards Track |
| 状態 | Draft |
| 版 | 1.0-draft.2 |
| 発行日 | 2026-08-30 |
| 対応Protocol Version | YRC 0003 `1.0-draft.4` |
| 更新対象 | YRC 0003 `1.0-draft.4` の `riichi-4p` profile |
| 廃止対象 | なし |

## Abstract

本書は、YAMAI Protocolの `riichi-4p` profileが使用する標準役ID、役満condition、ドラbonus、符計算および点数計算を規定する。[YRC 0003] はtransport、状態遷移およびrule valueを規定し、本書はそれらの麻雀上の意味を規定する。

## Status of This Memo

本書はYAMAI Projectが管理するStandards Track Draftであり、IETF Internet Standardではない。本書の配布に制限はない。

本書は [YRC 0003] Protocol Version `1.0-draft.4` と組み合わせて使用する。本書と [YRC 0003] に矛盾がある場合、対局状態とwire処理は [YRC 0003]、役・符・点数は本書を優先する。矛盾は次のdraft revisionで修正しなければならない。

## Table of Contents

1. 要件語と適用範囲
2. 和了形と共通用語
3. 通常役
4. 役満
5. ドラbonus
6. 符
7. 基本点と支払い
8. 局精算との関係
9. 非対応ルール
10. Security Considerations
11. Registry Considerations
12. References
Appendix A. 計算例

## 1. 要件語と適用範囲

本書の **MUST**、**MUST NOT**、**SHOULD**、**SHOULD NOT** および **MAY** は [BCP 14] の意味で解釈する。

本書は4人リーチ麻雀だけを対象とする。3人麻雀、花牌、joker、ローカル役および焼き鳥等の最終精算は対象外である。

## 2. 和了形と共通用語

### 2.1 和了形

和了には少なくとも1個の役または役満が必要である（MUST）。ドラbonusだけでは和了できない。役満でない和了の `han` は、成立した通常役とドラbonusの合計でなければならない（MUST）。

通常形は4面子1雀頭である。面子は順子、刻子または槓子、雀頭は同一牌2枚である。例外形は七対子および国士無双である。

### 2.2 門前

チー、ポンまたは大明槓を含む手は副露手である。暗槓だけを含む手は門前を維持する。加槓は元のポンが副露であるため門前ではない。

### 2.3 牌分類

数牌の1・9と字牌を么九牌、2から8の数牌を中張牌とする。赤五は牌種判定では通常の五と同じであり、bonus計算だけで区別する。

`rules.red_fives.m`、`rules.red_fives.p` および `rules.red_fives.s` は、それぞれの色で通常の五を置換する赤五の物理枚数である。各色について赤五と通常の五の合計は4枚でなければならず（MUST）、赤五を通常の五へ追加して牌種の総数を増やしてはならない（MUST NOT）。

### 2.4 待ち

- 両面: 連続2牌の外側または内側のいずれでも順子を完成できる待ち
- 嵌張: 順子中央1牌の待ち
- 辺張: `12` の3または `89` の7だけを待つ形
- 双碰: 2個の対子のいずれかを刻子にする待ち
- 単騎: 雀頭を完成する待ち

同じ和了牌に複数の解釈がある場合、最終的な `hand_points` が最大となる合法な解釈を採用する。`hand_points` が同じ場合は `han` が大きい解釈、次に `fu` が大きい解釈を採用し、それでも同じ場合は成立役IDをASCII昇順に並べた配列の辞書順が小さい解釈を採用する（MUST）。

## 3. 通常役

表の「門前」は門前時の飜数、「副露」は副露時の飜数である。`-` は副露時に成立しない。

| Yaku ID | 門前 | 副露 | 成立条件 |
|---|---:|---:|---|
| `riichi` | 1 | - | 門前聴牌でリーチを宣言し `reach_accepted` が成立している |
| `double_riichi` | 2 | - | 鳴きのない自分の第一巡でリーチが成立している。`riichi`と重複しない |
| `ippatsu` | 1 | - | リーチ成立後、自分の次の打牌までに和了し、その間にチー・ポン・槓・北抜きがない |
| `menzen_tsumo` | 1 | - | 門前でツモ和了する |
| `tanyao` | 1 | 1または- | 手牌・和了牌・副露が全て中張牌。副露時は `rules.kuitan` がtrueの場合だけ成立 |
| `pinfu` | 1 | - | 全面子が順子、雀頭に符がなく、待ちが両面である |
| `iipeikou` | 1 | - | 同色同数の順子を2組持つ |
| `yakuhai_haku` | 1 | 1 | 白の刻子または槓子 |
| `yakuhai_hatsu` | 1 | 1 | 發の刻子または槓子 |
| `yakuhai_chun` | 1 | 1 | 中の刻子または槓子 |
| `seat_wind` | 1 | 1 | 自風の刻子または槓子 |
| `round_wind` | 1 | 1 | 場風の刻子または槓子。自風と同じ牌なら両方を加算 |
| `rinshan_kaihou` | 1 | 1 | 槓成立後の嶺上牌でツモ和了する |
| `chankan` | 1 | 1 | 加槓、または `rules.ankan_chankan` が許す暗槓宣言牌をロンする |
| `haitei` | 1 | 1 | live wall最後の牌でツモ和了する。嶺上開花と重複しない |
| `houtei` | 1 | 1 | live wall最後の自摸後の打牌でロン和了する |
| `sanshoku_doujun` | 2 | 1 | 3色で同じ数字の順子を持つ |
| `ikkitsuukan` | 2 | 1 | 同色で123・456・789の順子を持つ |
| `chanta` | 2 | 1 | 全面子と雀頭が么九牌を含み、順子を1組以上、字牌を1枚以上含む |
| `chiitoitsu` | 2 | - | 異なる7組の対子を持つ。4枚の同一牌を2対子として数えない |
| `toitoi` | 2 | 2 | 4面子が全て刻子または槓子 |
| `sanankou` | 2 | 2 | 暗刻または暗槓を3組持つ。ロンで完成した刻子は明刻として扱う |
| `honroutou` | 2 | 2 | 全牌が么九牌である |
| `sanshoku_doukou` | 2 | 2 | 3色で同じ数字の刻子または槓子を持つ |
| `sankantsu` | 2 | 2 | 槓子を3組持つ |
| `shousangen` | 2 | 2 | 三元牌の刻子・槓子を2組、残りの三元牌を雀頭にする。三元牌の役牌も加算 |
| `honitsu` | 3 | 2 | 1色の数牌と字牌だけで構成する |
| `junchan` | 3 | 2 | 全面子と雀頭が老頭牌を含み、順子を1組以上持ち、字牌を含まない |
| `ryanpeikou` | 3 | - | 一盃口形を2組持つ。`iipeikou`と重複しない |
| `chinitsu` | 6 | 5 | 1色の数牌だけで構成する |

同一役を構成する独立した組が複数あっても、役IDの加算回数は表の役ごとに1回とする。ただし `seat_wind` と `round_wind` は別役である。`honitsu` と `chinitsu` は排他的であり、同じ和了に両方を加算してはならない（MUST）。`ryanpeikou` と `iipeikou`、`double_riichi` と `riichi` も同様に排他的である。wireへ出力する通常役IDは重複してはならず、各IDのvalueは本表の門前・副露欄に固定された値でなければならない（MUST）。

## 4. 役満

| Yaku ID | 成立条件 |
|---|---|
| `kokushi_musou` | 13種の么九牌を全て1枚以上持ち、そのうち1種を対子にする |
| `suuankou` | 暗刻・暗槓を4組持つ。双碰待ちのロンでは成立しない |
| `daisangen` | 三元牌3種を全て刻子または槓子にする |
| `shousuushii` | 風牌3種を刻子・槓子、残り1種を雀頭にする |
| `daisuushii` | 風牌4種を全て刻子または槓子にする |
| `tsuuiisou` | 全牌が字牌である |
| `chinroutou` | 全牌が老頭牌である |
| `ryuuiisou` | 全牌が2s・3s・4s・6s・8s・發のいずれかである |
| `chuuren_poutou` | 門前で同一色の1112345678999に同色任意1牌を加えた形 |
| `suukantsu` | 槓子を4組持つ |
| `tenhou` | 親が第一自摸で和了し、それ以前に鳴きがない |
| `chiihou` | 子が第一自摸で和了し、それ以前に鳴きがない |

`rules.double_yakuman` のconditionは次を意味する。

- `kokushi_13_wait`: 国士無双を13面待ちで和了する
- `suuankou_tanki`: 四暗刻を単騎待ちで和了する
- `junsei_chuuren`: 純正九蓮宝燈の9面待ちで和了する
- `daisuushii`: 大四喜をダブル役満とする

複数役満は `yakus[].value` を合計する。役満と通常役・ドラbonusは複合しない。役満和了の `fu` は常に0とする（MUST）。

## 5. ドラbonus

| Bonus ID | 計算 |
|---|---|
| `dora` | 全表ドラ表示牌 `dora_markers` の次牌と同じ牌種の枚数 |
| `uradora` | 有効なリーチ和了時に公開する裏ドラ表示牌 `ura_dora_markers` の次牌と同じ牌種の枚数 |
| `akadora` | 手牌・和了牌・副露に含まれる赤五の枚数 |

ドラの次牌は、数牌では9の次を1、風牌では東→南→西→北→東、三元牌では白→發→中→白とする。赤五と通常五はドラ牌種の枚数計算では同じ五として数え、赤五なら `dora` と `akadora` の両方を加算できる。

`dora_markers` は表ドラ表示牌の、`ura_dora_markers` は裏ドラ表示牌の、公開順に並んだ物理的な表示牌列である。`ura_dora_markers` は和了者に有効な `reach_accepted` がある和了でだけ公開し、その他の和了では空配列でなければならない（MUST）。複数和了では同じ局の裏ドラ表示牌列を各該当 `win` に記録する。裏ドラ表示牌は、和了が確定するまで `play` viewへ送信してはならない（MUST NOT）。

ホストは和了結果を確定する前に、各表示牌の次牌を和了手牌（手牌・和了牌・副露）と照合し、`dora`、`uradora` および `akadora` のbonus値を再計算しなければならない（MUST）。`win.ura_dora_markers` から再計算した `uradora` の値と `bonuses` の値が一致しない結果を送信してはならず（MUST NOT）、受信者は不一致を検出した場合に結果を不正として扱わなければならない（MUST）。

## 6. 符

### 6.1 基本

通常形は20符から開始し、次を加算する。

- 門前ロン: 10符
- ツモ: 2符。ただし平和ツモは加算しない
- 三元牌の雀頭: 2符
- 自風の雀頭: 2符
- 場風の雀頭: 2符。自風と同じなら合計4符
- 嵌張・辺張・単騎待ち: 2符

### 6.2 面子符

| 面子 | 中張牌 | 么九牌 |
|---|---:|---:|
| 明刻 | 2 | 4 |
| 暗刻 | 4 | 8 |
| 明槓 | 8 | 16 |
| 暗槓 | 16 | 32 |

ロン牌で完成した刻子は明刻として計算する。順子に面子符はない。

### 6.3 丸めと例外

合計符は10符単位へ切り上げる。七対子は常に25符で切り上げない。平和ツモは20符とする。副露ロンで計算結果が20符なら30符とする。役満和了は4節の規定により0符とする。

## 7. 基本点と支払い

### 7.1 基本点

役満でない手の `han` は通常役とbonusの合計であり、1以上でなければならない。uncapped basic points は `fu × 2^(han + 2)` である。`basic_points` は、次の分岐を上から順に評価し、最初に該当した値を採用しなければならない（MUST）。この順序により、満貫以上の上限と切り上げ満貫の判定を一意にする。

1. `han >= 13` かつ `rules.kazoe_yakuman == yakuman` なら8,000（数え役満）。
2. `han >= 13` かつ `rules.kazoe_yakuman == sanbaiman` なら6,000（三倍満）。
3. `han >= 11` なら6,000（三倍満）。
4. `han >= 8` なら4,000（倍満）。
5. `han >= 6` なら3,000（跳満）。
6. `han >= 5`、または `han == 4` かつ `fu >= 40`、または `han == 3` かつ `fu >= 70` なら2,000（満貫）。
7. `rules.kiriage_mangan == true` かつ、`han == 4` かつ `fu >= 30`、または `han == 3` かつ `fu >= 60` なら2,000（切り上げ満貫）。
8. 上記のいずれにも該当しない場合は uncapped basic points とする。

役満の `basic_points` は `8,000 × yakuman value合計` とし、上記の通常手の分岐を適用しない。役満と通常役・ドラbonusを同時に数えてはならない。

### 7.2 支払い

`ceil100(x)` を、x以上の最小の100の倍数とする。各支払者の基本支払額を個別に `ceil100` してから合計しなければならない（MUST）。

- 子のロン: `basic × 4` を放銃者が支払う
- 親のロン: `basic × 6` を放銃者が支払う
- 子のツモ: 親が `basic × 2`、他の子2人がそれぞれ `basic` を支払う
- 親のツモ: 他3人がそれぞれ `basic × 2` を支払う

`hand_points` は本場・供託を除いた、個別丸め後の全支払額の合計である。本場、供託、複数ロンおよび責任払いは [YRC 0003] 第7.2節のruleに従う。本場加算は基本支払いを丸めた後に適用する。

### 7.3 責任払い

`rules.pao.yakus` に含まれる役だけが責任払いの対象となる。責任払いの成立seatは役ごとに次で決定し、成立後はその局の和了まで保持する（MUST）。

- `daisangen`: 既に異なる三元牌の刻子・槓子を2組持つプレイヤーが、3組目の三元牌を `pon` または `daiminkan` したとき、その鳴きの `target`（捨て牌を供給したseat）を `daisangen` の責任seatとする。
- `daisuushii`: 既に異なる風牌の刻子・槓子を3組持つプレイヤーが、4組目の風牌を `pon` または `daiminkan` したとき、その鳴きの `target` を `daisuushii` の責任seatとする。

`ankan`、`kakan` または自摸で成立した刻子・槓子は責任払いの成立契機にならない。対象役が設定されていない場合、上記の鳴きがあっても責任払いを適用してはならない（MUST）。複数の対象役が同時に成立する場合、役ごとの責任seatを独立に保持し、単一seatへ暗黙に統合してはならない。責任seatと支払方式の対応、およびこの役ごとの対応を表すwire memberは [YRC 0003] 第7.2節で定義しなければならない（MUST）。

## 8. 局精算との関係

ホストは各winについて、本書で計算した `fu`、`han`、`yakus`、`bonuses`、`hand_points` を [YRC 0003] の `end_kyoku.result.wins[]` へ格納しなければならない（MUST）。

`deltas` は基本支払いへ本場、供託、複数ロン配分および責任払いを適用した結果である。`scores` は直前点数との加算で検証可能でなければならない（MUST）。

### 8.1 通常流局のノーテン精算

`result.type == "ryukyoku"` かつ `result.reason == "fanpai"` の場合、`result.tenpai` のtrueの個数を `k`、`rules.noten_payment.total_points` を `P` とする。`P` は600の倍数でなければならない（MUST）。各seatの純差額は次の表に従い、合計は0でなければならない（MUST）。

| `k` | 聴牌者 | ノーテン者 |
|---:|---:|---:|
| 0または4 | 0 | 0 |
| 1 | 各 `+P` | 各 `-P/3` |
| 2 | 各 `+P/2` | 各 `-P/2` |
| 3 | 各 `+P/3` | `-P` |

`tenpai == null` の途中流局およびチョンボでは、このノーテン精算を適用してはならない（MUST）。`end_kyoku.deltas` は表の純差額と一致しなければならず、0人または4人聴牌では点数を移動しない。個別seat間の支払明細を要求してはならない（MUST）。

## 9. 非対応ルール

初期 `riichi-4p` profileは、流し満貫、人和、オープンリーチ、切り上げ以外のローカル満貫、花牌および焼き鳥を定義しない。これらを使用する場合、[YRC 0003] のcapability、namespaced rule key、Yaku/Result registry登録を全て満たさなければならない（MUST）。

## 10. Security Considerations

点数・役判定の不一致は対局結果を改ざんする。ホストは和了actionを受理する前に、手牌、副露、和了牌およびruleから役、符、表示牌bonus、責任seatおよび点数を再計算しなければならない（MUST）。通常流局では `tenpai` の配分も再計算しなければならない。playerが申告する役・符・点数を権威として使用してはならない（MUST NOT）。

## 11. Registry Considerations

Yaku ID、Bonus ID、Double Yakuman Conditionの登録は [YRC 0003] 第19節に従う。新しい役は門前・副露飜数、成立条件、既存役との重複、符・役満との関係および最低2個のtest vectorを指定しなければならない（MUST）。

## 12. References

### 12.1 Normative References

- [BCP 14] Bradner, S. and B. Leiba, BCP 14, RFC 2119 and RFC 8174.  
  https://www.rfc-editor.org/info/bcp14
- [YRC 0003] YAMAI Project, “YAMAI Protocol Version 1 (1.0-draft.4)”.

## Appendix A. 計算例

### A.1 子の30符3飜ロン

`basic = 30 × 2^5 = 960`、子ロンは `960 × 4 = 3,840` を100点単位へ切り上げ、3,900点とする。

### A.2 親の40符3飜ロン

`basic = 40 × 2^5 = 1,280`、親ロンは `1,280 × 6 = 7,680` を切り上げ、7,700点とする。

### A.3 子の30符2飜ツモ

`basic = 30 × 2^4 = 480`。親は1,000点、子2人は各500点を支払い、`hand_points = 2,000` とする。

### A.4 切り上げ満貫の分岐

4飜30符では uncapped basic points は `30 × 2^6 = 1,920` である。`kiriage_mangan == false` なら子ロンは `ceil100(1,920 × 4) = 7,700` 点、`kiriage_mangan == true` ならbasic pointsを2,000として子ロンは8,000点とする。

### A.5 ノーテン罰符

`rules.noten_payment.total_points = 3,000`、聴牌者が2人の場合、各聴牌seatの純差額は `+1,500`、各不聴seatは `-1,500` である。聴牌者が1人の場合はそのseatが `+3,000`、他3seatが各 `-1,000` となる。
