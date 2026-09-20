# shadcn/ui components (Base UI)

Index: [../components.md](../components.md). MDX attributes are always strings; `children` is Markdown.

The full shadcn/ui set (Base UI primitives) is registered: `Button`, `Badge`, `Card`/`CardHeader`/…, `Alert`, `Tabs`, `Accordion`, `Dialog`, `Input`, `Label`, `Table`, `Progress`, `Skeleton`, `Separator`, `Kbd`, `Spinner`, and more — run `mdxr catalog` for the complete list. Use them as plain MDX elements; attributes are strings (`variant="outline"`, `size="sm"`).

**Note:** rendered documents include a hydration bundle, so stateful primitives (`Dialog`, `Tabs`, `Accordion`, `Tooltip`, `Select`, `Switch`, menus, …) are interactive in the browser — tabs switch, accordions open, switches flip. `mdxr render --no-hydrate` emits purely static HTML where they render their initial state only. Portal-based overlays (`Dialog`, `Tooltip`, `Select`, menus) still render nothing until opened — prefer `Card`, `Alert`, `Badge`, `Table`, `Kbd`, `Separator`, `Progress`, `Skeleton` for always-visible content.

```mdx
<Alert>
  <AlertTitle>Heads up</AlertTitle>
  <AlertDescription>Fully static and safe in documents.</AlertDescription>
</Alert>

<Badge variant="secondary">beta</Badge>
<Button variant="outline">Action</Button>
```

Theme: shadcn CSS variables (`--primary`, `--background`, …) are emitted with the document CSS; `.dark` variants follow `prefers-color-scheme` via a `<html>` class toggle.
