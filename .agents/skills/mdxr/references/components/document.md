# Document scaffolding

Index: [../components.md](../components.md). MDX attributes are always strings; `children` is Markdown.

### `<Plan title="…" status="…" date owner version updated>`

Document root. Renders a title header with an optional status badge and a metadata row. If frontmatter `title:` exists, a header is generated automatically — frontmatter `status:`, `date:`, `owner:`, `version:` and `updated:` are picked up too.

```mdx
<Plan title="Renderer rewrite" status="doing" owner="@alice" date="2026-09-10" version="v3">
```

### `<Meta date owner version updated>` / `<MetaItem label="…" icon="…">`

Document metadata row (muted line: `Date: … · Owner: …`). Usable anywhere, not only inside `<Plan>`. For custom entries nest `<MetaItem label="Spec" icon="lucide:tag">…</MetaItem>` — `MetaItem` children may contain Markdown links; `icon` takes any Iconify name (`lucide` bundled, prefix optional).

### `<Callout kind="…" title="…">`

Highlighted block. `:::note`, `:::warning`, `> [!NOTE]` produce the same output. Kinds: `note` `tip` `important` `warning` `caution` `danger` `decision` `goal` `nongoal` `question` `answer` — `goal`/`nongoal`/`question` cover plan-doc conventions, `answer` is the conclusion block of an investigation report. `:::non-goal` and `> [!NON-GOAL]` are aliases of `nongoal`.

### `<Details summary="…" open>`

Collapsible section on a native `<details>` element — opens/closes even without client JS (unlike the shadcn `Collapsible`, which needs the hydration bundle). `open` starts it expanded.

### `<Toc depth="3" min="2" title="Contents" open />` / `:::toc`

Table of contents auto-built from the document's headings (h2–h3 by default — h1 is the document title). Headings always get slug `id`s, so `[link](#slug)` deep links work anywhere. Renders as a collapsible outline — numbered top-level entries, guide-lined nesting — on a native `<details>` (works without JS); `open="false"` starts it folded.

### `<Glossary>` / `<Term name="…">`

Definition list (`<dl>`) for domain terms. `name` is the term; children are the definition.

### `<Figure src="…" alt="…" caption="…">`

Image with an optional caption (children work too). Use for screenshots or diagrams mermaid can't express.

### `<Ref href="…" title="…">` / `<Issue repo="o/r" number="12">` / `<PR repo="o/r" number="5">` / `<Commit repo="o/r" sha="…">`

`Ref` is a linked reference card (use for a "References" section). `Issue`/`PR`/`Commit` are inline chips linking to `github.com/{repo}/issues|pull|commit/{id}` — `repo` may also be `host/owner/repo` or a full URL for GitHub Enterprise. `Commit` displays the first 7 chars of `sha`, children become the title; `href` overrides the URL.

### `<Cmd>`

Inline command chip — terminal icon + copy button.

```mdx
Run <Cmd>pnpm build</Cmd> then <Cmd>mdxr render plan.mdx</Cmd>.
```

### `<Icon name="lucide:rocket" label className />`

Inline Iconify icon rendered as SVG — no runtime fetch. The `lucide` and `vscode-icons` sets are bundled; the `lucide:` prefix may be omitted (`name="check"`). Unknown names are render-time validation errors. Decorative by default (`aria-hidden`); pass `label` to expose it as an image with `aria-label`. Size and color come from `className` (`h-4 w-4 text-teal-500`).

Status badges, step markers, callouts, chips and the file tree all carry appropriate icons automatically.

```mdx
Launch checklist <Icon name="lucide:rocket" className="h-4 w-4" />
```

Icons also work as CSS classes (mask-image, single-color) on any element:

```mdx
<span className="icon-[lucide--github] h-5 w-5" />
```

### Fenced code

````
```ts title="src/cli.ts"
…
```
````

renders a framed block with filename + copy button; the header carries a file-type icon picked from the filename (or language), and links to the file in your editor when it exists on disk (see "File links" in [../components.md](../components.md)). Code is syntax-highlighted with Shiki (light/dark dual theme), so always tag the fence with a language (`ts`, `python`, `diff`, …). ` ```mermaid ` renders a diagram.

The fence meta also controls line presentation:

````
```ts {1,3-4} ln title="src/cli.ts"
…
```
````

- `{1,3-4}` highlights those lines; `/pattern/` highlights every match (word highlight).
- `ln` shows line numbers (also `line-numbers`, `lineNumbers`, `showLineNumbers`).

Inside the code, `// [!code …]` markers annotate lines and are stripped from output:

| Marker | Effect |
| --- | --- |
| `// [!code hl]` / `// [!code highlight]` | highlight the line |
| `// [!code ++]` / `// [!code --]` | green/red diff rows (works in any language) |
| `// [!code warning]` / `// [!code error]` | amber/red line bands |
| `// [!code focus]` | dim all other lines |
| `// [!code word:foo]` | highlight every `foo` occurrence |

### Math

`$…$` inline and `$$…$$` blocks render via KaTeX (stylesheet from CDN, only linked when math is present).
