import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from breakout.cli import main
from breakout.daemon import Service
from breakout.engine import Engine
from breakout.providers import FakeAdapter
from breakout.store import Store


class V2IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'state')
        self.addCleanup(self.store.db.close)
        self.engine = Engine(self.store)
        self.service = Service(self.engine)
        self.store.register(dict(provider='codex', session_id='origin', project=str(self.root), title='Work'))

    def cli(self, args):
        output = io.StringIO()
        def request(method, home=None, **kw):
            kw.pop('timeout', None)
            return self.service.dispatch(method, kw)
        with patch('breakout.cli.request', side_effect=request), patch('breakout.setup.ensure', return_value={'protocol_version': 2}), patch('sys.stdout', output):
            main(args)
        return output.getvalue()

    def complete(self, run_id):
        for _ in range(20):
            if not self.engine.step(run_id):
                break
        return self.store.get(run_id)

    def launch(self):
        return json.loads(self.cli(['launch', '--from-provider', 'codex', '--from-session', 'origin',
                                   '--config', 'examples/access-map.toml', '--team', 'rfc', '--task', 'Review',
                                   '--context', 'User decisions and relevant history', '--issue', 'Scope', '--simulate']))

    def test_cli_recipe_through_review_and_archive(self):
        run = self.launch()
        self.complete(run['id'])
        self.cli(['ack', '--provider', 'codex', '--session', 'origin', run['id'], '1'])
        self.cli(['ask', run['id'], 'Why?', '--to', 'Sol', '--provider', 'codex', '--session', 'origin'])
        self.complete(run['id'])
        result = self.cli(['result', run['id']])
        self.assertIn('Simulated answer from Sol', result)
        self.assertIn('Alternatives considered', result)
        self.assertEqual(len(json.loads(self.cli(['open', '--provider', 'codex', '--session', 'origin']))), 1)
        self.cli(['close', run['id'], '--provider', 'codex', '--session', 'origin'])
        self.assertEqual(json.loads(self.cli(['open', '--provider', 'codex', '--session', 'origin'])), [])

    def test_closed_run_can_seed_linked_followup(self):
        run = self.launch()
        self.complete(run['id'])
        self.engine.review_control(run['id'], 'close')
        new = self.engine.launch('codex', 'origin', [dict(provider='claude', model='fake', name='A'),
                                                   dict(provider='codex', model='fake', name='B')],
                                 'Follow up', issues=['Scope'], simulate=True, parent_run=run['id'])
        self.assertEqual(new['parent_run'], run['id'])
        self.assertIn('Previous run:', new['shared_context'])
        self.assertNotEqual(new['participants'][0]['child_id'], self.store.get(run['id'])['participants'][0]['child_id'])

    def test_close_during_review_does_not_apply_late_answer_or_reopen(self):
        run = self.launch()
        self.complete(run['id'])
        self.engine.review_control(run['id'], 'ask', text='Why?', target='Sol')
        started, release = threading.Event(), threading.Event()
        fake = FakeAdapter()
        class Slow:
            def turn(self, p, c, cancel, timeout):
                started.set()
                release.wait(3)
                return fake.turn(p, c, threading.Event(), timeout)
        self.engine.adapters['fake'] = Slow()
        thread = threading.Thread(target=self.engine.step, args=(run['id'],))
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            closed = self.engine.review_control(run['id'], 'close')
        finally:
            release.set()
            thread.join(3)
        after = self.store.get(run['id'])
        self.assertEqual(after['status'], 'closed')
        self.assertEqual(after['result_revision'], closed['result_revision'])
        self.assertTrue(after['messages'][-1]['stale'])

    def test_uncertain_review_does_not_repeat_on_restart(self):
        run = self.launch()
        self.complete(run['id'])
        self.engine.review_control(run['id'], 'ask', text='Why?', target='Sol', event_id='one-question')
        class Failure:
            def turn(self, *args):
                raise ConnectionError('May have been delivered')
        self.engine.adapters['fake'] = Failure()
        self.engine.step(run['id'])
        recovered = Engine(self.store)
        recovered.recover()
        self.assertFalse(recovered.step(run['id']))
        with self.assertRaisesRegex(ValueError, 'Reconcile'):
            recovered.control(run['id'], 'resume')
        attempt = self.store.db.execute("SELECT id FROM attempts WHERE status='uncertain'").fetchone()[0]
        recovered.reconcile(attempt, not_started=True)
        recovered.control(run['id'], 'resume')
        recovered.step(run['id'])
        self.assertEqual(self.store.get(run['id'])['review_replies'], 1)

    def test_host_upgrade_never_stops_a_busy_or_unwritable_service(self):
        from breakout.setup import ensure
        with patch('breakout.setup.request') as request:
            request.side_effect = [{'status': 'running', 'busy': True}, []]
            with self.assertRaisesRegex(ValueError, 'active or paused'):
                ensure(self.root)
            self.assertEqual([x.args[0] for x in request.call_args_list], ['ping', 'list'])
        with patch('breakout.setup.request') as request, patch('breakout.setup.tempfile.TemporaryFile', side_effect=PermissionError('Denied')):
            request.side_effect = [{'status': 'running'}, []]
            with self.assertRaises(PermissionError):
                ensure(self.root)
            self.assertEqual([x.args[0] for x in request.call_args_list], ['ping', 'list'])

    def test_daemon_start_does_not_spawn_after_upgrade_refusal(self):
        with patch('breakout.cli.request', return_value={'status': 'running'}), \
                patch('breakout.setup.ensure', side_effect=ValueError('Busy; finish first')), \
                patch('breakout.cli.subprocess.Popen') as spawn, patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                main(['daemon', 'start'])
            spawn.assert_not_called()

    def test_fake_adapter_contract_all_origin_pairs(self):
        for origin in ('codex', 'claude'):
            self.store.register(dict(provider=origin, session_id='source-'+origin, project=str(self.root), title='Work'))
            for providers in [('codex', 'claude'), (origin, origin)]:
                run = self.engine.launch(origin, 'source-'+origin,
                    [dict(provider=p, model='fake', name=f'Member{i}') for i,p in enumerate(providers)],
                    'Review', shared_context='Important decision: Blue Finch', issues=['Scope'], simulate=True)
                done = self.complete(run['id'])
                self.assertFalse(done['result']['partial'])
                self.assertTrue(all(p['child_id'] != p['session_id'] for p in done['participants']))
                self.engine.acknowledge(run['id'], 1, origin, 'source-'+origin)
                self.assertEqual(self.engine.mailbox(origin, 'source-'+origin), [])


if __name__ == '__main__':
    unittest.main()
