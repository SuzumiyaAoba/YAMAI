# YAMAI draft 1 仕様案内

YAMAI は、4人リーチ麻雀のホスト、AI プレイヤー、観戦・牌譜クライアントが共通の状態と結果を交換するためのプロトコルである。Protocol Version と `riichi-4p` profile revision はともに `1.0-draft.1`。

規範は [YAMAI 仕様書](yamai-protocol.md) に集約する。本書は仕様の読み方と主要な契約を案内する参考文書であり、規範要件を追加しない。

[リポジトリのREADME（Markdown）](../README.md) / [規範本文](yamai-protocol.md) / [成果物の仕様](artifacts.md) / [検証ガイド](../verification/README.md) / [形式モデル](../verification/quint/README.md)

## この案内の読み方

- 全体をつかむ: [対象範囲](#対象範囲) → [通信の流れ](#通信の流れ) → [状態と識別子](#状態と識別子)
- 実装を始める: [目的別の参照先](#実装目的別の参照先) → [実装を進める順序](#実装を進める順序)
- 既存実装を確認する: [成果物の仕様](artifacts.md) → [検証ガイド](../verification/README.md)

版・profile の値や識別子は、同じ commit の [release manifest](../release-manifest.json)、Schema、registry と合わせて読む。draft 同士の交渉と安定版の条件は[仕様書第1節](yamai-protocol.md#1-仕様の範囲と規約)に従う。

## 対象範囲

| 領域 | 定義する内容 |
|---|---|
| 接続 | JSON Lines / WebSocket、JSON の制約、資源上限 |
| 交渉 | Protocol Version、profile revision/hash、capability、mode/view、座席・対象 |
| 対局 | 4人リーチ麻雀のルール、牌、合法候補、行動とイベント、点数・局進行 |
| 配送 | session ごとの順序、不変の送信履歴、重複・欠落・衝突の判定 |
| 復旧 | play の resume、有限範囲の再送、snapshot による状態置換 |
| 適合性 | 必須試験、公式テストベクトル、実行検査と形式モデルの範囲 |

3人麻雀、AI の推論方法、アカウント、資格情報の発行方式、レーティングおよび課金は定義しない。公開サービスはゲーム・記録・座席・view・resume token・ログの認可を別の security/authorization profile で定義する。

## 実装目的別の参照先

| 目的 | 主に読む節 |
|---|---|
| 通信クライアント | 仕様書 [§4 JSON と transport](yamai-protocol.md#4-json-と-transport)、[§5 envelope](yamai-protocol.md#5-共通-envelope)、[§6 交渉](yamai-protocol.md#6-版機能交渉) |
| AI の行動選択 | [§7.3 牌と合法候補](yamai-protocol.md#73-牌)、[§8 行動要求](yamai-protocol.md#8-行動要求)、[§9 ACK と期限](yamai-protocol.md#9-ack-と-timeout) |
| 対局ホスト | [§3 状態モデル](yamai-protocol.md#3-プロトコルモデル)、[§7 ルールと採点](yamai-protocol.md#7-riichi-4p-profile)、[§10 イベント順序](yamai-protocol.md#10-イベント順序) |
| 観戦・牌譜 | [§11 mode と view](yamai-protocol.md#11-visibility-と-mode)、[§13 snapshot](yamai-protocol.md#13-再接続と-snapshot) |
| エラーと再接続 | [§12 エラー](yamai-protocol.md#12-エラー)、[§13 再接続](yamai-protocol.md#13-再接続と-snapshot)、[§15 資源上限](yamai-protocol.md#15-資源安全要件) |
| 拡張機能 | [§14 拡張](yamai-protocol.md#14-拡張)、[§19 識別子](yamai-protocol.md#19-識別子と-registry) |
| 適合性の確認 | [§17 適合性](yamai-protocol.md#17-適合性)、[成果物の仕様](artifacts.md)、[検証ガイド](../verification/README.md) |

## 実装を進める順序

次は読み進め方の目安であり、途中まで実装すれば適合するという段階別の適合規定ではない。各工程の受理・拒否条件と順序は、参照先の規範本文に従う。

| 順序 | 取り組む内容 | 規範・確認先 |
|---|---|---|
| 1 | 対象の版、profile、mode/view、ルールと成果物一式を決める | [§1](yamai-protocol.md#1-仕様の範囲と規約)、[§6](yamai-protocol.md#6-版機能交渉)、[§11](yamai-protocol.md#11-visibility-と-mode)、[成果物の版と適用範囲](artifacts.md#版と適用範囲) |
| 2 | JSON・transport・envelope と交渉の入口を実装する | [§4–6](yamai-protocol.md#4-json-と-transport)、[§12 エラー](yamai-protocol.md#12-エラー)、[§15 資源上限](yamai-protocol.md#15-資源安全要件) |
| 3 | game、session、要求・時計を分けて保持する | [§3 状態モデル](yamai-protocol.md#3-プロトコルモデル)、[§5 seq](yamai-protocol.md#5-共通-envelope)、[§8–9 要求とACK](yamai-protocol.md#8-行動要求) |
| 4 | 合法候補・競合解決・event・局結果とviewの投影を接続する | [§7 profile](yamai-protocol.md#7-riichi-4p-profile)、[§10 イベント順序](yamai-protocol.md#10-イベント順序)、[§11 visibility](yamai-protocol.md#11-visibility-と-mode) |
| 5 | 切断・再送・snapshot・fatal終了と資源境界を扱う | [§13 復旧](yamai-protocol.md#13-再接続と-snapshot)、[§8.1.2 DETACHED](yamai-protocol.md#812-fatal終了seatの自動応答)、[§15–18 資源とセキュリティ](yamai-protocol.md#15-資源安全要件) |
| 6 | 正例・負例、回帰、形式モデルを対象別に照合する | [検査の役割](../verification/README.md#検査の役割)、[適合35項目](../verification/README.md#適合35項目との対応)、[検証の範囲](../verification/README.md#検証の範囲) |

AI プレイヤーも、候補の選択だけでなく受信状態・配送位置・再開時の整合性を扱う。対局ホストは完全情報の採点・合法手を管理し、受信者側の検査は公開された範囲を超えて非公開牌を補わない。アカウントやアクセス権の設計は[§18.6](yamai-protocol.md#186-サービスの認証認可との境界)と分けて確認する。

## 通信の流れ

```mermaid
sequenceDiagram
    participant H as Host
    participant P as Player
    H->>P: hello（版・profile・機能・受信上限）
    P->>H: join（選択した組・mode・view）
    H->>P: welcome（session・座席・rules）
    H->>P: event（確定した状態）
    H->>P: request（候補・期限・request_id）
    P->>H: action（request_id・action_id）
    Note over H: 選択を確定し、競合を解決
    H->>P: ack（選択の結果）
    H->>P: event（対局状態への反映）
```

プレイヤーは `request` にだけ応答する。候補の内容は `legal_actions` にあり、応答には発行済み `action_id` を使う。ホストは切断中も期限と内部処理を継続し、確定した選択と結果を保持する。

## 状態と識別子

| 値 | 意味 |
|---|---|
| `game_id` | 対局を識別する |
| `session_id` | mode・view と配送状態を識別する |
| `seq` | session 内のホストメッセージの番号。新規メッセージで1ずつ増える |
| `request_id` | 一つの要求を識別する |
| `action_id` | その要求の合法候補を識別する |
| `profile_revision` / `profile_hash` | 交渉するルール・採点成果物の同一性を表す |

game の状態、session の配送位置、要求の選択・計時は独立して保持する。同じ `seq` の再送には元の JSON payload の byte 列を使い、再直列化しない。受信側は連続して完全に適用した位置だけを進める。

## モードと情報公開

| mode | view | 行動要求 | 対象 |
|---|---|---|---|
| `play` | `seat` | 自席の request に応答する | 新規対局への参加、または token が示す session の再開 |
| `spectate` | `public` | 受信しない | `game` |
| `replay` | `public` / `full` / 座席指定 | 受信しない | `game` または `recording` |

非公開牌1枚は `null`、非公開手牌は `count` で表す。公開範囲は通常の event、再送、snapshot と診断にも適用する。`public` は状態の投影範囲を表し、対象へのアクセス許可は別に判断する。

## 検査と HTML

[release manifest](../release-manifest.json) の成果物を同じ組として使用する。Schema はメッセージ構造、状態検査は順序と前後条件、採点検査は手牌・ルールから求めた役・符・支払いを扱う。Quint は有限境界内の制御フローを検査する。

- コマンドと前提環境: [検証ガイドの実行方法](../verification/README.md#実行方法)
- 各検査が保証しない範囲: [検証の範囲](../verification/README.md#検証の範囲)
- HTML の生成・原本の編集先: [文書の原本と生成物](artifacts.md#文書の原本と生成物)
- 文書や成果物を変更したとき: [変更時の確認手順](artifacts.md#変更時の確認手順)
