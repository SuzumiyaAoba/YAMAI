# 変更履歴

YAMAI の仕様と成果物の変更、および版間の互換性を記録する。各版は Draft であり、現行成果物は [release manifest](release-manifest.json) に従って取得する。

## draft.9への未公開の追補 — 2026-09-21（ACK結果と採点境界の再点検）

- 新規replayのwelcome.scoresをplayと同じ開始点の規則で検査する。進行中・終了済みのtargetでも現在点数や最終点数を開始点として受理せず、welcomeの時点で拒否する。
- passed・noneのdefaulted・supersededのACKと後続結果を照合し、自分の鳴き・和了の発生や、noneを選んだseatを含む三家和を拒否する。supersededには優先順位の高い採用結果か、明示horaによる三家和を要求し、鳴き・頭ハネの順位も検査する。派生eventを挟んで対応を保持し、完結したcaptureで結果の欠落を拒否する。
- 未終端requestのstale取消しはchombo policyに限り、その後にpenaltyを要求する。取消した候補を通常の打牌へ適用する不正と、見送りACKだけでpenaltyへ進む不正を拒否し、既存終端への後着staleとは区別する。V26のpenalty例の次局も、同局・同本場のやり直しへ揃えた。
- 三家和にもロン窓の条件を適用し、槍槓禁止または中張牌の暗槓、同じ打牌のreach_accepted後の三家和を拒否する。
- 採点fixtureの槍槓では宣言者の4枚と表裏表示牌の重複を拒否し、宣言時の山残量と成立槓数も検査する。複数ロンはpending_kanとlast_tileを共有し、一部の和了者だけ槍槓・河底として評価される状態を拒否する。
- V330〜V341、採点正例2件・負例N30〜N36と回帰検査を追加した。公式ベクトル341件、採点正例78件・負例36件へ更新し、仕様本文・検証ガイド・registry・manifestを同期した。profile hashは `sha256:a96f0f0868200838494573ac479f182c0a7a94c91e10ee1263d2108091dad934`。

既存の開始点・競合解決・取消し・槍槓・物理在庫の規則に検査実装を一致させる未公開追補である。wire memberと合法な対局の点数式を変えず、Protocol Version `1.0-draft.9`とprofile revision `1.0-draft.7`を維持する。新hashを含む成果物一式へ更新し、旧hashと混在させない。

## draft.9への未公開の追補 — 2026-09-21（公開情報と成果物hashの再点検）

- snapshotの鳴かれた河牌と副露を、捨てたseat・鳴いたseat・赤牌を区別したpaiの多重集合で照合する。同じ表記の別々の牌を複数回チーする正例を維持し、1個の河要素を複数の副露へ対応させる状態と、同じ副露に対応する河履歴の重複を拒否する。
- リーチ宣言中・成立済みのsnapshotは門前を維持し、各seatの宣言牌を正確に1個だけ保持する。副露手へのリーチ成立、複数の宣言牌から第一巡・一発資格を復元する不正状態を拒否する。
- 非公開手牌を持つseatの槓宣言も公開在庫へ含める。暗槓の4枚、加槓で追加した1枚を数え、既に可視の手牌・元のponは重複して数えない。live eventとsnapshotの双方で4枚上限と赤五の内訳を検査する。
- 和了結果の公開値から基本点・hand_points・本場・供託・責任払いを再計算し、各winのdeltasを照合する。合計点数を保存しながら支払額や支払seatを変更する結果を拒否する。全採点正例の和了について金額検査を照合した。
- 和了時の公開牌も在庫へ反映し、複数リーチ和了者の裏表示牌列の一致と、表示位置を重複計上しないことを検査する。
- profile hash用のwire正規化はJSON内のidentity fieldの位置で対象を選ぶ。同じhash文字列を持つ無関係な注釈、member名、配列要素と、置換対象以外のescape・空白を保存する。以前は値の一致だけで無関係なtokenをゼロ化できた。
- V320〜V329の正負ベクトルと回帰検査を追加し、manifestの件数制約を329へ更新した。RFC 8785の著者表記も一次資料に一致させた。profile hashは `sha256:4725579ef92f6aaff87413a57b092632a826f90e2895002f3566ffb16ed123c8`。

