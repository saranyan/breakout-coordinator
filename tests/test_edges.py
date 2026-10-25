import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from breakout.documents import snapshot
from breakout.engine import Engine
from breakout.probe import probe
from breakout.providers import CLIAdapter, Interrupted
from breakout.store import Store


class EdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_engine(self):
        store = Store(self.root / 'state')
        self.addCleanup(store.db.close)
        doc = self.root / 'rfc.txt'
        doc.write_text('A document')
        engine = Engine(store)
        participants = [dict(provider=p, session_id='parent-' + p, model='fake', project=str(self.root))
                        for p in ('codex', 'claude')]
        run = engine.create(snapshot(str(doc)), 'Review', participants, issues=['Scope'], simulate=True)
        return engine, run['id']

    def test_timeout_during_turn_returns_partial_and_keeps_uncertainty(self):
        engine, run_id = self.make_engine()

        class Timeout:
            def turn(self, *args):
                with engine.lock, engine.store.db:
                    run = engine.store.get(run_id)
                    run['deadline'] = time.time()-1
                    engine.store.save(run)
                raise Interrupted('Time budget elapsed during provider execution')

        engine.adapters['fake'] = Timeout()
        engine.step(run_id)
        run = engine.store.get(run_id)
        self.assertEqual(run['status'], 'ready for review')
        self.assertTrue(run['result']['partial'])
        self.assertIn('uncertain', run['result']['reason'])
        self.assertEqual(engine.store.db.execute('SELECT status FROM attempts').fetchone()[0], 'uncertain')

    def test_repeated_end_has_one_result_revision_and_delivery(self):
        engine, run_id = self.make_engine()
        engine.control(run_id, 'end')
        engine.control(run_id, 'end')
        self.assertEqual(engine.store.get(run_id)['result_revision'], 1)
        self.assertEqual(engine.store.db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0], 2)

    def test_known_child_cannot_be_selected_even_without_child_flag(self):
        engine, run_id = self.make_engine()
        engine.step(run_id)
        run = engine.store.get(run_id)
        participants = [dict(provider=p['provider'], session_id=p['child_id'] or p['session_id'],
                             project=p['project'], model=p['model']) for p in run['participants']]
        with self.assertRaisesRegex(ValueError, 'recursive'):
            engine.create(run['document'], 'Review', participants, simulate=True)

    def test_cancellation_terminates_actual_child_process(self):
        pid_path = self.root / 'pid'
        script = ('import os,sys,time,pathlib; '
                  'pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); '
                  'sys.stdin.read(); time.sleep(30)')

        class StubCLI(CLIAdapter):
            def command(self, participant, structured=True):
                return [sys.executable, '-c', script, str(pid_path)]

        cancel = threading.Event()
        timer = threading.Timer(.3, cancel.set)
        timer.start()
        try:
            with self.assertRaises(Interrupted):
                StubCLI().turn(dict(provider='claude', session_id='parent', model='fake', project=str(self.root)),
                               {'run_id': 'test'}, cancel, 3)
        finally:
            timer.cancel()
        pid = int(pid_path.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_probe_checks_fork_inheritance_and_parent_isolation(self):
        histories = {}

        def transport(command, text, cwd, timeout=90):
            if '--resume' not in command:
                session = 'test-parent'
                histories[session] = []
            else:
                source = command[command.index('--resume')+1]
                if '--fork-session' in command:
                    session = 'test-child'
                    histories[session] = list(histories[source])
                else:
                    session = source
            markers = re.findall(r'BREAKOUT-(?:PARENT|CHILD)-[a-f0-9-]+', text)
            histories[session].extend(markers)
            return json.dumps(dict(session_id=session, result='\n'.join(histories[session]), is_error=False))

        with patch('breakout.probe.run_command', side_effect=transport):
            report = probe('claude', 'explicit-model', self.root)
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(all(report['checks'].values()))
        self.assertEqual(len(histories['test-parent']), 1)
        self.assertEqual(len(histories['test-child']), 2)
        self.assertTrue(Path(report['report_path']).is_file())

    def test_codex_tools_are_disabled_and_verified_before_dispatch(self):
        class Result:
            def __init__(self, enabled):
                self.stdout = json.dumps([{'name': 'document-server', 'enabled': enabled}])

        with patch('breakout.providers.subprocess.run', side_effect=[Result(True), Result(False)]) as run:
            flags = CLIAdapter.codex_restrictions(str(self.root))
            self.assertIn('mcp_servers.document-server.enabled=false', flags)
            self.assertIn('plugins', flags)
            self.assertEqual(run.call_count, 2)
        with patch('breakout.providers.subprocess.run', side_effect=[Result(True), Result(True)]):
            with self.assertRaisesRegex(ValueError, 'disable Codex MCP'):
                CLIAdapter.codex_restrictions(str(self.root))

    def test_second_dispatch_cannot_overlap_active_turn(self):
        engine, run_id = self.make_engine()
        started, release = threading.Event(), threading.Event()
        original = engine.adapters['fake']

        class Slow:
            def turn(self, *args):
                started.set()
                release.wait(3)
                return original.turn(*args)

        engine.adapters['fake'] = Slow()
        thread = threading.Thread(target=engine.step, args=(run_id,))
        thread.start()
        self.assertTrue(started.wait(2))
        try:
            self.assertFalse(engine.step(run_id))
            self.assertEqual(engine.store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
        finally:
            release.set()
            thread.join(3)


if __name__ == '__main__':
    unittest.main()
