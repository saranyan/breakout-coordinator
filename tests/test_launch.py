import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from breakout.engine import Engine
from breakout.integration import install
from breakout.protocol import digest
from breakout.providers import CLIAdapter, FakeAdapter
from breakout.store import Store


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'state')
        self.engine = Engine(self.store)
        self.store.register(dict(provider='claude', session_id='origin', project=str(self.root), title='Working conversation'))

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def specs(self, providers=('claude', 'codex')):
        return [dict(provider=p, model='explicit-model-' + str(i), effort='medium',
                     name=n, personality=role) for i, (p, n, role) in enumerate(zip(
                         providers, ('Pragmatist', 'Skeptic'), ('Smallest workable design', 'Challenge failure modes')))]

    def launch(self, **kwargs):
        options = dict(provider='claude', session_id='origin', agents=self.specs(),
                       task='Evaluate our recovery design', shared_context='User decision: local storage only. Prior evidence: fixture A.',
                       issues=['Recovery design'], simulate=True)
        options.update(kwargs)
        return self.engine.launch(**options)

    def complete(self, run):
        for _ in range(10):
            if not self.engine.step(run['id']):
                break
        return self.store.get(run['id'])

    def test_mixed_provider_launch_forks_origin_and_creates_peer_from_handoff(self):
        run = self.launch()
        first, second = run['participants']
        self.assertEqual((first['source_mode'], first['session_id']), ('fork', 'origin'))
        self.assertEqual((second['source_mode'], second['session_id']), ('brief', None))
        self.assertIn('no inherited conversation', second['context_source'])
        self.assertEqual(run['context_revision'], digest(run['shared_context']))
        for i in (0, 1):
            context = self.engine.context(run, i)
            self.assertEqual(context['shared_context'], run['shared_context'])
            self.assertEqual(context['personality'], run['participants'][i]['personality'])
            self.assertEqual(run['participants'][i]['project'], str(self.root))

    def test_two_same_provider_models_fork_the_same_origin(self):
        run = self.launch(agents=self.specs(('claude', 'claude')), shared_context='')
        self.assertTrue(all(p['source_mode'] == 'fork' and p['session_id'] == 'origin' for p in run['participants']))
        self.assertNotEqual(run['participants'][0]['model'], run['participants'][1]['model'])
        completed = self.complete(run)
        self.assertEqual(completed['status'], 'ready for review')
        self.assertNotEqual(completed['participants'][0]['child_id'], completed['participants'][1]['child_id'])
        self.assertEqual({m['name'] for m in completed['messages']}, {'Pragmatist', 'Skeptic'})

    def test_results_return_only_to_origin_not_spawned_workers(self):
        run = self.complete(self.launch())
        deliveries = [dict(r) for r in self.store.db.execute('SELECT * FROM deliveries')]
        self.assertEqual(len(deliveries), 1)
        self.assertEqual((deliveries[0]['provider'], deliveries[0]['session_id']), ('claude', 'origin'))
        self.assertEqual(len(self.engine.mailbox('claude', 'origin')), 1)
        for p in run['participants']:
            self.assertEqual(self.engine.mailbox(p['provider'], p['child_id']), [])

    def test_codex_origin_can_launch_two_claude_workers_and_receive_result(self):
        self.store.register(dict(provider='codex', session_id='codex-origin', project=str(self.root), title='Codex work'))
        run = self.launch(provider='codex', session_id='codex-origin', agents=self.specs(('claude', 'claude')))
        self.assertTrue(all(p['source_mode'] == 'brief' for p in run['participants']))
        self.complete(run)
        self.assertEqual(len(self.engine.mailbox('codex', 'codex-origin')), 1)
        self.assertEqual(self.engine.mailbox('claude', 'origin'), [])

    def test_cross_provider_context_is_required_without_asking_for_a_second_session(self):
        with self.assertRaisesRegex(ValueError, 'context handoff'):
            self.launch(shared_context='')
        with self.assertRaisesRegex(ValueError, 'Origin session is not registered'):
            self.launch(session_id='unknown')

    def test_fresh_provider_command_then_persistent_resume(self):
        adapter = CLIAdapter()
        for provider in ('codex', 'claude'):
            p = dict(provider=provider, model='selected', effort='medium', session_id=None, source_mode='brief')
            fresh = adapter.command(p)
            self.assertNotIn(None, fresh)
            self.assertNotIn('fork', fresh)
            self.assertNotIn('--fork-session', fresh)
            self.assertNotIn('--resume', fresh)
            self.assertNotIn('resume', fresh)
            resumed = adapter.command(dict(p, child_id='persistent-child'))
            self.assertIn('persistent-child', resumed)
            self.assertIn('resume' if provider == 'codex' else '--resume', resumed)

    def test_missing_native_fork_is_not_replaced_with_fresh_chat(self):
        class Failure:
            def turn(self, *args):
                raise ValueError('Native fork unavailable')
        self.engine.adapters['fake'] = Failure()
        run = self.launch()
        self.engine.step(run['id'])
        failed = self.store.get(run['id'])
        self.assertEqual(failed['status'], 'paused')
        self.assertEqual(failed['participants'][0]['source_mode'], 'fork')
        self.assertIsNone(failed['participants'][0]['child_id'])

    def test_child_origin_is_rejected(self):
        self.store.register(dict(provider='claude', session_id='origin', project=str(self.root), title='Child', child=True))
        with self.assertRaisesRegex(ValueError, 'recursive'):
            self.launch()

    def test_stable_personalities_and_handoff_on_every_turn(self):
        contexts = []
        fake = FakeAdapter()
        class Record:
            def turn(self, p, context, cancel, timeout):
                contexts.append((p['index'], context))
                return fake.turn(p, context, cancel, timeout)
        self.engine.adapters['fake'] = Record()
        run = self.complete(self.launch())
        self.assertGreaterEqual(len(contexts), 3)
        for index, context in contexts:
            self.assertEqual(context['name'], run['participants'][index]['name'])
            self.assertEqual(context['personality'], run['participants'][index]['personality'])
            self.assertEqual(context['context_revision'], run['context_revision'])

    def test_reinstall_updates_owned_skill_without_altering_hooks(self):
        config = self.root / 'claude'
        install('claude', config, self.root / 'state')
        before = (config / 'settings.json').read_text()
        skill = config / 'skills/breakout/SKILL.md'
        manifest_path = config / 'breakout-install.json'
        manifest = json.loads(manifest_path.read_text())
        old = 'Previously installed owned skill'
        skill.write_text(old)
        manifest['skill_hash'] = digest(old)
        manifest_path.write_text(json.dumps(manifest))
        result = install('claude', config, self.root / 'state')
        self.assertEqual(result['status'], 'skill updated')
        self.assertEqual((config / 'settings.json').read_text(), before)
        self.assertNotEqual(skill.read_text(), old)
        skill.write_text('User customization')
        with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
            install('claude', config, self.root / 'state')