既存の物理牌保存・門前リーチ・採点式・hash対象範囲へ検査実装を一致させる未公開追補である。wire memberや合法な対局の規則・点数式は変更せず、Protocol Version `1.0-draft.9`とprofile revision `1.0-draft.7`を維持する。取得する成果物一式と交渉hashを同時に更新し、旧hashの成果物と混在させない。

## draft.9への未公開の追補 — 2026-09-21（仕様全体の再レビュー）

- snapshotの点数と供託の保存を全phaseで明示した。交渉した開始点と供託棒の値から総額を確認し、不正snapshotは状態を置換する前に拒否する。本文、成果物検査、Receiver、EventStateの復元経路を揃えた。
- host messageのdirection/kind検査を重複seqのbyte比較とsnapshot floor判定より先に実施する。不正なkindを`sequence_conflict`へ誤分類したり、置換済みの古い番号を理由に無視したりしない。
- 可視手牌に直前の自摸牌と同じ表記の牌が1枚しかない場合、その牌の打牌は`tsumogiri:true`を必須とした。通常五と赤五を区別し、同一表記の牌が複数ある合法な手出しは維持する。
- snapshotの`selection.source`と終端ACKのstatusの対応、単独decisionの`superseded`禁止、取消しACKの候補ID、明示選択の終端後に新しい`stale`を生成しない規則を受信検査へ反映した。
- 期限前のdefaultは`invalid_action_policy == default`に限り、`reject`/`chombo`の自動選択は期限時刻の計時値を必須とする。`default` policyの`rejected` ACKと、期限到達後の`rejected` ACKを拒否する。不正actionの即時defaultは選択の固定を指し、反応groupの終端ACKは全memberの選択確定後とする。
- event/rulesの節参照、`pao`の遷移表、単独decision後のresolving、frameとJSONのエラー優先表を補正した。MJAI実装比較表は、RiichiLabのIDなしresponse互換と矛盾する記述を訂正した。
- V305〜V319の正負ベクトルと境界条件の回帰検査を追加し、manifestの件数制約を319へ更新した。profile hashは `sha256:7314cafde606d58ff635f141c061889cb51117ff9713c03abde2a7512d51d86b`。

既存の規範要件と派生成果物の整合を修正する未公開追補であり、Protocol Version `1.0-draft.9`とprofile revision `1.0-draft.7`は維持する。成果物一式を新hashへ更新し、旧hashと混在させない。

## draft.9への未公開の追補 — 2026-09-21（仕様全体レビュー）

- 加槓後のsnapshotで、元のponの位置・target・鳴き牌paiを保持し、手牌から消費した2枚と追加牌をconsumedへ記録することを明確化した。kakan eventの追加牌でpaiを上書きしていた参照実装を修正し、通常五への赤五追加・赤五を鳴いた後の通常五追加の双方を検査する。
- 副露列の途中にあるponの加槓も、嶺上・延期ドラsnapshotから復元できるようにした。最初の副露成立順を最新の槓成立順とみなしていた3層の検査を修正し、延期ドラとrinshan、海底flagの整合も確認する。
- 延長局数を保持した場風循環をsnapshotとnext_kyokuの復元へ反映した。東南戦の延長9局目・東風戦の延長13局目以降で東場へ戻れる一方、延長1局目で西4局へ進むなど、配牌回数より速い座標進行は拒否する。既存の局座標検査を循環と連荘に対応する必要条件へ訂正した。
- 拡張member名、版・profile・hash・capability名、resume tokenのSchema正規表現を文字列全体への一致へ揃えた。末尾改行を受理する `$` 終端を除き、本文で禁止されている文字の混入をSchemaでも拒否する。
- 採点章の統合前の節番号12箇所とregistryの参照先、チョンボの全seat差額合計（0）、和了・三家和・流局・鳴きの遷移図を訂正した。公開サービスの認証・認可を別profileで定義する既存の適用範囲を、規範本文§18.6にも明記した。
- V297〜V304の正負ベクトル、再接続後の継続と境界条件の回帰検査を追加し、manifestの件数制約を304へ更新した。profile hashは `sha256:278ea1cff888a0370d6f8fc32213f89b45e000a7a7efe2449f17c9c20b623799`。

