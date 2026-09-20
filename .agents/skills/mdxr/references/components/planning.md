# Planning & status components

Index: [../components.md](../components.md). MDX attributes are always strings; `children` is Markdown.

### `<Phase title="…" status="todo|doing|done|blocked" owner due>`

A section heading with a status badge, plus optional owner chip and deadline. Also produced by `:::phase{title="…"}`.

### `<Steps progress>` / `<Step status owner effort priority due>`

Ordered, status-aware step list. `progress` on `<Steps>` renders an automatic progress bar counting `<Step>` children. `Step` accepts `owner="…"`, `effort="xs|s|m|l|xl"`, `priority="p0|p1|p2|p3"` and `due="YYYY-MM-DD"` — each renders a chip under the step text.

```mdx
<Steps progress>
  <Step status="done">Read the existing parser</Step>
  <Step status="doing" owner="@alice" priority="p1" due="2026-09-18">
    Add directive transform
  </Step>
  <Step status="todo" effort="m">
    Update snapshots
  </Step>
</Steps>
```

### `<Timeline title="…">` / `<Event date status title>`

Chronological milestone list with a left rail. `Event` requires `date` (any string), optional `status` (todo|doing|done|blocked) and `title`. Also produced by `:::timeline{title="…"}`.

```mdx
<Timeline title="Milestones">
  <Event date="2026-09-10" status="done" title="Skeleton merged" />
  <Event date="2026-09-30" status="todo" title="v1.0 freeze" />
</Timeline>
```

### `<Gantt title start end today>` / `<Task name start end status progress owner note>` / `<Milestone name date status owner note>` / `:::gantt`

Date-based schedule chart. `<Task>` needs `name` + `start` (ISO `YYYY-MM-DD`); `end` defaults to `start` (a one-day bar). `status` (todo|doing|done|blocked) colors the bar, `progress="0–100"` shows a partial fill, `owner`/`note` add a muted sub-line. `<Milestone>` needs `name` + `date` and renders a diamond. The range spans the earliest start to the latest end; `start`/`end` on `<Gantt>` override it. A "today" marker is drawn when the render date falls inside the range — disable with `today="false"` or pin it with `today="YYYY-MM-DD"`. Axis ticks are days on short ranges, Mondays mid-range, month starts after.

```mdx
<Gantt title="v1.0 release">
  <Task name="API design" start="2026-09-01" end="2026-09-05" status="done" />
  <Task
    name="Implementation"
    start="2026-09-08"
    end="2026-09-18"
    status="doing"
    progress="40"
    owner="alice"
  />
  <Task name="Docs" start="2026-09-21" end="2026-09-25" />
  <Milestone name="v1.0 freeze" date="2026-09-30" />
</Gantt>
```

### `<Decision title="…" status="…" date="…">`

Decision record (ADR-lite). `status` is `proposed|accepted|rejected|deprecated| superseded`. Children hold context and rationale. For a one-line record prefer `:::decision` (the Callout kind).

```mdx
<Decision
  title="Use renderToStaticMarkup (sync)"
  status="accepted"
  date="2026-09-12"
>
  Documents have no data fetching — a synchronous renderer keeps the CLI simple.
</Decision>
```

### `<Option title="…" status="recommended|considered|rejected">`

Alternative-comparison card. Pair with `<Columns>` for side-by-side layout.

```mdx
<Columns>
  <Option title="Template literals" status="rejected">
    Escaping bugs keep recurring.
  </Option>
  <Option title="Component pipeline" status="recommended">
    Deterministic + validated.
  </Option>
</Columns>
```

### `<Risk level="low|medium|high" title="…" mitigation="…">`

Risk block with a severity pill; `mitigation` renders a dedicated line.

```mdx
<Risk level="high" title="Ecosystem drift" mitigation="Pin @mdx-js/mdx">
  `evaluate()` semantics changed across majors before.
</Risk>
```

### `<Approvals>` / `<Approval name role status date>`

Sign-off list. `status` is `pending|approved|rejected|changes-requested`; children render as a comment next to the approver.

```mdx
<Approvals>
  <Approval name="alice" role="tech lead" status="approved" date="2026-09-14" />
  <Approval name="bob" role="security" status="pending" />
</Approvals>
```

### `<Stats>` / `<Stat value label delta>`

Metric card grid. `delta` colors by sign (`"+12"` green, `"-34%"` red).

### `<Priority level="p0|p1|p2|p3">` / `<Effort size="xs|s|m|l|xl">` / `<Due date="YYYY-MM-DD">` / `<Owner name="…" role="…">`

Inline chips: priority pill, T-shirt effort estimate (children = e.g. `3d`), deadline chip colored by urgency at render time (overdue → red, ≤3d → amber), and an initials-avatar person chip.

### `<Reqs>` / `<Req id="REQ-1" status="…">`

Requirement / acceptance-criteria rows: `id` renders a mono chip, optional `status` (todo|doing|done|blocked) a badge; children are the requirement text.

### `<Board title>` / `<Lane title status>` / `<BoardCard title>` / `:::board`

Kanban board — `Lane`s lay out side by side (horizontal scroll on narrow viewports); the header shows a status dot plus a card-count badge. `BoardCard` requires `title`; optional `status` icon plus the same inline chips as `Step` (`priority`, `effort`, `owner`, `due`); children are a muted description. Interactive: readers can drag cards between lanes (or use the ‹ › buttons on each card), and "Copy markdown" copies the current arrangement as `<Board>` markup to paste back into the source.

```mdx
<Board title="Sprint 12">
  <Lane title="Todo" status="todo">
    <BoardCard title="Write migration guide" priority="p1" owner="alice" />
  </Lane>
  <Lane title="In progress" status="doing">
    <BoardCard title="Graph component" status="doing" due="2026-09-20">
      dagre layout + svg edges
    </BoardCard>
  </Lane>
  <Lane title="Done" status="done">
    <BoardCard title="Ship v0.1" status="done" />
  </Lane>
</Board>
```

### `<Matrix title cols="…">` / `:::matrix{cols="…"}`

Comparison matrix. `cols` is a comma-separated header row; each top-level list item is one matrix row — the item text before the first `|` is the row label, the `|`-separated rest are cells. Cell values `yes`/`ok`/`✓`/`○`, `no`/`✗`/`×`/`ng`, `partial`/`~`/`△`/`warn`, and `-`/`—`/`?` render as icons; anything else renders as text.

```mdx
<Matrix title="Renderer comparison" cols="mdxr, raw mdx, astro">
  - standalone html | yes | no | partial - editor links | yes | no | no -
  hydration | no | yes | yes
</Matrix>
```

### `<Summary done="3" total="8" label="Progress" />`

Progress bar.

### `<StatusBadge status="…" />`

Standalone status pill.
