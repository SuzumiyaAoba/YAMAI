# YAMAI 仕様策定・リリースプロセス

## 1. 目的と状態

本書は、YAMAI の規範文書、機械可読成果物およびリリースを同一の版として管理するためのプロセスを定める。本書自身はプロセス文書であり、対局 wire の新しい message、event、action または rule を追加しない。

現在の対象は `yamai-1.0-draft.9`（Protocol Version `1.0-draft.9`、`riichi-4p` profile revision `1.0-draft.7`）であり、安定版ではない。公開用 release tag が付されるまでは、release manifest の `published` を `false` とする。

## 2. 権威関係

実装者は、次の順序で成果物の適用範囲を確認する。

1. [YRC 0003](yamai-protocol.md) は Protocol Version の transport、message envelope、交渉、状態遷移、visibility、error、resume、適合性および組込み `riichi-4p` profile の全規範を規定する。
2. YRC 0003 の message Schema とその `$ref` 閉包、および profile の rules/result Schema は、本文を機械的に検査する派生成果物である。release manifest が対象ファイルを列挙するが、Schemaは本文と競合したとき本文へ従う。
3. registry は、本文の profile、capability、message kind、event/action、error、rule、result reason および yaku/bonus の識別子と許可値を機械可読化する派生成果物である。
4. 公式 test vector は、Schemaだけでは表現しにくい相互作用、境界条件、状態遷移、秘匿、再送、精算および資源制限を本文に照合する適合性補助である。
5. Stateful trace Schemaとsemantic validatorは、sessionを通した前後条件、`seq` ledger、request/group、timeout、snapshot置換およびvisibility射影を実行検査する。
6. scoring oracleは、fixtureの期待値を入力として使用せず、YRC 0003 §7.6 に従って手牌・rule・eventから役、符、点数、支払いおよびdeltaを再計算する。
7. 5つのQuintモデルが有限境界内で要求・時計・配送・resumeの安全性を検査する。有限モデル検査を無制限状態または実装コードの完全証明とみなしてはならない。

YRC 0001、YRC 0002 および YRC 0004 は Informational 文書であり、既存 MJAI の背景・欠陥・実装差を説明する。これらの記述は YAMAI 適合要件の代替にならない。

JSON Schema の検証だけでは、重複 JSON key、frame境界、`seq` の連続性、状態遷移、request の冪等性、visibility、点数保存則および timing の全てを保証できない。`scripts/validate_artifacts.py`、`scripts/score_oracle.py`およびQuintモデルは、それぞれ異なる検証層を担当するが、いずれか一つの成功だけで YRC 0003 第17節の完全適合を表明してはならない。適合表明には、本文、Schema、registry、公式 vector、stateful trace、scoring oracle、形式モデルおよび必要な独立相互運用試験を併せて確認する。

検査の実行方法、適合項目との対応および各検査の範囲は [検証ガイド](../verification/README.md) に記載する。

## 3. Protocol Version と profile hash の責務

`yamai`、`hello.versions`、`join.version` および各 message Schema の版は Protocol Version に属する。Protocol Version `1.0-draft.9` は、release manifest に列挙されたYRC 0003のmessage Schema閉包とjoin-proposal Schemaを固定する。message Schema の変更は、互換性の有無に応じて draft revision または major version を更新しなければならない。

`profile_revision` と `profile_hash` は `riichi-4p` など profile の同一性を表す。現在のprofile_hashは、YRC 0003 第6.2節が列挙する7個の派生JSON成果物をJCS projectionして計算する。これらの意味は規範本文に従う。

- YRC 0003 の profile Schema
- `riichi-4p` の rules Schema
- `riichi-4p` の scoring-vectors Schema
- profile hash 自身を除いた YRC 0003 registry
- `riichi-4p` の採点 registry
- YRC 0003 official vectors（helloの広告hashとjoin/welcomeのprofile_hash、およびwire保存の対応hash tokenをゼロ値へ正規化）
- `riichi-4p` の scoring vectors

Protocol message Schema、release manifest および本文そのものは別の責務である。前者は Protocol Version、後者は release ID と同一 Git tag によって固定する。この責務境界を理由に、Protocol Version、profile revision、profile hash および release ID/tag を別の値として管理する。派生成果物の内容が本文と異なる場合は本文から再生成する。

## 4. Release ID、tag および互換性

各公開単位は root の [`release-manifest.json`](../release-manifest.json) に一意な `release_id` を持つ。manifest に列挙された規範文書、全 Schema、registry、vector、validator、scoring oracle、形式モデルおよび本プロセス文書は、同じ Git commit と同じ release tag から取得できなければならない。別の tag の成果物を混在させてはならない。

互換性は次の順に判定する。

1. `yamai`/Protocol Version が完全一致するか確認する。draft 版は完全一致しない限り互換とみなさない。
2. profile 名、`profile_revision` および `profile_hash` の組が一致するか確認する。
3. capability、rule、mode/view、transport 上限および適合試験の差を確認する。値が一致しても、独立実装試験に失敗した組を互換と表明しない。

Protocol の必須 member、状態遷移、既存値の意味または scoring semantics を変更する場合は、対応する Protocol Version または profile revision を更新する。任意の無害な追加だけは、本文の互換性規則を満たす場合に限り同一 major 内で許可する。

## 5. 変更、承認および公開

変更は pull request で行い、少なくとも次を同時に更新する。

- 変更対象の規範文書と版メタデータ
- release manifest の `release_id`、version、revision、hash および対象ファイル
- 影響する Schema、registry、公式 vector および validator
- [`CHANGELOG.md`](../CHANGELOG.md) の変更理由、互換性影響および移行方法

レビューでは、authority boundary、互換性、security/privacy、Schema と本文の整合、vector の十分性、外部参照の版固定およびリンクを確認する。規範変更は、少なくとも一人の変更者以外の reviewer が承認し、安定版を名乗る変更では二つ以上の独立相互運用実装と公式 vector の通過結果を記録する。

公開時は、レビュー済み commit に release manifest の `required_git_tag` を付け、tag 上の manifest で `published: true` を示す。tag、manifest、changelog または規範成果物のいずれかが一致しない場合は公開を中止し、Draft のまま扱う。過去の tag の成果物を上書きせず、訂正は新しい release ID と版を作成する。

## 6. セキュリティの適用範囲

YAMAI は対局 message semantics と秘匿 view を規定するが、対局サービスの認証方式、アカウント、権限管理およびレーティングを規定しない。`play` を信頼境界の外で使う場合は YRC 0003 の transport security 要件を満たす必要がある。

`public`、`spectate` または `replay` を公開運用する場合、`target` のゲーム／記録へのアクセス、`full` view、座席 view、resume token およびログの認可を別の security/authorization profile で定義しなければならない。YAMAI の protocol version または profile hash だけで認証・認可済みであると判断してはならない。
