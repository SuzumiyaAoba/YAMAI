#!/usr/bin/env python3
"""Render tracked Markdown and optional local MDX documentation through mdxr."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = {
    "docs/README.md": "docs/index.html",
    "docs/yamai-protocol.md": "docs/yamai-protocol.html",
    "docs/artifacts.md": "docs/artifacts.html",
    "docs/auth-implementation-plan.mdx": "docs/auth-implementation-plan.html",
    "docs/spec-review.mdx": "docs/spec-review.html",
    "verification/README.md": "verification/index.html",
    "verification/quint/README.md": "verification/quint/index.html",
}
MDXR_VERSION = "0.2.0"
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)")


def to_mdx(source: Path, output: Path, version: str) -> str:
    text = source.read_text(encoding="utf-8")
    native_mdx = source.suffix == ".mdx"
    title, _, body = ("", "", text) if native_mdx else text.partition("\n")
    body = re.sub(r"\n## 目次\n.*?(?=\n## 1\.)", "\n", body, flags=re.S)
    rendered_paths = {(ROOT / name).resolve(): ROOT / target for name, target in DOCUMENTS.items()}

    def link(match: re.Match) -> str:
        prefix, target, fragment, suffix = match.groups()
        if ":" in target or target.startswith("/"):
            return match[0]
        resolved = (source.parent / target).resolve()
        destination = rendered_paths.get(resolved, resolved)
        relative = Path(os.path.relpath(destination, output.parent)).as_posix()
        return prefix + relative + (fragment or "") + suffix

    lines = []
    fence = None
    for line in body.splitlines():
        marker = re.match(r"\s*(`{3,}|~{3,})(.*)$", line)
        if marker:
            delimiter = marker[1]
            if fence is None and (delimiter[0] != "`" or "`" not in marker[2]):
                fence = delimiter
            elif fence is not None and delimiter[0] == fence[0] and len(delimiter) >= len(fence) and not marker[2].strip():
                fence = None
            lines.append(line)
            continue
        if fence:
            lines.append(line)
            continue

        spans = list(INLINE_CODE.finditer(line))
        line = re.sub(r"(\[[^\]]+\]\()([^\s)#]+)(#[^)]*)?(\))",
                      lambda match: match[0] if any(span.start() <= match.start() < span.end() for span in spans) else link(match), line)
        if native_mdx:
            lines.append(line)
            continue
        # Keep inline code literal; escape prose that MDX would parse as JSX/JS.
        parts, cursor = [], 0
        for span in INLINE_CODE.finditer(line):
            prose = line[cursor:span.start()]
            parts.extend([re.sub(r"(?<!\\)([{}])", r"\\\1", prose).replace("<", "&lt;"), span[0]])
            cursor = span.end()
        parts.append(re.sub(r"(?<!\\)([{}])", r"\\\1", line[cursor:]).replace("<", "&lt;"))
        lines.append("".join(parts))

    header = "\n".join((
        "---",
        "title: " + json.dumps(title.removeprefix("# "), ensure_ascii=False),
        "version: " + json.dumps(version),
        'editor: "none"',
        "---",
        "",
        '<Toc depth="3" min="2" title="目次" />',
        "",
    ))
    return ("" if native_mdx else header) + "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mdxr", help="Path to a locally installed mdxr executable")
    args = parser.parse_args()
    renderer = [args.mdxr] if args.mdxr else ["npx", "--yes", f"@suzumiyaaoba/mdxr@{MDXR_VERSION}"]
    release = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
    for name, destination in DOCUMENTS.items():
        source, output = ROOT / name, ROOT / destination
        if source.suffix == ".mdx" and not source.is_file():
            continue
        intermediate = ROOT / ".mdxr" / "render" / Path(name).with_suffix(".mdx")
        intermediate.parent.mkdir(parents=True, exist_ok=True)
        intermediate.write_text(to_mdx(source, output, release["protocol"]["version"]), encoding="utf-8")
        subprocess.run([*renderer, "render", str(intermediate), "-o", str(output)], cwd=ROOT, check=True)
        print(destination, flush=True)


if __name__ == "__main__":
    main()
