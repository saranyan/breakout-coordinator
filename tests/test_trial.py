import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from breakout.trial import CHECKS, select_fixtures, trial


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        for provider, model in [('codex', 'gpt-6.1-sol'), ('claude', 'claude-opus-5-5')]:
            path = self.home / 'probes' / provider / 'report.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(provider=provider, model=model, effort='medium', status='passed',
                                             checks={c: True for c in CHECKS}, finished=1,
                                             parent_id=provider + '-parent', child_id=provider + '-child',
                                             project=str(self.home))))

    def tearDown(self):
        self.temp.cleanup()

    def test_selects_verified_parents_not_children(self):
        selected = select_fixtures(self.home, {'codex': 'gpt-6.1-sol', 'claude': 'claude-opus-5-5'}, 'medium')
        self.assertEqual([p['session_id'] for p in selected], ['codex-parent', 'claude-parent'])
        self.assertTrue(all(p['effort'] == 'medium' for p in selected))

    def test_never_substitutes_model_or_effort(self):
        with self.assertRaisesRegex(ValueError, 'No passed'):
            select_fixtures(self.home, {'codex': 'different-model', 'claude': 'claude-opus-5-5'}, 'medium')
        with self.assertRaisesRegex(ValueError, 'No passed'):
            select_fixtures(self.home, {'codex': 'gpt-6.1-sol', 'claude': 'claude-opus-5-5'}, 'high')

    def test_trial_routes_through_daemon_and_saves_results_without_ack(self):
        from breakout.daemon import Service
        from breakout.engine import Engine
        from breakout.providers import FakeAdapter
        from breakout.store import Store
        store = Store(self.home / 'mock-daemon')
        service = Service(Engine(store, adapters={'live': FakeAdapter()}))
        methods = []

        def rpc(method, home=None, timeout=10, **params):
            methods.append(method)
            if method == 'get':
                service.engine.step(params['run_id'])
            return service.dispatch(method, params)

        document = self.home / 'rfc.txt'
        document.write_text('A local review proposal')
        try:
            with patch('breakout.trial.request', side_effect=rpc), patch('breakout.trial.subprocess.Popen') as popen, \
                    patch('breakout.trial.time.sleep'):
                popen.return_value.poll.return_value = None
                result = trial(self.home, str(document))
            self.assertEqual(result['status'], 'ready for review')
            self.assertEqual(result['turns'], 3)
            self.assertTrue(Path(result['result_path']).is_file())
            self.assertTrue(Path(result['transcript_path']).is_file())
            self.assertEqual(len(result['deliveries']), 2)
            self.assertNotIn('acknowledge', methods)
            self.assertIn('shutdown', methods)
            run = store.get(result['run_id'])
            self.assertEqual(run['max_turns'], 4)
        finally:
            store.db.close()
