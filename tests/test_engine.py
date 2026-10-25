import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from breakout.documents import snapshot
from breakout.engine import Engine
from breakout.providers import FakeAdapter
from breakout.store import Store, uid


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.document = self.root / 'rfc.txt'
        self.document.write_text('Ship a local coordinator.\n')
        self.store = Store(self.root / 'state')
        self.engine = Engine(self.store)
        self.participants = [dict(provider=p, session_id='parent-' + p, model='test', project=str(self.root))
                             for p in ('codex', 'claude')]

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def create(self, **kw):
        options = dict(document=snapshot(str(self.document)), task='Review acceptance criteria',
                       participants=self.participants, issues=['Acceptance criteria'], simulate=True)
        options.update(kw)
        return self.engine.create(**options)['id']

    def complete(self, run_id):
        for _ in range(12):
            if not self.engine.step(run_id):
                break
        return self.store.get(run_id)

    def test_consensus_requires_three_turns_and_same_revision(self):
        run_id = self.create()
        self.engine.step(run_id)
        first = self.store.get(run_id)
        self.assertEqual(first['status'], 'working')
        self.assertNotEqual(first['participants'][0]['child_id'], self.participants[0]['session_id'])
        self.engine.step(run_id)
        self.assertEqual(self.store.get(run_id)['status'], 'working')
        run = self.complete(run_id)
        self.assertEqual(run['turns'], 3)
        self.assertEqual(run['status'], 'ready for review')
        self.assertFalse(run['result']['partial'])
        self.assertEqual(run['issues'][0]['status'], 'agreed')
        self.assertTrue(all(p['reviewed_revision'] == run['proposal_revision'] for p in run['participants']))

    def test_scan_waits_for_single_issue_selection(self):
        run_id = self.create(issues=None)
        run = self.complete(run_id)
        self.assertEqual(run['status'], 'needs your input')
        self.assertEqual(run['scan_turns'], 2)
        self.assertFalse(run['proposal'])
        self.engine.control(run_id, 'select', issue_ids=['I1'])
        self.assertEqual(self.complete(run_id)['status'], 'ready for review')

    def test_steering_deduplicates_and_reaches_both_once(self):
        contexts = []
        fake = FakeAdapter()

        class Recording:
            def turn(self, participant, context, cancel, timeout):
                contexts.append((participant['index'], copy.deepcopy(context)))
                return fake.turn(participant, context, cancel, timeout)

        self.engine.adapters['fake'] = Recording()
        run_id = self.create()
        self.engine.step(run_id)
        self.engine.control(run_id, 'decide', text='Preserve local-only storage', event_id='decision-1')
        self.engine.control(run_id, 'decide', text='Preserve local-only storage', event_id='decision-1')
        after = self.store.get(run_id)
        self.assertEqual(after['epoch'], 1)
        self.assertEqual(len(after['decisions']), 1)
        self.assertFalse(after['proposal_revision'])
        self.complete(run_id)
        for index in (0, 1):
            seen = [m for i, c in contexts if i == index for m in c['messages'] if m['id'] == 'decision-1']
            self.assertEqual(len(seen), 1)

    def test_disagreement_is_not_consensus(self):
        fake = FakeAdapter()

        class Disagree:
            def turn(self, participant, context, cancel, timeout):
                child, envelope = fake.turn(participant, context, cancel, timeout)
                if participant['index'] == 1:
                    envelope['issues'][0].update(resolution='Use a different design', objection='Conflicts with requirement')
                return child, envelope

        self.engine.adapters['fake'] = Disagree()
        run_id = self.create(max_rounds=2)
        run = self.complete(run_id)
        self.assertTrue(run['result']['partial'])
        self.assertNotEqual(run['issues'][0]['status'], 'agreed')
        self.assertEqual(run['turns'], 4)

    def test_evidence_and_human_decisions_remain_explicit(self):
        run = self.complete(self.create(issues=['Requires experiment', 'User decision']))
        self.assertEqual([i['status'] for i in run['issues']], ['needs evidence', 'needs user'])
        self.assertEqual(run['result']['questions'], ['User decision'])

    def test_round_budget_produces_partial(self):
        run = self.complete(self.create(max_rounds=1))
        self.assertTrue(run['result']['partial'])
        self.assertEqual(run['turns'], 2)

    def test_wall_budget_no_dispatch(self):
        run_id = self.create()
        with self.store.db:
            run = self.store.get(run_id)
            run['deadline'] = time.time()-1
            self.store.save(run)
        run = self.complete(run_id)
        self.assertEqual(run['turns'], 0)
        self.assertTrue(run['result']['partial'])

    def test_pause_resume_preserves_child(self):
        run_id = self.create()
        self.engine.step(run_id)
        child = self.store.get(run_id)['participants'][0]['child_id']
        self.engine.control(run_id, 'pause')
        self.assertFalse(self.engine.step(run_id))
        self.engine.control(run_id, 'resume')
        self.assertEqual(self.complete(run_id)['participants'][0]['child_id'], child)

    def test_unknown_outcome_blocks_replay_after_restart(self):
        class Fail:
            def turn(self, *args):
                raise ConnectionError('Disconnected after dispatch')

        self.engine.adapters['fake'] = Fail()
        run_id = self.create()
        self.engine.step(run_id)
        self.engine = Engine(self.store)
        self.engine.recover()
        with self.assertRaisesRegex(ValueError, 'Reconcile'):
            self.engine.control(run_id, 'resume')
        self.assertFalse(self.engine.step(run_id))
        attempt = self.store.db.execute('SELECT id FROM attempts').fetchone()[0]
        self.engine.reconcile(attempt, not_started=True)
        self.engine.control(run_id, 'resume')
        self.assertEqual(self.complete(run_id)['status'], 'ready for review')

    def test_received_response_is_applied_once_after_restart(self):
        run_id = self.create()
        run = self.store.get(run_id)
        context = self.engine.context(run, 0)
        child, envelope = FakeAdapter().turn(run['participants'][0], context, threading.Event(), 5)
        with self.store.db:
            self.store.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)',
                                  (uid(), run_id, 0, 'received', json.dumps(context),
                                   json.dumps(dict(child_id=child, envelope=envelope)), None, time.time()))
        self.engine.recover()
        self.engine.recover()
        self.assertEqual(len(self.store.get(run_id)['messages']), 1)
        self.assertEqual(self.store.get(run_id)['turns'], 1)

    def test_running_attempt_becomes_uncertain_on_recovery(self):
        run_id = self.create()
        with self.store.db:
            self.store.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)',
                                  (uid(), run_id, 0, 'running', '{}', None, None, time.time()))
        self.engine.recover()
        self.assertEqual(self.store.get(run_id)['status'], 'paused')
        self.assertEqual(self.store.db.execute('SELECT status FROM attempts').fetchone()[0], 'uncertain')

    def test_delivery_ack_is_idempotent(self):
        run_id = self.create()
        self.complete(run_id)
        first = self.engine.mailbox('codex', 'parent-codex')
        self.assertEqual(first, self.engine.mailbox('codex', 'parent-codex'))
        self.engine.acknowledge(run_id, 1, 'codex', 'parent-codex')
        self.engine.acknowledge(run_id, 1, 'codex', 'parent-codex')
        self.assertEqual(self.engine.mailbox('codex', 'parent-codex'), [])
        self.assertEqual(len(self.engine.mailbox('claude', 'parent-claude')), 1)

    def test_source_edit_is_flagged_stale(self):
        run_id = self.create()
        self.document.write_text('An external edit')
        self.assertEqual(self.complete(run_id)['result']['document_freshness'], 'stale')

    def test_start_request_is_idempotent(self):
        a = self.create(request_id='same-event')
        b = self.create(request_id='same-event')
        self.assertEqual(a, b)
        self.assertEqual(len(self.store.all()), 1)

    def test_child_recursion_rejected(self):
        self.participants[0]['child'] = True
        with self.assertRaisesRegex(ValueError, 'recursive'):
            self.create()

    def test_steer_during_turn_discards_old_conclusions(self):
        started, release = threading.Event(), threading.Event()
        fake = FakeAdapter()

        class Slow:
            def turn(self, participant, context, cancel, timeout):
                started.set()
                release.wait(3)
                return fake.turn(participant, context, cancel, timeout)

        self.engine.adapters['fake'] = Slow()
        run_id = self.create()
        worker = threading.Thread(target=self.engine.step, args=(run_id,))
        worker.start()
        self.assertTrue(started.wait(2))
        self.engine.control(run_id, 'steer', text='Changed scope', event_id='new-scope')
        release.set()
        worker.join(3)
        run = self.store.get(run_id)
        self.assertFalse(run['proposal'])
        self.assertTrue(run['messages'][-1]['stale'])
        self.assertTrue(run['participants'][0]['child_id'])

    def test_cancel_stops_new_scheduling(self):
        run_id = self.create()
        self.engine.control(run_id, 'cancel')
        self.assertFalse(self.engine.step(run_id))
        self.assertTrue(self.store.get(run_id)['result']['partial'])


if __name__ == '__main__':
    unittest.main()
