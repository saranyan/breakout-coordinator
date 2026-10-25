"""V2 review-window transitions; delivery receipt never means closure."""
import copy
import time
from .protocol import digest
from .store import uid


class ReviewWindow:
    @staticmethod
    def required(run):
        return [p for p in run['participants'] if p.get('role') != 'advisory']

    @staticmethod
    def author_index(run):
        return next((p['index'] for p in run['participants'] if p.get('role') == 'author'), 0)

    @staticmethod
    def next_index(run, index):
        for offset in range(1, len(run['participants']) + 1):
            candidate = (index + offset) % len(run['participants'])
            if not run['participants'][candidate].get('unavailable'):
                return candidate
        return index

    def check_origin(self, run, provider, session_id):
        if provider is not None or session_id is not None:
            origin = run.get('origin') or {}
            if origin.get('provider') != provider or origin.get('session_id') != session_id:
                raise ValueError('Only the originating conversation may control this review window')

    def review_control(self, run_id, action, text=None, event_id=None, target=None,
                       provider=None, session_id=None, revision=None, replies=0, rounds=0, minutes=0,
                       source='origin chat', source_revision=None):
        with self.lock, self.store.db:
            run = self.store.get(run_id)
            self.check_origin(run, provider, session_id)
            if run.get('version', 1) < 2:
                raise ValueError('Legacy run is terminal; start a v2 follow-up with its result and discussion')
            if event_id and event_id in run['events']:
                return run
            if run['status'] == 'closed':
                if action == 'close':
                    return run
                raise ValueError('Breakout is closed; start a follow-up')
            if revision and revision != run['proposal_revision']:
                raise ValueError('Artifact version changed; review the current version first')
            event_id = event_id or uid()
            if action == 'close':
                if run_id in self.active:
                    self.active[run_id].set()
                run['epoch'] += 1
                run['closed_at'] = time.time()
                run['close_reason'] = text or 'User closed breakout'
                run['review_queue'] = []
                self._finish(run, run['close_reason'])
                run['status'] = 'closed'
            elif action == 'accept':
                if run['status'] != 'ready for review' or not run['proposal_revision']:
                    raise ValueError('Wait for an artifact before accepting it')
                run['acceptance'] = dict(revision=run['proposal_revision'], time=time.time(), event_id=event_id)
            elif action == 'extend':
                if run['status'] not in ('ready for review', 'paused', 'needs your input') or self._uncertain(run_id):
                    raise ValueError('Park or reconcile the breakout before extending limits')
                if (not all(isinstance(v, (int, float)) and v >= 0 for v in (replies, rounds, minutes))
                        or not any((replies, rounds, minutes)) or replies > 100 or rounds > 100 or minutes > 1440):
                    raise ValueError('Provide a positive bounded replies, rounds or minutes extension')
                run['max_review_replies'] += int(replies)
                run['max_turns'] += int(rounds) * len(run['participants'])
                run['work_seconds'] += minutes * 60
                run['review_seconds'] += minutes * 60
                run['diagnostic'] = 'Limits extended; ask, revise or resume explicitly'
            elif action in ('ask', 'revise', 'feedback'):
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('Supply a question or feedback')
                if run['status'] != 'ready for review' or run_id in self.active or self._uncertain(run_id):
                    raise ValueError('Wait for review readiness or reconcile the active turn first')
                if len(text.encode()) > 128000:
                    raise ValueError('Feedback exceeds 128 KB')
                if action == 'ask':
                    names = {p['name']: p['index'] for p in run['participants'] if not p.get('unavailable')}
                    if target in (None, 'team'):
                        queue = list(names.values())
                    elif target in names:
                        queue = [names[target]]
                    else:
                        raise ValueError('Unknown or unavailable member; choose ' + ', '.join(names))
                    if run['review_replies'] + len(queue) > run['max_review_replies'] or run['review_seconds'] <= 0:
                        raise ValueError('Review budget exhausted; close or explicitly extend it')
                    run['phase'] = 'review'
                    run['review_queue'] = queue
                    run['next_participant'] = queue[0]
                    run['status'] = 'working'
                elif action == 'revise':
                    if run['turns'] >= run['max_turns'] or run['work_seconds'] <= 0:
                        raise ValueError('Work budget exhausted; close or explicitly extend it')
                    run['epoch'] += 1
                    run['phase'] = 'discuss'
                    run['status'] = 'working'
                    run['next_participant'] = self.author_index(run)
                    run['revision_requested'] = True
                    for p in run['participants']:
                        p['reviewed_revision'] = ''
                        p['questions'] = []
                    for issue in run['issues']:
                        issue.update(status='open', votes={})
                feedback = dict(id=event_id, author='user', name='You', epoch=run['epoch'],
                                content={'message': text}, created=time.time(), action=action,
                                artifact_revision=run['proposal_revision'], target=target or 'team',
                                source=source, source_revision=source_revision)
                run['messages'].append(feedback)
                run.setdefault('feedback', []).append(copy.deepcopy(feedback))
            else:
                raise ValueError('Unsupported review action')
            run['events'][event_id] = action
            self.store.save(run)
            return run

    def expire_reviews(self):
        with self.lock:
            expired = [r['id'] for r in self.store.all() if r.get('version', 1) >= 2
                       and r['status'] == 'ready for review' and r.get('close_at')
                       and r['close_at'] <= time.time() and r['id'] not in self.active and not self._uncertain(r['id'])]
            for run_id in expired:
                self.review_control(run_id, 'close', text='Previously authorized review timeout elapsed',
                                    event_id='review-timeout')

    def review_receipt(self, run):
        return dict(team=[dict(name=p['name'], provider=p['provider'], model=p['model'], role=p.get('role'),
                               effort=p.get('effort'), context=p['source_mode'],
                               personality_hash=digest(p.get('personality', '')),
                               personality_version=(p.get('personality_card') or {}).get('metadata', {}).get('version'))
                          for p in run['participants']],
                    returns_to=run.get('origin'), guidance=run.get('guidance', []),
                    limits=dict(work_turns=run['max_turns'], work_seconds=run.get('work_seconds'),
                                review_replies=run.get('max_review_replies'), close_at=run.get('close_at')),
                    cost='unknown for CLI; API usage reported when available',
                    context_packet=run.get('context_packet'))
