# Reader input components

Index: [../components.md](../components.md). MDX attributes are always strings; `children` is Markdown.

### `<Ask title description>` / `<Question name type label>` / `<Choice value checked>`

Question block that asks the reader for input — open decisions in a plan, sign-off toggles, free-form answers. Built on **native** form controls (unlike the shadcn set), so every field is interactive in the static document. Answers render live as a Markdown sheet in the block's output pane — `# title` then `- **label**: answer` per question — which "Copy answers" copies to the clipboard and "Save .md" downloads as a file the user can keep or paste back.

`<Question>` `type`: `choice` (radio cards), `multi` (checkbox cards), `select` (dropdown), `text`, `textarea`, `toggle` (switch). Default: `choice` when it has `<Choice>` children, else `text`. `name` is the answer key; `label`, `description`, `required`, `placeholder`, `value` (text default), `rows` (textarea), `checked` (toggle) are supported.

```mdx
<Ask title="確認事項" description="プランに反映します">
  <Question name="approach" type="choice" label="実装方針" required>
    <Choice value="gradual" checked>
      段階的移行
    </Choice>
    <Choice value="rewrite" description="ロールバック経路が必要">
      一括書き換え
    </Choice>
  </Question>
  <Question name="scope" type="multi" label="含める範囲">
    <Choice value="api" checked>
      API
    </Choice>
    <Choice value="ui">UI</Choice>
  </Question>
  <Question name="prio" type="select" label="優先度" placeholder="選択">
    <Choice value="high">高</Choice>
    <Choice value="mid">中</Choice>
  </Question>
  <Question name="deadline" type="text" label="期限" placeholder="YYYY-MM-DD" />
  <Question name="notes" type="textarea" label="補足" rows="2" />
  <Question name="preview" type="toggle" label="プレビュー環境を作る" checked />
</Ask>
```
