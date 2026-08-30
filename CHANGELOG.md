# 変更履歴

この変更履歴は、YAMAI の規範文書、機械可読成果物およびリリース管理上の変更を記録する。現在のすべての項目は Draft であり、安定版を意味しない。

## 1.0-draft.5 / profile 1.0-draft.3 — 2026-08-30

### Changed

- YRC 0003 の Protocol Version を `1.0-draft.5`、`riichi-4p` profile の revision を `1.0-draft.3` へ更新。
- YRC 0003/YRC 0005 の規範本文、Schema、registry および test vector に非互換修正があるため、旧 draft と混在しないよう Protocol Version/profile revision を bump。
- 現行成果物に基づき、`riichi-4p` の profile hash を `sha256:140a9b6d4d962799bb2bf2bc5dcdc2fa9ee88cc64374c930d0c3fe84ea749fb8` へ更新。
- Schema、registry、vector の参照先を `1.0-draft.5` / `1.0-draft.3` のディレクトリへ移行。

### Added

- `scoring-vectors.schema.json` を `riichi-4p` の規範 Schema として現行 profile revision に追加。
- 規範成果物の権威関係、hash責務、互換性判定および公開手順を [`docs/specification-process.md`](docs/specification-process.md) に整理。

### Compatibility

- `1.0-draft.5` / `1.0-draft.3` は旧 `1.0-draft.4` / `1.0-draft.2` と互換とみなさない。Protocol Version、profile revision、profile hash および release tag の組を一致させること。

### Status

- 安定版の公開条件（公式 vector と二つ以上の独立相互運用実装）は未充足として扱う。
- 公開 release tag が付くまで、release manifest の `published` は `false` とする。

## 過去版: 1.0-draft.4 / profile 1.0-draft.2 — 2026-08-30

以下は過去版の履歴であり、現行成果物の参照には使用しない。

### Added

- YRC 0003 `1.0-draft.4` と `riichi-4p` profile revision `1.0-draft.2` の規範成果物を release manifest で一覧化。
- 規範本文、全 Schema、registry、test vector、validator の権威関係と適合性の境界を [`docs/specification-process.md`](docs/specification-process.md) に追加。
- Protocol Version、profile revision/hash、release ID/tag の責務と互換性判定を明文化。
- 認証方式が対象外であること、および public/spectate/replay には別の認可 profile が必要であることを明記。
- `scoring-vectors.schema.json` を `riichi-4p` の規範 Schema として追加。

### Changed

- YRC 0003/YRC 0005 の現在の成果物に合わせ、`riichi-4p` の profile hash を `sha256:e610bf40e4ad75e64a1d40a5add5a51f64151b22fa794e57382a906eec617824` に更新。

### Status

- 安定版の公開条件（公式 vector と二つ以上の独立相互運用実装）は未充足として扱う。
- 公開 release tag が付くまで、release manifest の `published` は `false` とする。
