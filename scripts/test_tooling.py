"""End-to-end boundaries for the scoring CLI and documentation conversion."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import render_docs
import validate_artifacts as v


class ScoreOracleCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = v.strict_load(v.ROOT / 'test-vectors/riichi-4p/1.0-draft.1/scoring.json')
        cls.data = {**data, 'fixtures': data['fixtures'][:1], 'negative_fixtures': []}

    def run_oracle(self, *args):
        return subprocess.run([sys.executable, str(v.ROOT / 'scripts/score_oracle.py'), *args],
                              cwd=v.ROOT, text=True, capture_output=True)

    def test_external_input_prints_machine_readable_json(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'scoring.json'
            path.write_text(json.dumps(self.data), encoding='utf-8')
            result = self.run_oracle(str(path), '--print')
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = {f['id']: f['expected'] for f in self.data['fixtures']}
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertIn('1 positive', result.stderr)

    def test_duplicate_fixture_ids_are_rejected(self):
        data = deepcopy(self.data)
        data['fixtures'] *= 2
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicates.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            result = self.run_oracle(str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn('duplicate fixture id', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_missing_input_is_a_cli_error(self):
        with TemporaryDirectory() as directory:
            result = self.run_oracle(str(Path(directory) / 'missing.json'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('score oracle:', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


class DocumentConversion(unittest.TestCase):
    def convert(self, text, suffix='.md'):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'docs' / 'input' / ('source' + suffix)
            source.parent.mkdir(parents=True)
            source.write_text(text, encoding='utf-8')
            with patch.object(render_docs, 'ROOT', root):
                return render_docs.to_mdx(source, source.with_suffix('.html'), v.PROTOCOL)

    def test_link_examples_stay_literal_inside_inline_and_fenced_code(self):
        link = '[artifact](../artifacts.md)'
        result = self.convert('# Title\n' + link + '\n`' + link + '`\n```md\n' + link + '\n```\n')
        self.assertIn('[artifact](../artifacts.html)', result)
        self.assertIn('`' + link + '`', result)
        self.assertIn('```md\n' + link + '\n```', result)

    def test_only_bare_matching_fence_closes_code(self):
        text = '# Title\n```md\n```python\n{example}\n```\n{prose}\n'
        result = self.convert(text)
        self.assertIn('```python\n{example}\n```', result)
        self.assertIn('\\{prose\\}', result)

    def test_single_line_and_empty_markdown_render(self):
        self.assertIn('title: "Title"', self.convert('# Title'))
        self.assertIn('title:', self.convert(''))


if __name__ == '__main__':
    unittest.main()
