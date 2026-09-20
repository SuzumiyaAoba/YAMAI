# Code investigation components

Index: [../components.md](../components.md). MDX attributes are always strings; `children` is Markdown.

### `<Findings title>` / `<Finding confidence="confirmed|inferred|unverified" title="…">` / `:::findings` / `:::finding`

Investigation findings with an epistemic-status pill: `confirmed` (read from the code), `inferred` (deduced from evidence), `unverified` (claimed, not checked). `Findings` numbers each `Finding` and renders a count summary on top; children carry the evidence (`<FileRef>`, `<CodeFile>`).

```mdx
<Findings title="Investigation results">
  <Finding confidence="confirmed" title="Rendering is synchronous">
    `mdxToHtml` awaits `evaluate()` then calls `renderToStaticMarkup`.
  </Finding>
  <Finding confidence="unverified" title="Watch-mode reloads">
    Probably, but no test covers it.
  </Finding>
</Findings>
```

### `<Files title>` / `<File path kind lines>` / `:::files`

Related-file inventory — "the files this investigation touches". The row icon is picked automatically from the file name/extension (`vscode-icons` set: `package.json` → npm, `Dockerfile` → Docker, `*.test.ts` → test TS, …). `kind` is a free-form chip; known values get an icon and color: `entry` `core` `types` `config` `test` `docs` `generated`. Children render as a muted note. For change-sets (what a plan modifies) use `Changes` instead. `path` links to the file in the reader's editor when it exists on disk (`href` overrides; see "File links" in [../components.md](../components.md)).

```mdx
<Files title="Files involved">
  <File path="src/mdx.ts" kind="entry" lines="70-106">
    Pipeline entry.
  </File>
  <File path="src/define.ts" kind="types" />
  <File path="tests/render.test.ts" kind="test" />
</Files>
```

### `<Deps title>` / `<Dep from to kind>` / `:::deps`

Dependency-edge list — compact alternative to a mermaid graph for module relationships. `kind` (default `imports`): `imports` `calls` `extends` `implements` `reads` `writes`. `from`/`to` accept paths, module names, or symbols; children render as a muted note.

```mdx
<Deps title="Module dependencies">
  <Dep from="src/cli.ts" to="src/render.ts" kind="calls" />
  <Dep from="src/mdx.ts" to="remark-directive" kind="imports" />
  <Dep from="src/render.ts" to="dist/out.html" kind="writes" />
</Deps>
```

### `<Changes>` / `<Change kind="add|modify|delete|rename" path="…" to="…">`

Change-set list — the "files this plan touches" section. `kind` drives the icon and color (`to` is the new path on `rename`); the file path also gets an extension-based icon automatically. Children render as a muted note. The path links to the file in the reader's editor when it exists (`to` wins on `rename`; `href` overrides).

```mdx
<Changes>
  <Change kind="add" path="src/remark/headings.ts">
    slug + toc expansion
  </Change>
  <Change kind="modify" path="src/mdx.ts" />
  <Change kind="rename" path="src/old.ts" to="src/new.ts" />
  <Change kind="delete" path="src/dead.ts" />
</Changes>
```

### `<Flow title="…">` / `<FlowStep name path lines>` / `:::flow{title="…"}`