本追補は未公開draftの本文内の矛盾と派生成果物の不一致を修正する。新しいwire member、役または点数式は追加しないため、Protocol Versionとprofile revisionは維持する。実装は新hashを含む成果物一式へ更新し、旧hashとの混在を拒否する。認証機能の計画とレビュー記録は非規範資料であり、認証実装や独立相互運用試験の完了を意味しない。

## draft.9への未公開の追補 — 2026-09-21

- snapshotの河要素 `reach` を規範本文で定義した。その打牌がリーチ宣言の複合打牌である場合だけtrueとし、宣言の受理・破棄や牌の鳴かれで取り消さない。これまではmember列挙のみで意味が未定義だった。
- 宣言中リーチ（`declared`）が生存できる取引境界を本文と3層の検査で固定した。宣言seatを `turn.actor`、その宣言打牌を `last_event` とする `awaiting_responses`/`resolving` に限る。これまでは原因を `dahai` に限定していなかったため、槓宣言や自摸を原因とするsnapshotが古い `declared` を保持したまま受理され、復元後の全ての自摸が「受理前の自摸」として拒否されsessionを致命化させ得た。
- `reach_status` のflagと公開状態の照合を追加した。`none` では `double`・`ippatsu` がfalseかつ河に `reach` マークを持たない（宣言の破棄は必ず局を終了するため）。`declared` では宣言牌が河末尾で `reach:true` を保持し `ippatsu` はfalse、`double` は宣言時の第一巡条件（そのseatの初打牌かつ全卓副露なし）と一致する。`accepted` では宣言牌の河マークを必須とし、`double` は「宣言牌が河先頭かつ現在副露なし」、`ippatsu` は「宣言牌が河末尾かつ現在副露なし」と双方向で一致する。
- snapshotの原因 event と公開状態の照合を追加した。`turn.actor` は原因の行為者（`start_kyoku` では `oya`）と一致し、`start_kyoku` 原因では局座標・配牌・表ドラ・点数が原因と一致し河・副露・pao・槓数・フリテンが初期値、`dahai` 原因では河末尾が `{pai, tsumogiri}` 一致・`called_by` なし・`reach` が宣言中と一致、`tsumo` 原因では可視手牌に自摸牌を保持、槓宣言原因では `consumed` が同種で嶺・槓容量が残り、加槓は `none` 宣言状態・対応pon実在・`pai` 同種を要求し、可視手牌は宣言牌を保持する。
- 公式ベクトルV296の加槓宣言snapshotを修正した。可視手牌が宣言牌 `3m` を持たず `9m` で帳尻を合わせていたため、宣言牌を手内に移した（牌の多重集合は保存）。profile hashを `sha256:f30f7cfa…` へ更新し、manifest・registry・release manifest・本文例示を同期した。
- 副露の形状と河の `called_by` との双方向対応を3層の検査で固定した。ankanは同種4枚でtargetを持たず、chiは連続順子で直前seatの打牌のみ、それ以外は同種牌の構成を要求する。鳴きで成立した副露はtargetの河に `called_by == actor` の対応牌を持ち、`called_by` を持つ河牌は鳴き手の副露へ必ず対応する。本文 §13.3 にもこの対応を明記した。
- snapshotの自己フリテンflagを公開状態と照合する検査を追加した。`riichi_furiten` はそのseatの受理済みリーチを前提とし、`temporary_furiten` は本人の自摸後には残存しない。可視手牌が5枚目の牌種や交渉上限超の赤五を持つsnapshotは成果物検査でも拒否する。
- 復元経路の物理不変条件を他層へ揃えた。`kyoku.kyotaku` とtop-levelの一致、非公開枚数式（`13 - 3×副露数 + 保持自摸`）、`kan_counts` の成立槓との一致と合計4以下、可視牌・副露・未鳴河・山・王牌の136枚保存、ドラ表示枚数（`1 + 槓数 - 延期ドラ`）を復元時にも検査する。
- 牌の多重集合検査（4枚上限・赤五分割）を復元の状態代入より前へ移した。これまでは代入後に検査していたため、多重集合違反だけで失敗するsnapshotが内部状態を部分更新したまま残り、「不正なsnapshotを部分適用しない」規則に反し得た。回帰テストで失敗時の非破壊を固定した。
- snapshotの副露 `consumed` 枚数（chi/pon=2、daiminkan/kakan=3、ankan=4）を3層の検査で固定した。同種判定のみでは枚数違反を通し、山枚数の帳尻合わせで物理保存則も回避できた。局座標の到達可能性も検査する。`oya == kyoku - 1`（絶対座席順）、`extension_round <= rules.extension.max_extra_rounds`、延長局は規定最終場風を越えた場風に限る（`extension_round > 0` ⟺ 場風がlast_wind以降）。`next_kyoku` にも同じ座標検査と `kyotaku` 一致を適用し、`kyoku`/`next_kyoku` の存在を `game_phase` と一致させる。終局snapshotの `final_rankings` はscoresからの順位導出と一致させる。
- 回帰テストを追加した。宣言窓の正例snapshotが受理・復元されること、原因を槓宣言へすり替えた幻影リーチsnapshotが拒否されることを受信側で固定した。受理済みリーチは「宣言者の次の打牌原因（河末尾は無印・一発消費済み）」と「他家決定原因（宣言牌が河末尾・一発存続）」の2境界で固定し、河マーク欠落・flag不一致・供託不一致・原因との河末尾不一致を負例とした。
- rules Schemaの拡張rule keyパターンから、ownerにdot/hyphenを許し64文字上限を持たない旧来の緩い形式を除去した。namespaced rule keyは第14節のfield形式 `x_<owner>_<name>`（ownerは英数字のみ、nameは英数字で始まり英数字・underscore・dot・hyphenを使用でき、全体64文字以下）に従うことを本文へ明記し、Schemaを意味検査の判定へ一致させた。緩い形式でしか受理されないkeyは既存成果物に存在せず、意味検査は従来どおり厳格な形式だけを受理するため、wire上の意味は変わらない。profile hashを `sha256:03f552d7…` へ更新し、manifest・registry・release manifest・本文例示を同期した。
- 形式モデル成果物の記述から旧版への参照を除去した。Quintモデル説明の「draft.8」およびextendedモデルの有限tag `Draft6` を現行版の表記へ揃え、検証ガイドへ未公開追補のV289〜V296が検査する範囲を追記した。モデルの状態・遷移・不変条件は変更していない。

