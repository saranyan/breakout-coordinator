import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from breakout.config import connection, resolve
from breakout.engine import Engine
from breakout.http_adapter import HTTPAdapter
from breakout.library import Library
from breakout.protocol import digest
from breakout.providers import FakeAdapter
from breakout.store import Store


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_recipe_resolves_exact_models_roles_and_active_card(self):
        lib = Library(self.root)
        card = lib.save('personalities', 'visionary_designer', 'Ask about onboarding', direct=True)
        result = resolve('examples/access-map.toml', 'rfc', self.root)
        self.assertEqual([a['model'] for a in result['agents']], ['claude-opus-5-5', 'gpt-6.1-sol'])
        self.assertEqual(result['agents'][1]['personality_card']['sha256'], card['sha256'])
        self.assertEqual(result['agents'][1]['personality'], 'Ask about onboarding')
        self.assertEqual(result['agents'][0]['role'], 'author')

    def test_unknown_options_and_embedded_credentials_are_rejected(self):
        for spec in [dict(adapter='unknown'), dict(adapter='openai_chat', base_url='https://secret:password@example.com/v1'),
                     dict(adapter='openai_chat', base_url='https://example.com/v1?token=x'),
                     dict(adapter='openai_chat', base_url='http://example.com/v1'),
                     dict(adapter='openai_chat', base_url='https://example.com/v1', api_key='SECRET')]:
            with self.assertRaises(ValueError):
                connection(spec)

    def test_undeclared_model_options_are_not_silently_dropped(self):
        text = Path('examples/access-map.toml').read_text().replace('supported_options = ["max_tokens", "response_format"]', 'supported_options = []')
        config = self.root / 'config.toml'
        config.write_text(text)
        with self.assertRaisesRegex(ValueError, 'declare'):
            resolve(config, 'extended', self.root)

    def test_http_adapter_conversation_usage_preflight_and_first_send_consent(self):
        requests = []
        class Response:
            def __init__(self, data):
                self.data = json.dumps(data).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return self.data[:limit]
        def transport(req, timeout):
            if req.get_method() == 'GET':
                return Response({'data': [{'id': 'fixture-model'}]})
            body = json.loads(req.data)
            requests.append(body)
            context = json.loads(body['messages'][-1]['content'].split('BEGIN TASK DATA\n')[1].split('\nEND TASK DATA')[0])
            _, env = FakeAdapter().turn(dict(index=1, name='API'), context, threading.Event(), 10)
            return Response(dict(choices=[dict(finish_reason='stop', message=dict(content=json.dumps(env)))], usage={'total_tokens': 42}))
        mocked = patch('breakout.http_adapter.build_opener')
        opener = mocked.start()
        self.addCleanup(mocked.stop)
        opener.return_value.open.side_effect = transport
        store = Store(self.root / 'state')
        self.addCleanup(store.db.close)
        engine = Engine(store, {'live': FakeAdapter(), 'api': HTTPAdapter()})
        store.register(dict(provider='codex', session_id='origin', project=str(self.root), title='Test'))
        agents = [dict(provider='codex', model='fake', name='Author'), dict(provider='api', model='fixture-model', name='API',
                   connection_name='fixture', connection=dict(adapter='openai_chat', base_url='https://fixture.invalid/v1', supported_options=[]))]
        args = dict(provider='codex', session_id='origin', agents=agents, task='Review', shared_context='Explicit handoff', issues=['Scope'])
        with self.assertRaisesRegex(ValueError, 'Authorize first'):
            engine.launch(**args)
        self.assertEqual(requests, [])
        r = engine.launch(**args, allow_connections=['fixture'])
        for _ in range(8):
            if not engine.step(r['id']):
                break
        r = store.get(r['id'])
        self.assertFalse(r['result']['partial'])
        self.assertEqual(r['participants'][1]['usage'][0]['total_tokens'], 42)
        engine.review_control(r['id'], 'ask', target='API', text='What did I ask?')
        engine.step(r['id'])
        self.assertGreater(len(requests[-1]['messages']), 1)
        self.assertEqual(requests[-1]['model'], 'fixture-model')
        self.assertNotIn('tools', requests[-1])
        self.assertEqual(store.get(r['id'])['review_replies'], 1)
        engine.launch(**args)  # approved same destination, no repeated consent

    def test_http_failure_does_not_leak_credential_or_body(self):
        from urllib.error import HTTPError
        p = dict(connection=dict(adapter='openai_chat', base_url='https://example.com/v1', api_key_env='TEST_BREAKOUT_KEY'))
        with patch.dict('os.environ', {'TEST_BREAKOUT_KEY': 'very-secret'}), patch('breakout.http_adapter.build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('https://example.com', 401, 'very-secret', {}, None)
            with self.assertRaises(ValueError) as exc:
                HTTPAdapter().request(p, '/models')
            self.assertNotIn('very-secret', str(exc.exception))


if __name__ == '__main__':
    unittest.main()
