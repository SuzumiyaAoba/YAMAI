---
name: mdxr
description: Write rich plan/report documents as MDX using the mdxr component catalog, then render them to standalone HTML with `npx @suzumiyaaoba/mdxr render`. Use when creating plan files, status reports, reviews, or any structured document meant to be viewed as a styled HTML page.
---

# mdxr — agent-authored documents rendered to HTML

Write documents as **Markdown + a small set of JSX components** (MDX). Do NOT write raw HTML: `mdxr render` compiles the document deterministically, so markup, styling and scripts are never emitted by the model.

## Workflow

1. Write the document as `*.mdx` using Markdown plus the components below.
2. Render: `npx @suzumiyaaoba/mdxr render plan.mdx -o plan.html`
3. Or pipe MDX directly: `cat plan.mdx | npx @suzumiyaaoba/mdxr render > plan.html` (`mdxr render -` also reads stdin; `-o out.html` writes a file).
4. On errors, the message includes `file:line:col` — fix and re-run. `npx @suzumiyaaoba/mdxr render plan.mdx --format json` prints machine-readable errors.
5. Preview while editing: `npx @suzumiyaaoba/mdxr serve plan.mdx` — or pipe: `cat plan.mdx | npx @suzumiyaaoba/mdxr serve`

## Rules

- **No JS in documents.** `import`/`export` and `{expressions}` are rejected. All attributes are strings: `<Step status="done">`, not `status={...}`.
- Prefer plain Markdown for prose; use components only for structure.
- If a needed component is missing, run `npx @suzumiyaaoba/mdxr catalog --json` to see the full catalog, then define it in the project's component file — see `references/extending.md` for the extension mechanism.
- For component usage, read `references/components.md` (the index — includes a syntax cheatsheet), then only the `references/components/*.md` detail file(s) the document needs.

## Component categories

| Category | Covers | Details |
| --- | --- | --- |
| Document scaffolding | `<Plan>` root, meta row, callouts, TOC, glossary, refs, code blocks | `references/components/document.md` |
| Planning & status | phases, steps, timeline, gantt, decisions, risks, board, matrix, stats | `references/components/planning.md` |
| Code investigation | findings, hypotheses, terminal, traces, searches, files, flows, trees | `references/components/investigation.md` |
| Output artifacts | diff cards, graphs, tests, endpoints, JSON, waterfalls | `references/components/output.md` |
| Reports | code review, CI checks, vuln/dep audits, metrics, schema/env docs, status/release/incident | `references/components/reports.md` |
| Data visualization | bar/line/pie/scatter/radar/funnel/quadrant/bridge/treemap/sankey/venn | `references/components/charts.md` |
| Layout | columns, grid, row, stack, before/after panels | `references/components/layout.md` |
| Reader input | `<Ask>` question forms with copyable Markdown answers | `references/components/forms.md` |
| shadcn/ui | `Button`, `Card`, `Table`, `Tabs`, … — interactive via hydration | `references/components/shadcn.md` |

## Common syntax

| Write | Get |
| --- | --- |
| `:::note` / `:::warning` / `:::decision` / `> [!NOTE]` | `<Callout>` |
| `:::phase{title="…" status="doing"}` | `<Phase>` heading with status badge |
| ` ```ts title="src/x.ts" ` | highlighted code block + filename bar |
| ` ```diff ` / ` ```mermaid ` / ` ```console ` | diff cards / diagram / terminal transcript |
| `` `src/x.ts` `` naming a real file | `<FileRef>` chip with editor link |
| `- [ ]` / `- [x]` | styled task list |
| frontmatter `status:` / `date:` / `owner:` | document header badge + meta row |