## draft.9への未公開の追補 — 2026-09-20（その3）

- `end_kyoku` の参照状態検査を強化した。`hora` は原因 event の決定窓（自摸和了は `awaiting_action`、それ以外は `awaiting_responses`）でのみ受理し、`reach_accepted` 成立済みの宣言打牌へのロンを拒否する。`ankan_chankan == "never"` では暗槓への槍槓和了を、`"kokushi_only"` では国士無双を主張しない槍槓和了を拒否する。`wins` のseat昇順・一意性、top-level `deltas` と全win差額の要素一致、受理リーチ和了者の裏ドラ表示牌列（未受理では空、受理では公開済み表ドラと同枚数）を検査する。
- `fanpai` の `deltas` を `noten_payment.total_points` と聴牌者数から求まる正規分配と照合し、可視手牌の聴牌判定と `tenpai` 宣言の一致を検査する。`penalty` の `payments` は `chombo.penalty_points` の100点単位等分と余りの最小seat加算へ完全一致させ、`deltas` との一致と宛先seatの一意も要求する。
- 成果物側の意味検査を同じ規則へ揃えた。`fanpai`/`penalty`/`hora` のmessage級検査で聴牌者数別配分・チョンボ配分・宛先重複・役ID昇順を拒否し、snapshotの公開責任対応判定を交渉済み `rules.pao.yakus` から導出するよう修正した（これまでは登録済み2役を固定で要求し、対象役を限定したrules下の正当なsnapshotを誤拒否し得た）。receiver・multi-session・ledger traceのwire snapshot検査にもwelcomeのrulesを束縛し、交渉なしの単体snapshotは従来どおり登録済み全役で判定する。
- 公式ベクトルV26のチョンボ配分を `q=floor(P/300)×100`・余りを最小seatへ加算する規範式と採点fixtureへ一致させ（`[2700,2700,2600]`→`[2800,2600,2600]`）、旧配分と宛先重複を負例として固定した。V09へ聴牌者数と不整合な配分の負例を追加した。
- profile hashを `sha256:bcedbea5…` へ更新し、manifest・registry・release manifest・本文例示を同期した。回帰テストでノーテン精算・チョンボ配分・和了集計・決定窓・受理済みリーチ打牌へのロン・槍槓制限・裏ドラ条件を固定した。

