import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from breakout.daemon import Service
from breakout.documents import snapshot
from breakout.engine import Engine
from breakout.integration import hook, install, uninstall
from breakout.protocol import validate
from breakout.providers import CLIAdapter
from breakout.store import Store


ENVELOPE = dict(message='Review', issues=[dict(id='I1', title='Scope', status='needs evidence',
                resolution='Run an experiment', objection='', evidence=['snapshot:1'])],
                proposal='', reviewed_revision='', questions=[])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_install_uninstall_preserves_other_hooks_and_settings(self):
        for provider, filename in [('codex', 'hooks.json'), ('claude', 'settings.json')]:
            config = self.root / provider
            config.mkdir()
            existing = {'unrelated': 'keep', 'hooks': {'SessionStart': [{'hooks': [
                {'type': 'command', 'command': 'echo existing'}]}]}}
            (config / filename).write_text(json.dumps(existing))
            install(provider, config, self.root / 'state')
            installed = json.loads((config / filename).read_text())
            self.assertEqual(len(installed['hooks']['SessionStart']), 2)
            install(provider, config, self.root / 'state')
            self.assertEqual(installed, json.loads((config / filename).read_text()))
            installed['added_later'] = True
            (config / filename).write_text(json.dumps(installed))
            uninstall(provider, config)
            self.assertEqual(json.loads((config / filename).read_text()), dict(existing, added_later=True))
            self.assertFalse((config / 'skills/breakout/SKILL.md').exists())

    def test_modified_skill_is_preserved(self):
        config = self.root / 'claude'
        install('claude', config, self.root / 'state')
        skill = config / 'skills/breakout/SKILL.md'
        skill.write_text('User changes')
        result = uninstall('claude', config)
        self.assertTrue(result['modified_skill_preserved'])
        self.assertEqual(skill.read_text(), 'User changes')

    def test_existing_skill_is_not_overwritten(self):
        skill = self.root / 'skills/breakout/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('Existing skill')
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            install('codex', self.root, self.root / 'state')
        self.assertEqual(skill.read_text(), 'Existing skill')

    def test_child_hook_is_noop(self):
        with patch.dict(os.environ, {'BREAKOUT_CHILD': '1'}), patch('breakout.integration.request') as request:
            hook('codex', self.root)
            request.assert_not_called()

    def test_hook_queues_notice_without_acknowledging(self):
        event = json.dumps({'session_id': 'parent', 'cwd': str(self.root),
                            'hook_event_name': 'UserPromptSubmit', 'prompt': 'private conversation'})
        output = io.StringIO()
        with patch.dict(os.environ, {'BREAKOUT_CHILD': '0'}), patch('sys.stdin', io.StringIO(event)), \
                patch('sys.stdout', output), patch('breakout.integration.request') as request:
            request.side_effect = [{}, [{'run_id': 'run', 'revision': 1}]]
            hook('codex', self.root)
            self.assertEqual([c.args[0] for c in request.call_args_list], ['register', 'mailbox'])
            self.assertNotIn('prompt', request.call_args_list[0].kwargs)
        result = json.loads(output.getvalue())
        self.assertEqual(result['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        self.assertIn('run', result['hookSpecificOutput']['additionalContext'])

    def test_stopped_daemon_does_not_break_hook(self):
        with patch.dict(os.environ, {'BREAKOUT_CHILD': '0'}), patch('sys.stdin', io.StringIO(
                json.dumps({'session_id': 'parent', 'cwd': str(self.root)}))), \
                patch('breakout.integration.request', side_effect=ConnectionRefusedError):
            hook('claude', self.root)

    def test_native_commands_fork_then_resume_same_child(self):
        adapter = CLIAdapter()
        for provider in ['codex', 'claude']:
            p = dict(provider=provider, model='chosen-model', session_id='parent', project=str(self.root))
            first = adapter.command(p)
            self.assertIn('chosen-model', first)
            self.assertIn('parent', first)
            self.assertIn('fork' if provider == 'codex' else '--fork-session', first)
            second = adapter.command(dict(p, child_id='child'))
            self.assertIn('child', second)
            self.assertNotIn('parent', second)
            self.assertNotIn('fork' if provider == 'codex' else '--fork-session', second)
            self.assertFalse(any('dangerously' in arg for arg in first + second))

    def test_provider_response_formats(self):
        claude = json.dumps({'session_id': 'child', 'is_error': False, 'result': json.dumps(ENVELOPE)})
        self.assertEqual(CLIAdapter.parse('claude', claude), ('child', ENVELOPE))
        codex = '\n'.join(json.dumps(e) for e in [
            {'type': 'thread.started', 'thread_id': 'child'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(ENVELOPE)}},
            {'type': 'turn.completed', 'usage': {}}])
        self.assertEqual(CLIAdapter.parse('codex', codex), ('child', ENVELOPE))
        with self.assertRaises(ValueError):
            CLIAdapter.parse('codex', codex.rsplit('\n', 1)[0])
        with self.assertRaises(ValueError):
            CLIAdapter.parse('claude', json.dumps({'is_error': True}))

    def test_malformed_envelope_cannot_claim_done(self):
        with self.assertRaises(ValueError):
            validate({'message': 'done'})
        with self.assertRaises(ValueError):
            validate(dict(ENVELOPE, issues=[dict(ENVELOPE['issues'][0], status='done')]))

    def test_private_document_requires_complete_export(self):
        doc = self.root / 'snapshot.txt'
        doc.write_text('Page and all comments')
        source = 'https://app.notion.com/p/abc'
        with self.assertRaisesRegex(ValueError, 'Export'):
            snapshot(source)
        with self.assertRaisesRegex(ValueError, 'comments'):
            snapshot(source, str(doc))
        result = snapshot(source, str(doc), 'revision-1', True)
        self.assertEqual(result['source_revision'], 'revision-1')
        self.assertTrue(result['comments_complete'])

    def test_dispatch_work_and_mailbox_end_to_end(self):
        store = Store(self.root / 'state')
        service = Service(Engine(store))
        doc = self.root / 'rfc.txt'
        doc.write_text('Review this document')
        participants = [dict(provider=p, session_id=p, model='fake', project=str(self.root)) for p in ('codex', 'claude')]
        run = service.dispatch('create', dict(document=snapshot(str(doc)), task='Review',
                                              participants=participants, issues=['Scope'], simulate=True))
        for _ in range(4):
            service.engine.step(run['id'])
        received = service.dispatch('get', dict(run_id=run['id']))
        self.assertEqual(received['status'], 'ready for review')
        self.assertEqual(len(received['deliveries']), 2)
        self.assertTrue(all(a['status'] == 'applied' for a in received['attempts']))
        self.assertEqual((store.home.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((store.home / 'state.sqlite3').stat().st_mode & 0o777), 0o600)
        store.db.close()

    def test_cli_demo_runs_without_provider_or_socket(self):
        doc = self.root / 'rfc.txt'
        doc.write_text('Acceptance criteria')
        result = subprocess.run([sys.executable, '-m', 'breakout', 'demo', '--document', str(doc)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ready for review', result.stdout)
        self.assertIn('needs evidence', result.stdout)
        self.assertIn('needs user', result.stdout)


if __name__ == '__main__':
    unittest.main()
