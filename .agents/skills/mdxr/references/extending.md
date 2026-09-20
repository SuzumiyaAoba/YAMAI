# Project-defined components

Index: [components.md](components.md).

Create `mdxr.config.ts` in the project root:

```ts
import { defineConfig } from "@suzumiyaaoba/mdxr";

export default defineConfig({
  components: "./components/index.tsx", // named exports become MDX components
  theme: "./mdxr.css", // optional: @theme token overrides
  editor: "vscode", // optional: file-link target — see below
});
```

`editor` sets the URL scheme for file links (`path`-carrying components and code-block filename headers): `vscode` (default), `cursor`, `zed`, `vscode-insiders`, `windsurf`, `sublime`, `textmate`, `idea`, a custom template like `"myed://open?f={path}&l={line}"`, or `"none"` to disable. Frontmatter `editor:` overrides it per document.

Define components with `defineComponent` (adds a valibot schema — used for runtime validation **and** `mdxr catalog` documentation):

```tsx
// components/index.tsx
import { defineComponent, v } from "@suzumiyaaoba/mdxr";

export const LinkCard = defineComponent(
  {
    description: "External reference card",
    schema: v.looseObject({ href: v.string(), title: v.string() }),
  },
  ({ href, title, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="block rounded-lg border p-3 no-underline"
    >
      <div className="font-medium">{title} ↗</div>
      {children}
    </a>
  )
);
```

- Tailwind classes in custom components are compiled automatically.
- `import { Callout, StatusBadge } from '@suzumiyaaoba/mdxr/components'` to compose built-ins.
- A project component with the same name as a built-in overrides it (a warning is printed).
- Project components join the hydration bundle — hooks (`useState`, `useContext`, …) work, so they can be interactive in the rendered HTML (e.g. a counter). With `--no-hydrate` they render initial state only.
- Components must still be synchronous — no Suspense / data fetching.