## draft.9への未公開の追補 — 2026-09-20

- 再開replayの `original_seq` 禁止規則が参照する節番号を、存在しない `11.2` から replay mode を規定する `11` へ修正した。
- snapshotの `turn.phase == "resolving"` を、decision groupのlinearization point記録から全memberの終端ACKと結果event列のtransaction確定までの卓の状態と定義した。この期間にrequestが未終端のplay seat向けには、終端ACKが対応するrequestを欠くため、transactionの確定までsnapshotを生成してはならないことを明記した。
- replay snapshotの `state.original_seq` をmember名として明示した。wire・Schema・vectorの意味は変更していない。

## draft.9への未公開の追補 — 2026-09-20（その2）

- snapshot復元時に `turn.phase == "resolving"` を原因 event の論理 phase へ写像し、採用 call や手番動作などlinearization済みの結果 event 列を復元後も適用できるよう修正した。これまでは `awaiting_*` 以外の phase を受理せず、合法な観戦・replay用snapshot復元後の全eventが `invalid_message` になっていた。
- snapshot受信側の検査を成果物検査と同等に強化した。phaseと `last_event` の原因関係（`awaiting_draw`↔`start_kyoku`、`awaiting_action`↔`tsumo`、`awaiting_responses`/`resolving`↔打牌・槓宣言）、必要なpending requestの保有、`kuikae_forbidden` 空、副露・河・槓数・136枚保存・ドラ表示・順位の整合、selectionの合法性と共有time bankを検査する。取引途中を示す内部 event（call・reach_accepted・dora・pao等）を原因とするsnapshotは拒否する。
- snapshotの `pao` は公開副露履歴から導かれる責任対応を重複も欠落もなく保持する完全一致へ統一し、成果物検査と参照実装の差異を解消した。本文の記述も明確化した。
- 公開状態から導出できるsnapshot不変条件を追加した。宣言中リーチは反応窓のみ、延期槓ドラは嶺上決定窓のみで最新槓副露と一致、`rinshan` は連続槓宣言窓を含む決定期間のみ、`haitei` は山尽き時のみ、受理リーチ数は供託以下、`first_turn_eligible` は河・副露の有無と一致する。
- 採点fixture投影で `ankan_declared` の `pai` 欠落を `invalid_message`、赤五未正規化を `invalid_context` として扱い、未知型の例外送出を防いだ。内部モデルの `pending_dora` からwire Schema外形の `actor` memberを除去した。
- 公式ベクトルV289〜V296（resolving継続、取引途中snapshot拒否、pao完全一致、槓宣言原因、複合打牌フロー、延期ドラ・連続槓窓）と回帰テストを追加した。profile hashを `sha256:4ec5bc8a…` へ更新し、manifest・registry・release manifest・本文例示を同期した。

## 1.0-draft.9 / profile 1.0-draft.7 — 2026-09-17（未公開）

