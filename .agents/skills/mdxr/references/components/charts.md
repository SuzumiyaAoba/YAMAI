# Charts — quantitative data visualizations

Index: [../components.md](../components.md). MDX attributes are always strings; `children` is Markdown.

Every chart renders a static SVG/HTML panel at render time — no client JS, no CDN. Containers share the same frame: an icon + `title` caption bar and an optional right-side figure (range or total + `unit`). Data comes from typed child elements (`<Bar>`, `<Slice>`, …); anything else renders below the chart.

Shared conventions:

- Numeric attrs accept numbers or numeric strings (`value="42"`); comma lists (`values="1,2,3"`, `labels="Mon,Tue"`) split on `,`.
- `tone="sky|emerald|amber|violet|teal|indigo|orange|red"` pins an item's color; without it the palette cycles by position.
- `unit="ms"` (etc.) labels values in axes, legends and tooltips; `title` sets the caption.
- Standalone children (outside their container) degrade to a small `name value` chip instead of failing.

### `<BarChart title direction series stacked max unit>` / `<Bar name value values note tone>` / `:::barchart`

Categorical comparison. `<Bar name value>` needs `name` and `value` (or `values` for multi-series). `direction="horizontal"` flips to labeled rows for long names. `series="a,b"` names the `values` slots (legend + per-slot colors); `stacked` sums them into one column instead of grouping. `max` overrides the axis ceiling (defaults to a "nice" bound over the data).

```mdx
<BarChart title="deploys per quarter" unit="deploys">
  <Bar name="Q1" value="18" />
  <Bar name="Q2" value="26" note="launch" />
  <Bar name="Q3" tone="emerald" value="34" />
</BarChart>
```

```mdx
<BarChart series="this year,last year" title="latency by endpoint" unit="ms">
  <Bar name="/users" values="42,58" />
  <Bar name="/search" values="120,210" />
</BarChart>
```

### `<LineChart title labels area min max unit>` / `<Series name values tone dash>` / `:::linechart`

Trend lines over evenly-spaced categories. `labels="Mon,Tue,…"` names the x slots; each `<Series values="…">` aligns with them (extra points extend the axis). The y domain snaps to round bounds — `min`/`max` pin it. `area` adds a soft fill under each line; `dash` on a series draws it dashed (baselines/targets).

```mdx
<LineChart labels="Mon,Tue,Wed,Thu,Fri" title="p95 latency" unit="ms">
  <Series name="api" values="120,118,132,125,110" />
  <Series name="edge" tone="emerald" values="60,58,64,70,55" />
</LineChart>
```

### `<PieChart title donut unit>` / `<Slice name value note tone>` / `:::piechart`

Part-of-whole share. `<Slice name value>` rows fill the legend with name · value · %; `donut` cuts a center hole carrying the total. Empty or all-zero data renders a neutral ring.

```mdx
<PieChart donut title="build minutes" unit="min">
  <Slice name="compile" value="38" />
  <Slice name="tests" value="27" />
  <Slice name="lint" value="9" />
</PieChart>
```

### `<Scatter title x y>` / `<Point x y name size tone>` / `:::scatter`

Two-variable correlation on auto-scaled numeric axes — `x`/`y` on the container name them. `<Point>` needs `x` and `y`; `name` labels the dot, `size` turns it into a bubble (radius ∝ √size).

```mdx
<Scatter title="latency vs payload" x="payload kb" y="latency ms">
  <Point x="12" y="40" />
  <Point name="bulk export" size="90" tone="amber" x="140" y="210" />
</Scatter>
```

### `<Radar title axes max unit>` / `<Series>` / `:::radar`

Spider chart for multivariate comparison — needs ≥3 axes. `axes="Perf,DX,Tests"` names the corners; each `<Series>` becomes a polygon. `max` fixes the ring scale (default: largest value snapped up); ring gridlines sit at quarter steps.

```mdx
<Radar axes="Perf,DX,Tests,Docs,Security" title="service health">
  <Series name="api" values="80,65,90,40,70" />
  <Series name="web" tone="emerald" values="60,85,55,70,60" />
</Radar>
```

### `<Funnel title unit>` / `<Stage name value note tone>` / `:::funnel`

Stage-by-stage narrowing — centered bars shrink through `<Stage>` rows in order. Each row shows the value plus its share of the first stage; the left column carries the conversion from the previous stage when it drops.

