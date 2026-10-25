import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from breakout.cli import parser
from breakout.probe import probe
from breakout.providers import CLIAdapter


class EffortTests(unittest.TestCase):
    def test_medium_is_explicit_on_fork_and_resume(self):
        for provider in ('codex', 'claude'):
            for child in (None, 'child-id'):
                participant = dict(provider=provider, model='selected-model', session_id='parent-id',
                                   child_id=child, effort='medium')
                command = CLIAdapter().command(participant)
                if provider == 'codex':
                    self.assertIn('model_reasoning_effort="medium"', command)
                else:
                    self.assertEqual(command[command.index('--effort')+1], 'medium')

    def test_probe_preserves_effort_on_every_turn_and_report(self):
        for provider in ('codex', 'claude'):
            with tempfile.TemporaryDirectory() as home:
                commands = []

                def transport(command, text, cwd, timeout=90):
                    commands.append(command)
                    return ''

                with patch('breakout.probe.run_command', side_effect=transport), \
                        patch('breakout.probe.parse_text', side_effect=[('parent',''), ('child',''),
                              ('child',''), ('child',''), ('parent','')]), \
                        patch.object(CLIAdapter, 'codex_restrictions', return_value=[]):
                    report = probe(provider, 'selected-model', home, effort='medium')
                self.assertEqual(len(commands), 5)
                for command in commands:
                    if provider == 'codex':
                        self.assertIn('model_reasoning_effort="medium"', command)
                    else:
                        self.assertEqual(command[command.index('--effort')+1], 'medium')
                self.assertEqual(json.loads(Path(report['report_path']).read_text())['effort'], 'medium')

    def test_no_effort_override_when_unspecified(self):
        for provider in ('codex', 'claude'):
            self.assertEqual(CLIAdapter.effort_flags(provider, None), [])

    def test_reject_unsupported_effort(self):
        with self.assertRaises(ValueError):
            CLIAdapter.effort_flags('claude', 'ultra')
        with self.assertRaises(ValueError):
            CLIAdapter.effort_flags('codex', 'made-up')

    def test_start_parses_separate_provider_efforts(self):
        args = parser().parse_args(['start', '--document', 'rfc.txt', '--task', 'review',
                                    '--codex-effort', 'medium', '--claude-effort', 'medium'])
        self.assertEqual(args.codex_effort, 'medium')
        self.assertEqual(args.claude_effort, 'medium')