- 途中流局のtenpaiをnull、点差を全seatゼロとし、直前の点数と供託を維持することを規定した。Schemaと意味検査を揃え、illegal_actionはpenaltyだけに制限した。
- accepted/defaulted ACKを元の候補へ対応付け、別の合法手や複合候補内の異なる打牌への置換を拒否する。槓宣言、和了・九種九牌、所定位置のdora・reach_accepted・paoも検査する。
- snapshotによる確定済みselectionの変更、OPENへの巻き戻し、終端requestの復活を拒否する。復元時にも元のdeadlineとbank消費式を適用し、不正なsnapshotを部分適用しない。
- 公式ベクトルV282〜V288と回帰テストを追加し、Protocol Version、profile revision/hash、Schema ID、registry、release manifestを更新した。

`1.0-draft.8` / profile `1.0-draft.6` とは非互換である。実装は成果物一式を更新し、WebSocket subprotocolに`yamai.1.draft9`を使用する。

## 1.0-draft.8 / profile 1.0-draft.6 — 2026-09-16（未公開）

- 履歴中のsnapshotを連続したseqで再送できるよう、欠落範囲を飛び越える復旧用snapshotと受信条件を分離した。古いsnapshotで今回の再送終端を覆う必要はなく、終端まで回復待ちを維持する。新規生成の理由はホスト側で制限・監査する。
- IDのSchemaを文字列全体へ一致させ、末尾の改行などを候補外actionやchomboとして扱う前にSchema違反として拒否する。
- 終局前に受け付けた後着actionへのstale通知は、end_game後にも一度だけ記録できることを明記した。終局後の新規actionは破棄し、次sessionの開始前に保留通知をcommitする。
- 参照受信実装の拡張event判定を仕様の`x-<owner>-<name>`へ合わせ、交渉済み拡張を含むsession全体を検査する。
- 有効だが未対応のmode/viewはunsupported_viewへ統一し、profile非対応・構造違反との検査順を定義した。
- hello.profilesは本文どおり1件以上とし、既存の正例を修正した。公式ベクトルV276〜V281、回帰テスト、独立Schema負例検査、終了後の保留通知を扱う有限モデルを追加した。
- Protocol Version、profile成果物のrevision/hash、Schema ID、registry、release manifestを一式更新した。profileの役・符・点数式は変更していない。

`1.0-draft.7` / profile `1.0-draft.5` とは非互換である。実装は成果物一式を更新し、WebSocket subprotocolに`yamai.1.draft8`を使用する。受信側は連続したsnapshotで回復待ちを早期終了せず、送信側は新規snapshotの生成理由と終局後の通知範囲を守る必要がある。

## 1.0-draft.7 / profile 1.0-draft.5 — 2026-09-16（未公開）

- 再開時の `start_game.scores` は開始点、`welcome.scores` は同期対象時点の点数とし、一致比較を新規sessionだけに限定した。
- 槍槓できない暗槓も、他家3seat全員への反応要求・選択・終端ACKを経て成立することを状態前後条件表へ統一した。
- 保持済みseqのbyte衝突判定をpayload Schema検査より先に行うよう、エラー優先順の表と本文を統一した。
- 局間snapshotの `next_kyoku` に `type` を要求していた参照実装を修正し、復元後の次局開始・自摸、座標不一致、早すぎる終局を回帰検査する。
- 開始点・本場・供託・流局・penaltyを含むゲーム全体の点数上界を定義した。Schemaの個別上限と意味検査の組合せ条件を満たさないrulesを、ゲーム開始前に拒否する。
- 公式ベクトルV271〜V275、採点負例N29と回帰テストを追加し、Schema、registry、profile hash、release manifestおよび現行版の参照先を更新した。

`1.0-draft.6` / profile `1.0-draft.4` とは非互換である。実装はProtocol Version、profile revision/hash、Schema・registry・vectorを一式で更新する。WebSocket subprotocolは `yamai.1.draft7` を使用する。通常の点数設定とメッセージ構造は維持するが、整数範囲を保証できない点数ルールは受理しない。

## draft.6への未公開の追補 — 2026-09-16

- draft.6の検査実装・公式ベクトル拡充と仕様レビューの修正を統合した。
- 初局の開始条件、複合リーチの宣言、槍槓判定が不要な槓の成立条件を状態前後条件表に反映した。
- 採点ID・期待値に依存しない計算、入力変更、sessionごとのseq、再開時の状態保持を回帰テストで検査する。
- 独立したsession ledgerと取消し後の状態を扱う有限モデルを追加し、5モデルをNixの検査対象にした。