Numbered call/execution chain — "how a request travels through the code". Each `FlowStep` renders a numbered node on a connecting rail; `name` is the function/phase label (mono), `path`/`lines` pin the location (linked to the file in the reader's editor when it exists; `href` overrides), children describe what happens there. Use `Steps` for task checklists and `Timeline` for dates — `Flow` is for hops through code.

```mdx
<Flow title="Request path">
  <FlowStep name="cli()" path="src/cli.ts" lines="12-30">
    Parses argv, loads config.
  </FlowStep>
  <FlowStep name="mdxToHtml()" path="src/mdx.ts">
    Compiles MDX and evaluates it.
  </FlowStep>
</Flow>
```

### `<Tree root="…">`

File tree rendered from a nested Markdown list. Items ending in `/` or with children get a folder icon; `name — note` or `name # note` adds a muted note. Icons are picked automatically from the file extension or directory name (VS Code-style `vscode-icons` set, e.g. `.ts` → TypeScript logo, `src/` → src folder).

Starlight-style extras: directories are collapsible (click to fold; works without JS), `open="false"` starts every folder collapsed, a `...` or `…` entry renders a placeholder for omitted files, and a `**bold**` name highlights the entry.

```mdx
<Tree root="mdxr/">

- src/
  - render.ts — pipeline entry
  - ui/
    - **plan.tsx**
    - steps.tsx
    - …
- package.json
- ...

</Tree>
```

### `<FileRef path="src/mdx.ts" lines="40-52" />`

Inline file reference chip with a copy button. The icon is picked automatically from the file extension (`vscode-icons` set). Links to the file in the reader's editor (`vscode://file/…` by default) when it exists on disk; `href` overrides the URL, `editor: none` disables linking.

### `<SymbolRef name="mdxToHtml" kind="fn" path="src/mdx.ts" lines="70-106" />`

Inline symbol reference chip for code explanations. `kind` picks the icon: `fn` `type` `class` `interface` `const` `enum` `prop` `component` (omit for no icon). `path`/`lines` show the definition site muted — the chip links to that file in the reader's editor when it exists (`href` overrides); the copy button copies `path` (or `name` when absent).

```mdx
<SymbolRef name="mdxToHtml" kind="fn" path="src/mdx.ts" /> returns
<SymbolRef name="MdxResult" kind="interface" />.
```

### `<CodeFile path="src/x.ts" lines="40-52" lang="ts" />`

Embeds a real file from disk as a fenced block — code explanations quote the actual source instead of drifting copies. `path` resolves relative to the document; `lines` slices a 1-based range (`"40"`, `"40-52"`, `"40-"`); `lang` overrides the extension-derived language. The filename header links back to the file in the reader's editor. Missing files and bad ranges are render errors.

### `<Props of="…">` / `<Prop name type required default>`

API/props table for documenting a component or function signature. `of` renders a caption bar; `required` (bare attr) adds a `*`; children are the description cell.

```mdx
<Props of="Step">
  <Prop name="status" type="todo | doing | done | blocked" required>
    Marker state.
  </Prop>
  <Prop name="effort" type="xs | s | m | l | xl" default="m" />
</Props>
```

### `<Terminal cmd exit title>` / `:::terminal` / ` ```console `

Terminal transcript — the standard evidence block for "what was run and what it printed". `cmd` renders as a `$ `-prompted first line; `exit` adds a title-bar badge (`0` green, anything else red). Children carry the output: put it in a fenced block for verbatim text — plain-text children are parsed as Markdown, which can mangle `_`, `*` etc. Body lines starting with `$ ` render as prompt lines, so multi-command sessions work too. The copy button copies the whole transcript.

````mdx
<Terminal cmd="pnpm test" exit="1" title="test run">

```
FAIL  tests/render.test.ts
  ✗ renders markdown prose
```

</Terminal>
````

A ` ```console ` (or ` ```terminal ` / ` ```shellsession `) fence renders the same transcript with no component — `exit="N"` and `title="…"` in the fence meta work as well:

````
```console exit="1" title="grep evidence"
$ rg 'evaluate' src/ --count
src/mdx.ts: 3
```
````

### `<Hypotheses title>` / `<Hypothesis status="supported|refuted|untested" title="…">` / `:::hypotheses` / `:::hypothesis`

Hypothesis ledger — the "what we suspected, and whether it held up" section of a debugging-style investigation. `supported` = evidence backs it, `refuted` = ruled out (record dead ends — they save the reader from re-checking), `untested` = not yet checked. `Hypotheses` numbers each entry and summarizes per-status counts; children carry the reasoning and evidence (`<Terminal>`, `<FileRef>`).

```mdx
<Hypotheses title="Hypotheses tested">
  <Hypothesis status="refuted" title="CSS loads at runtime">
    Ruled out: the stylesheet is inlined into the HTML — see the
    <Terminal cmd="grep stylesheet dist/out.html">…</Terminal>
  </Hypothesis>
  <Hypothesis status="supported" title="Directives expand before eval" />
</Hypotheses>
```

### `<Trace title error>` / `<TraceFrame name path lines kind="app|lib">` / `:::trace`

Stack/call trace for error and crash investigations. `error` renders the exception line on top (red); `TraceFrame`s are auto-numbered `#0…` top-down (most recent first, like `gdb bt`). `kind="lib"` dims framework/runtime frames and adds a `lib` tag. `path` links to the file in the reader's editor when it exists (`href` overrides). Children are per-frame notes.

```mdx
<Trace error="TypeError: Cannot read properties of undefined (reading 'kind')">
  <TraceFrame name="toMdxComponent" path="src/remark/directives.ts" lines="64">
    leaf directives have no attributes record
  </TraceFrame>
  <TraceFrame
    name="visit"
    path="node_modules/unist-util-visit/index.js"
    kind="lib"
  />
  <TraceFrame
    name="remarkMdxrDirectives"
    path="src/remark/directives.ts"
    lines="91"
  />
</Trace>
```

### `<Searches title>` / `<Search pattern path tool hits>` / `:::searches` / `:::search`

Query log — provenance for an investigation: which patterns were searched where and how much came back. `pattern` is required; `path` is the scope (`in src/`), `tool` a small mono chip (`rg`, `grep`, …), `hits` a count badge — `0` renders "no hits" so dead ends read as deliberate exclusions. `Searches` adds a totals line; children are notes.

```mdx
<Searches title="How the code was searched">
  <Search pattern="evaluate" path="src/" tool="rg" hits="3" />
  <Search pattern="hydrateRoot" path="src/" tool="rg" hits="0">
    Dead end — there is no client entry point.
  </Search>
</Searches>
```
