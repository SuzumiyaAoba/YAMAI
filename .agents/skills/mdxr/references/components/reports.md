# Reports — code review, CI checks, audits, metrics, config, ops & releases

Index: [../components.md](../components.md). MDX attributes are always strings; `children` is Markdown.

This file covers report-shaped deliverables a coding agent produces beyond plans and investigations: review results, verification status, security/dependency scans, quantitative metrics, config documentation, and ops/release summaries.

### `<Review title verdict>` / `<Comment severity title file>` / `:::review`

Code-review result. `Review` tallies `<Comment>` children by severity in a summary line and shows an optional `verdict` pill — `approve` (green, "Approved"), `changes` (amber, "Changes requested"), `comment` (sky, "Commented"). `Comment` renders a numbered card: `severity` is `critical|high|medium|low|info` (default `info`), `title` is a bold headline, `file`/`lines`/`href` link to the code location, children are the finding text (a ` ```diff ` fence inside makes a great suggestion block).

```mdx
<Review title="PR #42 review" verdict="changes">
  <Comment
    severity="high"
    file="src/mdx.ts"
    lines="42-58"
    title="Unbounded recursion"
  >
    The visitor recurses without a depth cap — a hostile doc can overflow the
    stack.
  </Comment>
  <Comment severity="low" title="Naming nit">
    `out` → `result` reads better at the call site.
  </Comment>
</Review>
```

### `<Verdict status title label>` / `:::verdict`

Standalone verdict banner — a colored edge panel with a status pill. `status` is `approve`/`pass` (green), `warn` (amber), `fail` (red), `info` (sky); `label` overrides the pill text; children are the rationale. Use at the top or bottom of any report to state the conclusion ("do not merge", "safe to deploy", "migration is reversible").

```mdx
<Verdict status="warn" title="Deploy with care">
  Schema change ships without a backfill — run it off-peak.
</Verdict>
```

### `<Severity level>`

Inline severity pill: `critical|high|medium|low|info`. Reused by `<Comment>`, `<Vuln>` and `<Incident>`; use standalone inside prose or tables.

### `<Checks title context>` / `<Check name status duration required>` / `:::checks`

CI/verification status list — the "checks" tab of a PR. `Checks` counts `<Check>` children per status and sums parseable durations in the caption; `context` adds a chip (commit SHA, PR number). `Check` needs `name`; `status` is `pass` (default) / `fail` / `running` / `pending` / `skip`, `duration` shows right-aligned, `required` adds a Required chip, `href` links to the job log. Children render as an indented log excerpt — red-tinted when the check failed.

```mdx
<Checks title="CI — main" context="a1b2c3d">
  <Check name="lint" status="pass" duration="32s" required />
  <Check name="test" status="fail" duration="4m12s" required>
    AssertionError in render.test.ts
  </Check>
  <Check name="deploy" status="pending" />
</Checks>
```

For unit-test runs prefer `<Tests>` (pass/fail/skip/todo per assertion); `Checks` models pipeline stages (lint/build/deploy).

### `<Audit title tool>` / `<Vuln severity id package affected fix>` / `:::audit`

Vulnerability/audit report. `Audit` counts `<Vuln>` children per severity in the caption; `tool` adds a scanner chip (`npm audit`, `osv`, `trivy`). `Vuln` renders: severity pill, optional `title`, `id` (CVE/GHSA — `href` links it to the advisory), `package` + `affected` range in mono, `→ fix` version in green, and a `wontfix` badge. Children are the description.

```mdx
<Audit title="Dependency audit" tool="npm audit">
  <Vuln
    severity="critical"
    id="GHSA-35jh-r3h4-6jhm"
    package="minimatch"
    affected="<3.1.2"
    fix="3.1.2"
    href="https://github.com/advisories/GHSA-35jh-r3h4-6jhm"
    title="ReDoS in brace expansion"
  />
  <Vuln severity="low" package="left-pad" wontfix="true">
    No fix planned upstream.
  </Vuln>
</Audit>
```

### `<Bumps title>` / `<Bump name from to kind breaking cves>` / `:::bumps`

Dependency upgrade plan. `Bumps` counts major/minor/patch bumps in the caption. `Bump` needs `name`; `from`/`to` display as `old → new` and the kind (`major`=red, `minor`=amber, `patch`=neutral) is **auto-detected from the versions** — `kind` only overrides. `breaking` adds a warning chip, `cves="2"` shows the CVEs the upgrade fixes, `href` links to the changelog, `note`/children carry the plan detail.

```mdx
<Bumps title="Dependency upgrades">
  <Bump name="react" from="18.3.1" to="19.3.0" breaking />
  <Bump name="minimatch" from="3.0.9" to="3.1.2" cves="2" note="security" />
</Bumps>
```

### `<Packages title>` / `<Package name version kind license>` / `:::packages`

Package inventory — a flat list with a total count in the caption. `Package` needs `name`; `version`, `kind` (`dep`=sky, `dev`=neutral, `peer`=violet, `optional`=amber), `license`, `href` (registry/repo link), `note`/children for the "why it's here".

```mdx
<Packages title="Direct dependencies">
  <Package name="react" version="19.3.0" kind="dep" license="MIT" />
  <Package name="ultracite" version="7.11.1" kind="dev" />
</Packages>
```

### `<Gauges title unit>` / `<Gauge value target tone label path>` / `:::gauges`

Percent-bar list — coverage reports, score breakdowns, progress by area. `Gauge` needs `value` (0–100, or a fraction via `max`). Color is automatic: with `target`, green when met else red; without, graded 80/50 bands — `tone` (`emerald|amber|red|sky|violet|neutral`) pins it. Label via `label` or `path` (a real file becomes an editor link); `detail` adds a muted second line ("412/500 lines").

```mdx
<Gauges title="Coverage by area" unit="lines">
  <Gauge path="src/mdx.ts" value="82" target="80" detail="412/500 lines" />
  <Gauge path="src/cli.ts" value="45" detail="90/200 lines" />
</Gauges>
```

### `<Score value max label detail>` — 0-100 ring gauge

Circular score gauge — health scans, lint scores, quality grades. `value` is required (`max` changes the denominator); 80+ green, 50+ amber, below red. `label` names the metric, `detail` the sub-line. Inline-flex sized — group several in `<Row>` or `<Grid>`.

```mdx
<Row>
  <Score value="87" label="react-doctor" detail="0 errors, 2 warnings" />
  <Score value="9" max="10" label="a11y audit" />
</Row>
```

### `<Spark values tone label>` — inline sparkline

Tiny inline trend line for prose. `values` is a comma/space-separated number list, `tone` a color, `label` the accessible name. Renders nothing with <2 points.

```mdx
p95 latency improved <Spark values="120,110,96,88,71,65" tone="emerald" /> over 6 runs.
```

### `<Benchmarks title better unit>` / `<Bench name before after>` / `:::benchmarks`

Before/after measurement table. `Bench` needs `name`; `before`/`after` show as `old → new` and the % delta is computed automatically (`unit` suffixes are display-only). `Benchmarks better="lower"` (for latency/size) flips the delta color: improvement = green trend-up, regression = red trend-down; default is `higher`. `note`/children carry caveats.

```mdx
<Benchmarks title="render bench" better="lower" unit="ms">
  <Bench name="small doc" before="120" after="98" />
  <Bench name="large doc" before="820ms" after="910ms" />
</Benchmarks>
```

For timing _within_ one operation (span offsets) prefer `<Waterfall>`; `Benchmarks` compares two runs.

### `<DiffStat files adds dels>` — diff size summary

The `+N −M across F files` stat from a PR header, as an inline chip with a proportional green/red bar. All attributes are numbers; `files` is optional.

```mdx
The PR touched <DiffStat files="12" adds="340" dels="120" /> in the renderer.
```

### `<Schema title engine>` / `<DbTable name note>` / `<DbField name type pk fk>` / `:::schema`

Database schema documentation. `Schema` is a section wrapper (`engine` shows muted, e.g. `postgres 16`). `DbTable` is a bordered block per table (`name` required, `note` for row counts/engine). `DbField` is one column row: `name` + `type` required, `pk`/`fk="table.col"`/`unique`/`null` render constraint chips, `default` shows `= value`; children are the column description.

```mdx
<Schema title="Data model" engine="postgres 16">
  <DbTable name="users" note="core accounts">
    <DbField name="id" type="uuid" pk />
    <DbField name="email" type="text" unique />
    <DbField name="org_id" type="uuid" fk="orgs.id" null />
  </DbTable>
</Schema>
```

### `<EnvVars title>` / `<EnvVar name required secret value default>` / `:::envvars`

Environment-variable reference. `EnvVars` shows a count chip and a copy button that copies all variable names. `EnvVar` needs `name`; `required` adds an amber chip, `value`/`default` display the value (`default:` prefix for defaults), `secret` masks the value as `••••••••` plus a secret chip — never write real secrets, mark them instead. Children are the description.

```mdx
<EnvVars title="Runtime config">
  <EnvVar name="DATABASE_URL" required secret>
    Primary Postgres connection.
  </EnvVar>
  <EnvVar name="LOG_LEVEL" default="info" />
</EnvVars>
```

### `<StatusPage title updated>` / `<Service name status uptime>` / `:::statuspage`

Service-health summary. `StatusPage` rolls up the worst `<Service>` status into the caption ("All systems operational" / Degraded / Outage / Maintenance); `updated` shows a muted timestamp. `Service` needs `name`; `status` is `operational` (default) / `degraded` / `outage` / `maintenance` with a status dot + pill, `uptime` shows right-aligned, children describe the impact.

```mdx
<StatusPage title="System status" updated="2m ago">
  <Service name="API" status="operational" uptime="99.98%" />
  <Service name="Dashboard" status="degraded">
    Elevated p95 since the 14:00 deploy.
  </Service>
</StatusPage>
```

### `<Uptime title pct from to>` / `<Day status date note>` / `:::uptime`

Statuspage-style uptime bar — one cell per day. `Uptime` shows `title`, `pct` (`"99.9"` renders `99.9%`), and `from`/`to` end labels under the bar. `Day` status is `up` (green, default) / `degraded` (amber) / `down` (red) / `maint` (sky) / `none` (gray); `date`/`note` compose the hover tooltip.

```mdx
<Uptime title="API — last 7 days" pct="99.9" from="Sep 12" to="today">
  <Day status="up" date="Sep 12" />
  <Day status="degraded" date="Sep 13" note="deploy" />
  <Day status="down" date="Sep 14" note="INC-12" />
</Uptime>
```

### `<Release version date href title>` / `<Entry kind scope>` / `:::release`

Release notes / changelog block. `Release` needs `version` (shown mono in the caption; `date`, compare `href`, optional `title` prefix). `<Entry>` children are **auto-grouped by kind** into headed sections regardless of write order: `breaking` → `added` → `changed` → `deprecated` → `removed` → `fixed` → `security`, each with an icon and count. `scope` adds a `cli:`-style prefix chip; children are the entry text. Non-Entry children render at the bottom.

```mdx
<Release
  version="v0.2.0"
  date="2026-09-19"
  href="https://github.com/acme/app/compare/v0.1.0...v0.2.0"
>
  <Entry kind="breaking" scope="cli">
    Renamed `--html` to `--format`.
  </Entry>
  <Entry kind="added">New report components.</Entry>
  <Entry kind="fixed">Stdin piping no longer hangs.</Entry>
</Release>
```

### `<Pathway title>` / `<Stop label status note current>` / `:::pathway`

Horizontal stepper for migration/rollout paths — version upgrade routes (v17→v18→v19), environment promotion (dev→staging→prod), phased rollouts. `Stop` needs `label`; `status` is `todo|doing|done|blocked` (same icon set as `<StatusBadge>`), `note` a sub-line, `current` rings the active stop. Connectors draw automatically between stops; overflow scrolls.

```mdx
<Pathway title="Upgrade path">
  <Stop label="v17" status="done" note="current" />
  <Stop label="v18" status="doing" note="codemod" current />
  <Stop label="v19" status="todo" />
</Pathway>
```

For dated milestones prefer `<Timeline>`; `Pathway` is for _sequences_ (no dates).

### `<Incident title severity status …>` / `:::incident`

Incident/postmortem header block. `title` is required; `severity` (shared scale) and `status` (`investigating`=orange, `identified`=amber, `monitoring`=sky, `resolved`=emerald) render as pills. `started`/`detected`/`resolved`/`duration` become a labeled meta row, `impact` a red highlighted line. Children are the summary; follow with `<Timeline>` for the event sequence and `<Steps>` for action items.

```mdx
<Incident
  title="Production DB wiped"
  severity="critical"
  status="resolved"
  started="09:12 UTC"
  resolved="09:46 UTC"
  duration="34m"
  impact="production DB + backups deleted"
>
  An unscoped API token used by the nightly cleanup job had delete permissions.
</Incident>
```