```mdx
<Funnel title="signup funnel" unit="users">
  <Stage name="visited" value="12800" />
  <Stage name="signed up" value="3400" />
  <Stage name="paid" tone="emerald" value="420" />
</Funnel>
```

### `<Quadrant title x y quadrants>` / `<Pin name x y note tone>` / `:::quadrant`

2-axis positioning map — `<Pin>` items on a 0–100 square split by midlines. `x`/`y` name the axes; `quadrants="TL,TR,BL,BR"` labels the four regions (top-left → top-right → bottom-left → bottom-right).

```mdx
<Quadrant
  title="capability map"
  x="adoption"
  y="impact"
  quadrants="invest,bet,maintain,drop"
>
  <Pin name="mdxr" tone="sky" x="78" y="85" />
  <Pin name="legacy" tone="red" x="15" y="15" />
</Quadrant>
```

### `<Bridge title unit>` / `<Delta name value note total>` / `:::bridge`

Running-total waterfall — a start pillar carried to an end pillar by signed `<Delta>` contributions. Deltas float from the previous sum to the new one (emerald up / red down); `total` re-anchors the running sum at its `value` as a neutral pillar. Connector ticks link each column to the previous total.

```mdx
<Bridge title="FY revenue bridge" unit="M$">
  <Delta name="FY24" total value="120" />
  <Delta name="new sales" value="48" />
  <Delta name="churn" value="-15" />
  <Delta name="FY25" total value="175" />
</Bridge>
```

### `<Treemap title unit>` / `<Tile name value note tone>` / `:::treemap`

Part-of-whole areas packed by the squarified algorithm — `<Tile>` area ∝ `value`. Labels carry the name plus value and share percent (small tiles drop the number line).

```mdx
<Treemap title="bundle by workspace" unit="kB">
  <Tile name="ui" value="184" />
  <Tile name="vendor" note="react+mdx" value="260" />
  <Tile name="cli" value="72" />
</Treemap>
```

### `<Sankey title stages unit>` / `<Link from to value label>` + `<Node id label stage>` / `:::sankey`

Quantity splitting/merging across stage columns — ribbon thickness ∝ `<Link value>`. Nodes are implicit from link endpoints; `<Node>` children (the same element `<Graph>` uses) rename them via `label`, pin a column via `stage`, or recolor via `tone`. Stage columns come from longest-path layering when not pinned; `stages="A,B,C"` titles the columns. Cycles fall back to stage 0.

```mdx
<Sankey stages="source,grid,use" title="energy flow" unit="TWh">
  <Link from="solar" to="grid" value="40" />
  <Link from="wind" to="grid" value="35" />
  <Link from="grid" to="homes" value="55" />
  <Link from="grid" to="industry" value="20" />
</Sankey>
```

### `<Venn title unit>` / `<Set name value tone>` + `<Overlap sets value>` / `:::venn`

Set overlaps — 2 or 3 `<Set>` circles (uniform size; a Venn is not to scale). `<Overlap sets="a,b" value>` prints a value at the intersection named by comma-joined set names.

```mdx
<Venn title="skill coverage" unit="devs">
  <Set name="frontend" value="14" />
  <Set name="backend" value="18" />
  <Overlap sets="frontend,backend" value="6" />
</Venn>
```

## Choosing

| Data shape | Component |
| --- | --- |
| Category comparison, single or grouped/stacked series | `<BarChart>` |
| Trend over ordered slots, one or more series | `<LineChart>` |
| Shares of a total (≤ ~8 parts) | `<PieChart>` or `<Treemap>` (many/uneven parts) |
| Two numeric variables, optional bubble | `<Scatter>` |
| Multivariate profile on 3+ shared axes | `<Radar>` |
| Sequential narrowing / conversion | `<Funnel>` |
| 0–100 positioning on two named axes | `<Quadrant>` |
| Start→deltas→end reconciliation | `<Bridge>` |
| Flow splitting/merging between columns | `<Sankey>` |
| 2–3 set membership overlap | `<Venn>` |
| Timing offsets on a shared clock | `<Waterfall>` (output.md) |
| Date-based schedule | `<Gantt>` (planning.md) |
| Arbitrary node/edge topology | `<Graph>` (output.md) |
