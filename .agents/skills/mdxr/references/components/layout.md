# Layout components

Index: [../components.md](../components.md). MDX attributes are always strings; `children` is Markdown.

### `<Columns cols="2|3|4">` / `<Column>`

Responsive grid layout for side-by-side content (options, before/after).

### `<Grid cols min gap flow items>` / `<Cell span rowSpan>`

Flexible 12-track grid for anything `<Columns>` can't express: asymmetric splits, dashboards, auto-wrapping card grids. Children that aren't `<Cell>` auto-place one track each.

- `cols`: `1`–`12` tracks (default `12`); applies from `sm` up — below that everything stacks.
- `min`: CSS length (`"12rem"`, `"200px"`) switches to `auto-fit` tracks at **every** viewport — use for card grids that wrap naturally. Overrides `cols`.
- `gap`: `none|xs|sm|md|lg|xl` (default `md`). `flow`: `row|col|row-dense|col-dense` — `*-dense` backfills gaps in dashboards. `items`: `stretch|start|center|end`.
- `<Cell>` `span`: `1`–`12` or `full` column span; `rowSpan`: `1`–`4`. Both apply from `sm` up. `className` on either adds arbitrary utilities.

```mdx
<Grid>
  <Cell span="8">Main narrative…</Cell>
  <Cell span="4">Sidebar: meta, links…</Cell>
</Grid>

<Grid cols="4" flow="row-dense">
  <Cell span="2">
    <Stat label="Coverage" value="92%" />
  </Cell>
  <Cell span="2" rowSpan="2">
    Tall notes panel
  </Cell>
</Grid>

<Grid min="14rem" gap="sm">
  <Option title="A" status="recommended">
    …
  </Option>
  <Option title="B" status="considered">
    …
  </Option>
  <Option title="C" status="rejected">
    …
  </Option>
</Grid>
```

### `<Row gap items>` / `<Stack gap items>`

Flex containers for grouping components. Adjacent JSX elements render with no whitespace between them — margin-less primitives (the shadcn/ui set: `Button`, `Badge`, `Input`, `Switch`, …) end up glued together unless wrapped in `<Row>` or `<Stack>`.

- `<Row>`: `flex flex-wrap items-center` — button/badge rows and other inline groups. `gap` default `sm`.
- `<Stack>`: `flex flex-col` — vertical rhythm for stacked controls. `gap` default `md`.
- `gap`: `none|xs|sm|md|lg|xl`. `items`: `stretch|start|center|end|baseline`. `className` adds arbitrary utilities.

```mdx
<Row>
  <Button>Default</Button>
  <Button variant="outline">Outline</Button>
</Row>

<Stack gap="sm">
  <Progress value="60" />
  <Skeleton className="h-4 w-40" />
</Stack>
```

### `<Before>` / `<After>`

Semantic before/after panels (red / green header). Wrap in `<Columns>` for side-by-side; `title` overrides the label ("Current" / "Proposed").