## 1.0-draft.6 / profile 1.0-draft.4 — 2026-09-12

- プロトコルと採点規則を YRC 0003 にまとめ、唯一の規範本文とした。
- request を `OPEN`、`SELECTED`、`TERMINAL` に分離し、全選択の固定、競合判定、終端 ACK、結果 event の順序を定義した。decision group は他家3人を含み、`all_selected_or_deadline` で閉じる。
- deadline ちょうどは timeout を優先し、期限前の選択は後着 ACK でも有効とした。不正 action の再試行、選択後の再送、後着 action、chombo による取消しと time bank の扱いを固定した。
- 牌山、嶺上補充、合法手、フリテン、リーチ後の暗槓、喰い替え、連続槓の順序を明文化した。リーチ打牌が鳴かれてもリーチは成立し、一発を失う。暗槓の4枚は全 view で公開する。
- 延長上限、親のアガリ止め、penalty での同局やり直し、本場・供託の繰越を定義した。`next` は次局の `bakaze`・`kyoku` を含み、`end_game` の場合も `kyotaku` を必須とする。
- snapshot に局外の供託・次局・終局順位・time bank、鳴かれた捨て牌、槓ドラ保留、選択済み行動と残り期限を追加した。公開 view と他家の自摸番における pending request の条件を修正した。
- `welcome` に有効 capability 集合と `replay_through_seq` を追加した。観戦の途中参加、初回 snapshot、全 application message を含む replay の seq、token の一回使用・失効を定義した。
- profile revision と Protocol Version の対応表、選択前の join Schema、交渉の拒否順序、session 内の拡張 Schema 合成を定義した。新規 join の座席割当、resume の座席固定、replay target を明確化した。
- stateful trace で wire ledger、transaction、timeout、全 view の再帰的な情報投影を検査する。公式ベクトルには合法候補全体、牌136枚の保存、局進行、資源上限の検査を含めた。
- 採点入力から手牌の全分解、役・符・点数・支払いを再計算する形式を定義した。責任払いは該当役満の点数成分に適用し、本場の端数配分と供託の別会計を固定した。
- Schema、registry、公式ベクトル、検査実装、要求・配送・再開を扱う4つの有限形式モデルを同じ版として管理する。

`1.0-draft.5` / profile `1.0-draft.3` とは非互換である。実装は Protocol Version、profile revision、Schema、registry、ベクトル、hash を一緒に更新する。

## 1.0-draft.5 / profile 1.0-draft.3 — 2026-08-30

- Protocol Core の正準状態、message の適用前後条件、不変な wire ledger、transaction 境界を定義した。
- request の同時到着、timeout、線形化、ACK と採用 event の原子的な処理を明文化した。
- JSON Lines の frame 境界、mode 別の resume、session の開始・終了、decision group の期限、再接続中の時計進行を明確化した。
- 座席割当、game/recording の replay target、延長局の番号、snapshot を含む情報公開範囲を定義した。
- 採点の副露、本場・供託、切り上げ満貫、数え役満、責任払い、チョンボを明確化した。
- session を通した stateful trace、採点の再計算、Schema・registry・hash・公式ベクトルの整合性検査を追加した。

`1.0-draft.4` / profile `1.0-draft.2` とは非互換である。対応する版の成果物一式を使用し、異なる版を混在させない。

## 1.0-draft.4 / profile 1.0-draft.2 — 2026-08-30

- 規範本文、Schema、registry、テストベクトル、validator を release manifest で一覧化した。
- 成果物の権威関係、Protocol Version・profile revision/hash・release ID/tag の責務、互換性判定と公開手順を定義した。
- 採点ベクトルの Schema を追加した。
- 認証方式を対象外とし、公開の spectate/replay に別の認可 profile を要求した。

過去版の成果物は Git 履歴から取得する。変更提案と公開の手順は [仕様策定・リリースプロセス](docs/specification-process.md) を参照する。
