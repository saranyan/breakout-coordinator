"""Transactional scheduler. All state transitions are serialized by the engine lock."""
import copy
import json
from pathlib import Path
import threading
import time
from .documents import freshness
from .protocol import digest, validate
from .providers import CLIAdapter, FakeAdapter
from .store import uid
from .review import ReviewWindow
from .http_adapter import HTTPAdapter
from .library import Library


class Engine(ReviewWindow):
    def __init__(self, store, adapters=None):
        self.store = store
        self.lock = threading.RLock()
        self.active = {}
        self.preflight_cache = {}
        self.adapters = adapters or {"fake": FakeAdapter(), "live": CLIAdapter(), 'api': HTTPAdapter()}

    def launch(self, provider, session_id, agents, task, document=None, shared_context='',
               issues=None, simulate=False, max_rounds=4, minutes=20, request_id=None,
               review_replies=10, close_after_hours=None, context_bytes=1048576,
               omissions=None, constraints=None, recipe=None, allow_connections=None, parent_run=None):
        """Launch 2–4 workers from one origin; pin context and return only here."""
        if not isinstance(agents, list) or not 2 <= len(agents) <= 4 or not all(isinstance(a, dict) for a in agents):
            raise ValueError('Supply two to four agent specifications')
        with self.lock:
            origin = next((s for s in self.store.sessions()
                           if s['provider'] == provider and s['session_id'] == session_id), None)
        if origin is None:
            raise ValueError('Origin session is not registered; enable its hook or register its known ID')
        if parent_run:
            with self.lock:
                parent = self.store.get(parent_run)
            if parent.get('origin') and (parent['origin']['provider'], parent['origin']['session_id']) != (provider, session_id):
                raise ValueError('Follow-up must originate in the earlier run\'s conversation')
            from .render import result_markdown, transcript_markdown
            shared_context = shared_context + '\n\nPrevious run: ' + parent_run + '\n' + result_markdown(parent) + '\n' + transcript_markdown(parent)
        if any(a.get('provider', provider) != provider for a in agents) and (not isinstance(shared_context, str) or not shared_context.strip()):
            raise ValueError('Cross-provider agents require an explicit context handoff from the originating session')
        participants = []
        for index, spec in enumerate(agents):
            if set(spec) - {'provider', 'model', 'effort', 'name', 'personality', 'role', 'connection',
                            'connection_name', 'personality_card', 'max_output_tokens', 'json_mode'}:
                raise ValueError('Unknown agent specification fields')
            agent_provider = spec.get('provider', provider)
            inherited = agent_provider == provider
            participant = dict(spec, provider=agent_provider, model=spec.get('model'), effort=spec.get('effort'),
                                     name=spec.get('name', f'{agent_provider}-{index+1}'),
                                     personality=spec.get('personality', ''), project=origin['project'],
                                     session_id=session_id if inherited else None,
                                     source_mode='fork' if inherited else 'brief',
                                     role=spec.get('role', 'author' if index == 0 else 'reviewer'),
                                     boundary=origin.get('boundary'))
            participants.append(participant)
        if sum(p['role'] == 'author' for p in participants) != 1 or not any(p['role'] == 'reviewer' for p in participants):
            raise ValueError('A team requires exactly one author and at least one required reviewer')
        if any(p['role'] not in ('author', 'reviewer', 'advisory') for p in participants):
            raise ValueError('Role must be author, reviewer or advisory')
        if not isinstance(review_replies, int) or not 1 <= review_replies <= 100:
            raise ValueError('Review reply limit must be 1–100')
        if close_after_hours is not None and not 0 < close_after_hours <= 8760:
            raise ValueError('Close timeout must be positive and at most one year')
        if not isinstance(context_bytes, int) or not 1024 <= context_bytes <= 4 * 1024 * 1024:
            raise ValueError('Context byte budget out of range')
        for values in (omissions or [], constraints or []):
            if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
                raise ValueError('Omissions and constraints must be lists of text')
        guidance = Library(origin['project']).active()
        packet = dict(origin=dict(provider=provider, session_id=session_id), project=origin['project'],
                      boundary=origin.get('boundary'), shared_context=shared_context, omissions=omissions or [],
                      constraints=constraints or [], guidance=guidance,
                      acquisition=[p['source_mode'] for p in participants],
                      history_owner=['coordinator' if p['provider'] == 'api' else 'provider' for p in participants],
                      native_history_size='unknown; provider may compact history', source_revision=(document or {}).get('source_revision'))
        packet['sha256'] = digest(json.dumps(packet, sort_keys=True))
        packet['bytes'] = len(json.dumps(packet).encode()) + len(json.dumps(document or {}).encode())
        if packet['bytes'] > context_bytes:
            raise ValueError('Context exceeds budget; narrow the task without dropping explicit constraints')
        for p in participants:
            if p['provider'] == 'api':
                from .config import api_options
                p['connection'] = api_options(p)
                if not simulate:
                    key = digest(json.dumps(p['connection'], sort_keys=True))
                    with self.lock, self.store.db:
                        approved = self.store.db.execute('SELECT 1 FROM consents WHERE id=? AND project=?', (key, origin['project'])).fetchone()
                        if not approved and p.get('connection_name') not in (allow_connections or []):
                            raise ValueError('Authorize first context send with --allow-connection ' + p.get('connection_name', 'NAME'))
                        self.adapters['api'].preflight(p)
                        self.store.db.execute('INSERT OR IGNORE INTO consents VALUES(?,?,?)', (key, origin['project'], time.time()))
            elif not simulate and hasattr(self.adapters['live'], 'preflight'):
                cache_key = (p['provider'], p['model'], p.get('effort'))
                cached = self.preflight_cache.get(cache_key)
                if not cached or cached[0] < time.time()-300:
                    cached = (time.time(), self.adapters['live'].preflight(p))
                    self.preflight_cache[cache_key] = cached
                p['preflight'] = cached[1]
        if document is None:
            document = dict(source='task://brief', kind='task', text=task, sha256=digest(task),
                            captured=time.time(), local_path=None, source_revision=None, comments_complete=False)
        return self.create(document, task, participants, issues=issues, simulate=simulate,
                           max_rounds=max_rounds, minutes=minutes, request_id=request_id,
                           origin=origin, shared_context=shared_context, version=2,
                           v2=dict(parent_run=parent_run, guidance=guidance, context_packet=packet, context_bytes=context_bytes,
                                   recipe=recipe, review_replies=0, max_review_replies=review_replies,
                                   review_queue=[], work_seconds=minutes*60, review_seconds=minutes*60,
                                   close_after_hours=close_after_hours, close_at=None, feedback=[]))

    def create(self, document, task, participants, issues=None, simulate=False,
               max_rounds=4, minutes=20, request_id=None, origin=None, shared_context='', version=1, v2=None):
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A task is required")
        if (not (2 <= len(participants) <= (4 if version == 2 else 2))
                or any(p.get('provider') not in (('codex', 'claude', 'api') if version == 2 else ('codex', 'claude')) for p in participants)):
            raise ValueError('Select two agents using Codex or Claude')
        if not isinstance(shared_context, str):
            raise ValueError('Shared context must be text')
        if origin is not None:
            origin = copy.deepcopy(origin)
            if origin.get('provider') not in ('codex', 'claude') or not origin.get('session_id'):
                raise ValueError('Origin must identify the invoking provider and session')
            if origin.get('child'):
                raise ValueError('Breakout children cannot create recursive breakouts')
            if not isinstance(origin.get('project'), str) or not Path(origin['project']).is_dir():
                raise ValueError('Origin project must exist')
        if not 1 <= max_rounds <= 100 or not 0 < minutes <= 1440:
            raise ValueError("Budget out of range")
        if not isinstance(document, dict) or digest(document.get("text", "")) != document.get("sha256"):
            raise ValueError("Invalid document snapshot")
        participants = copy.deepcopy(participants)
        for index, p in enumerate(participants):
            if p['provider'] != 'api':
                CLIAdapter.effort_flags(p['provider'], p.get('effort'))
            p.setdefault('source_mode', 'fork')
            if p['source_mode'] not in ('fork', 'brief'):
                raise ValueError('Unsupported context source mode')
            if p['source_mode'] == 'brief':
                if origin is None or p.get('session_id') is not None:
                    raise ValueError('Brief-based agents require an origin and no source session')
                p['session_id'] = None
            for key in (('session_id', 'model', 'project') if p['source_mode'] == 'fork' else ('model', 'project')):
                if not isinstance(p.get(key), str) or not p[key] or p[key].startswith("-"):
                    raise ValueError(f"Participant requires an explicit {key}")
            if p.get("child"):
                raise ValueError("Breakout children cannot create recursive breakouts")
            if not Path(p["project"]).is_dir():
                raise ValueError("Participant project does not exist")
            if origin and Path(p['project']).resolve() != Path(origin['project']).resolve():
                raise ValueError('Origin-launched agents must stay in the origin project')
            p.setdefault('name', f"{p['provider']}-{index+1}")
            p.setdefault('personality', '')
            p.setdefault('role', 'author' if index == 0 else 'reviewer')
            if not isinstance(p['name'], str) or not p['name'].strip() or not isinstance(p['personality'], str):
                raise ValueError('Agents need a name and a text personality')
            p['context_source'] = 'native fork' if p['source_mode'] == 'fork' else 'explicit shared task/context brief; no inherited conversation'
            p.update(index=index, child_id=None, cursor=0, reviewed_revision="", questions=[],
                     fork_boundary=p.get("boundary", "provider-native snapshot at dispatch; exact turn unverified") if p['source_mode'] == 'fork' else None,
                     tool_configuration="snapshot-only discussion; Codex read-only sandbox / Claude tools disabled")
        if len({p['name'] for p in participants}) != len(participants):
            raise ValueError('Agent names must be unique')
        if issues is not None and (not isinstance(issues, list) or not all(isinstance(i, str) and i.strip() for i in issues)):
            raise ValueError("Issues must be nonempty strings")
        run_id = request_id or uid()
        run = dict(id=run_id, status="working", phase="discuss" if issues else "scan", task=task,
                   origin=origin, shared_context=shared_context, context_revision=digest(shared_context),
                   participants=participants, document=document, simulate=simulate,
                   issues=[dict(id=f"I{i+1}", title=t, status="open", votes={}) for i, t in enumerate(issues or [])],
                   proposal="", proposal_revision="", proposal_history=[], messages=[], decisions=[],
                   epoch=0, events={}, turns=0, scan_turns=0, max_turns=len(participants)*max_rounds,
                   created=time.time(), deadline=time.time()+minutes*60, next_participant=0,
                   result=None, result_revision=0, diagnostic="", selected=bool(issues))
        run.update(version=version, **(v2 or {}))
        run['next_participant'] = self.author_index(run)
        if version == 2:
            run['receipt'] = self.review_receipt(run)
        with self.lock, self.store.db:
            children = {(p['provider'], p['child_id']) for r in self.store.all()
                        for p in r['participants'] if p.get('child_id')}
            if any((p['provider'], p['session_id']) in children for p in participants):
                raise ValueError('Breakout children cannot create recursive breakouts')
            if origin and (origin['provider'], origin['session_id']) in children:
                raise ValueError('Breakout children cannot create recursive breakouts')
            existing = self.store.db.execute("SELECT state FROM breakouts WHERE id=?", (run_id,)).fetchone()
            if existing:
                return json.loads(existing[0])
            self.store.save(run)
        return run

    def recover(self):
        with self.lock, self.store.db:
            for row in self.store.db.execute("SELECT * FROM attempts WHERE status IN ('running','received')").fetchall():
                if row["status"] == "received":
                    self._apply(row)
                else:
                    run = self.store.get(row["run_id"])
                    if run["status"] not in ("ready for review", "failed", "closed"):
                        run["status"] = "paused"
                    run["diagnostic"] = "Interrupted turn has unknown outcome; reconcile it before resume"
                    self.store.db.execute("UPDATE attempts SET status='uncertain' WHERE id=?", (row["id"],))
                    self.store.save(run)

    def _uncertain(self, run_id):
        return self.store.db.execute("SELECT id FROM attempts WHERE run_id=? AND status IN ('running','uncertain')", (run_id,)).fetchall()

    def context(self, run, index):
        p = run["participants"][index]
        return dict(run_id=run["id"], epoch=run["epoch"], phase=run["phase"], task=run["task"],
                    name=p.get('name', p['provider']), personality=p.get('personality', ''),
                    context_source=p.get('context_source', 'native fork'), shared_context=run.get('shared_context', ''),
                    context_revision=run.get('context_revision'),
                    role=p.get('role', "author" if index == 0 else "reviewer"), document=run["document"],
                    issues=run["issues"], proposal=run["proposal"], proposal_revision=run["proposal_revision"],
                    messages=run["messages"][p["cursor"]:], message_cursor=len(run["messages"]),
                    user_decisions=run["decisions"], guidance=run.get('guidance', []),
                    constraints=run.get('context_packet', {}).get('constraints', []),
                    omissions=run.get('context_packet', {}).get('omissions', []),
                    revision_requested=run.get('revision_requested', False),
                    review_question=next((m for m in reversed(run['messages']) if m.get('action') == 'ask'), None)
                    if run['phase'] == 'review' else None)

    def step(self, run_id):
        with self.lock, self.store.db:
            run = self.store.get(run_id)
            if run["status"] != "working" or run_id in self.active or self._uncertain(run_id):
                return False
            v2 = run.get('version', 1) >= 2
            budget_key = 'review_seconds' if run['phase'] == 'review' else 'work_seconds'
            exhausted = ((run[budget_key] <= 0 or (run['review_replies'] >= run['max_review_replies'] if run['phase'] == 'review' else run['turns'] >= run['max_turns']))
                         if v2 else (time.time() >= run['deadline'] or run['turns'] >= run['max_turns']))
            if exhausted:
                self._finish(run, "budget exhausted; partial result")
                return False
            index = run["next_participant"]
            context = self.context(run, index)
            history_bytes = len(json.dumps(run['participants'][index].get('api_history', [])).encode())
            if v2 and len(json.dumps(context).encode()) + history_bytes > run['context_bytes']:
                run['diagnostic'] = 'Turn context exceeds budget; narrow scope explicitly. No decisions were trimmed.'
                self._finish(run, run['diagnostic'])
                return False
            attempt_id = uid()
            self.store.db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)",
                                  (attempt_id, run_id, index, "running", json.dumps(context), None, None, time.time()))
            cancel = threading.Event()
            self.active[run_id] = cancel
            participant = copy.deepcopy(run["participants"][index])
            timeout = max(.01, run[budget_key] if v2 else run['deadline']-time.time())
            started = time.monotonic()
        try:
            adapter = self.adapters['fake' if run['simulate'] else ('api' if participant['provider'] == 'api' else 'live')]
            child, envelope = adapter.turn(participant, context, cancel, timeout)
            validate(envelope)
            if not isinstance(child, str) or not child or child == participant["session_id"]:
                raise ValueError("Invalid child identity")
            if participant.get("child_id") and participant["child_id"] != child:
                raise ValueError("Child identity changed")
            # Persist the receipt before applying/forwarding it. A crash between these commits is recoverable.
            with self.lock, self.store.db:
                self.store.db.execute("UPDATE attempts SET status='received',response=? WHERE id=?",
                                      (json.dumps(dict(child_id=child, envelope=envelope, usage=participant.get('_usage'))), attempt_id))
            with self.lock, self.store.db:
                row = self.store.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
                self._apply(row)
        except Exception as exc:
            with self.lock, self.store.db:
                current = self.store.get(run_id)
                self.store.db.execute("UPDATE attempts SET status='uncertain',error=? WHERE id=? AND status!='applied'",
                                      (str(exc), attempt_id))
                if participant.get('role') == 'advisory' and current['status'] == 'working':
                    current['participants'][index]['unavailable'] = 'Turn outcome uncertain; not retried'
                    self.store.db.execute("UPDATE attempts SET status='advisory_uncertain' WHERE id=?", (attempt_id,))
                    current['next_participant'] = self.next_index(current, index)
                    if current['phase'] == 'scan':
                        current['scan_turns'] += 1
                        if current['scan_turns'] >= len(current['participants']):
                            current['status'] = 'needs your input'
                            current['diagnostic'] = 'Select issues; advisory failure recorded'
                    if current['phase'] == 'review':
                        current['review_queue'] = [i for i in current['review_queue'] if i != index]
                        if not current['review_queue']:
                            self._finish(current, 'Review replies complete; advisory failure recorded')
                        else:
                            current['next_participant'] = current['review_queue'][0]
                elif current["status"] == "working":
                    current["status"] = "paused"
                current["diagnostic"] = f"Turn outcome uncertain: {exc}. Reconciliation required."
                if not v2 and time.time() >= current['deadline'] and current['result'] is None:
                    self._finish(current, 'wall-clock budget exhausted; active turn outcome uncertain')
                self.store.save(current)
        finally:
            with self.lock, self.store.db:
                if v2:
                    current = self.store.get(run_id)
                    current[budget_key] = max(0, current[budget_key] - (time.monotonic()-started))
                    self.store.save(current)
                self.active.pop(run_id, None)
        return True

    def _apply(self, attempt):
        if attempt["status"] == "applied":
            return
        run = self.store.get(attempt["run_id"])
        index = attempt["participant"]
        context = json.loads(attempt["context"])
        receipt = json.loads(attempt["response"])
        envelope = validate(receipt["envelope"])
        p = run["participants"][index]
        p["child_id"] = receipt["child_id"]
        p["cursor"] = context["message_cursor"]
        if p['provider'] == 'api':
            from .protocol import prompt
            p.setdefault('api_history', []).extend([dict(role='user', content=prompt(context)),
                                                    dict(role='assistant', content=json.dumps(envelope))])
            p.setdefault('usage', []).append(receipt.get('usage'))
        stale = context["epoch"] != run["epoch"]
        run["messages"].append(dict(id=attempt["id"], author=p["provider"], epoch=context["epoch"],
                                    participant=index, name=p.get('name', p['provider']),
                                    stale=stale, content=envelope, created=time.time(),
                                    artifact_revision=context['proposal_revision'], phase=context['phase']))
        if context['phase'] == 'review':
            run['review_replies'] += 1
            if envelope['proposal'] or envelope['issues'] or envelope['reviewed_revision']:
                raise ValueError('Review Q&A must not revise or vote on an artifact; use revise explicitly')
            if not stale:
                run['review_queue'] = [i for i in run['review_queue'] if i != index]
                if run['review_queue']:
                    run['next_participant'] = run['review_queue'][0]
                else:
                    self._finish(run, 'Requested review replies are ready')
            self.store.db.execute("UPDATE attempts SET status='applied' WHERE id=?", (attempt['id'],))
            self.store.save(run)
            return
        if context["phase"] == "scan":
            run["scan_turns"] += 1
        else:
            run["turns"] += 1
        if not stale:
            run["next_participant"] = self.next_index(run, index)
        if not stale:
            p['questions'] = envelope['questions']
            p['alternatives'] = envelope.get('alternatives', [])
            p['constraints_kept'] = envelope.get('constraints_kept', [])
            if envelope["proposal"] and index != self.author_index(run):
                raise ValueError("Only the designated author may revise the proposal")
            if envelope["proposal"] and context["phase"] != "scan":
                revision = digest(envelope["proposal"])
                if revision != run["proposal_revision"]:
                    run["proposal"] = envelope["proposal"]
                    run["proposal_revision"] = revision
                    run["proposal_history"].append(dict(revision=revision, text=envelope["proposal"], epoch=run["epoch"]))
                    run['revision_requested'] = False
                    for peer in run["participants"]:
                        peer["reviewed_revision"] = ""
                    for issue in run["issues"]:
                        issue.update(status="open", votes={})
            reviewed = (bool(run["proposal_revision"]) and
                        envelope["reviewed_revision"] == run["proposal_revision"] == context["proposal_revision"])
            if reviewed:
                p["reviewed_revision"] = run["proposal_revision"]
            for incoming in envelope["issues"]:
                issue = next((i for i in run["issues"] if i["id"] == incoming["id"]), None)
                if issue is None:
                    if run["selected"]:
                        raise ValueError("Response addressed an issue outside the selected scope")
                    issue = dict(id=incoming["id"], title=incoming["title"], status="open", votes={})
                    run["issues"].append(issue)
                issue["votes"][str(index)] = dict(incoming, revision=run["proposal_revision"] if reviewed else "")
                self._issue_status(issue, run["proposal_revision"], [p['index'] for p in self.required(run)])
            if context["phase"] == "scan" and run["scan_turns"] >= len(run['participants']):
                run["status"] = "needs your input"
                run["diagnostic"] = "Select issues to discuss"
            elif run["status"] == "working" and self._ready(run):
                self._finish(run, "all required participants reviewed the final proposal")
            elif run["turns"] >= run["max_turns"] and run["status"] == "working":
                self._finish(run, "round budget exhausted; partial result")
        self.store.db.execute("UPDATE attempts SET status='applied' WHERE id=?", (attempt["id"],))
        self.store.save(run)

    @staticmethod
    def _issue_status(issue, revision, required=None):
        required = required or [0, 1]
        votes = [issue['votes'][str(i)] for i in required if str(i) in issue['votes']]
        current = [v for v in votes if revision and v["revision"] == revision]
        if any(v["status"] == "needs user" for v in current):
            issue["status"] = "needs user"
        elif any(v["status"] == "needs evidence" for v in current):
            issue["status"] = "needs evidence"
        elif len(current) == len(required) and all(v["status"] == "agreed" and not v["objection"] for v in current) and len({v['resolution'] for v in current}) == 1:
            issue["status"] = "agreed"
        elif votes:
            issue["status"] = "proposed" if any(v["resolution"] for v in votes) else "open"
        else:
            issue["status"] = "open"

    @staticmethod
    def _ready(run):
        return (bool(run["proposal_revision"]) and bool(run["issues"]) and
                all(p["reviewed_revision"] == run["proposal_revision"] for p in run["participants"] if p.get('role') != 'advisory') and
                all(p.get('child_id') or p.get('unavailable') for p in run['participants']) and
                all(i["status"] in ("agreed", "needs evidence", "needs user") for i in run["issues"]))

    def _finish(self, run, reason):
        run["status"] = "ready for review"
        run["result_revision"] += 1
        run["result"] = dict(revision=run["result_revision"], reason=reason, partial=not self._ready(run),
                             proposal=run["proposal"], proposal_revision=run["proposal_revision"],
                             issues=copy.deepcopy(run["issues"]), decisions=copy.deepcopy(run["decisions"]),
                             questions=list(dict.fromkeys(q for p in run['participants'] for q in p['questions'])),
                             document_freshness=freshness(run["document"]), created=time.time(),
                             review_replies=[m for m in run['messages'] if m.get('phase') == 'review'],
                             feedback=run.get('feedback', []), acceptance=run.get('acceptance'),
                             unavailable=[p['name'] for p in run['participants'] if p.get('unavailable')])
        run['result']['alternatives'] = list(dict.fromkeys(x for p in run['participants'] for x in p.get('alternatives', [])))
        run['result']['constraints_kept'] = list(dict.fromkeys(x for p in run['participants'] for x in p.get('constraints_kept', [])))
        run.setdefault('results', {})[str(run['result_revision'])] = copy.deepcopy(run['result'])
        run.pop('notification', None)
        if run.get('version', 1) >= 2 and run.get('close_after_hours') and run.get('close_at') is None:
            run['close_at'] = time.time() + run['close_after_hours'] * 3600
        recipients = [run['origin']] if run.get('origin') else run['participants']
        for p in recipients:
            self.store.db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?,?)",
                                  (run["id"], run["result_revision"], p["provider"], p["session_id"], "queued"))
        self.store.save(run)

    def control(self, run_id, action, text=None, event_id=None, issue_ids=None):
        with self.lock:
            current = self.store.get(run_id)
            if current.get('version', 1) >= 2 and action in ('end', 'cancel'):
                return self.review_control(run_id, 'close', text=text, event_id=event_id)
            if current.get('version', 1) >= 2 and current['status'] == 'closed':
                raise ValueError('Breakout is closed; start a follow-up')
        with self.lock, self.store.db:
            run = self.store.get(run_id)
            if event_id and event_id in run["events"]:
                return run
            if action == "pause":
                if run["status"] != "working":
                    raise ValueError("Only working breakouts can be paused")
                run["status"] = "paused"
                if run_id in self.active:
                    self.active[run_id].set()
            elif action == "resume":
                if run["status"] != "paused":
                    raise ValueError("Only paused breakouts can resume")
                if self._uncertain(run_id) or run_id in self.active:
                    raise ValueError("Reconcile the active/uncertain turn before resuming")
                run["status"] = "working"
                run["diagnostic"] = ""
            elif action in ("end", "cancel"):
                if run['result'] is not None:
                    return run
                if run_id in self.active:
                    self.active[run_id].set()
                run["epoch"] += 1
                self._finish(run, "ended by user; partial result")
            elif action == "select":
                if run["phase"] != "scan" or run["status"] != "needs your input":
                    raise ValueError("No issue selection pending")
                if not issue_ids or not set(issue_ids) <= {i["id"] for i in run["issues"]}:
                    raise ValueError("Select valid issue IDs")
                run["issues"] = [dict(i, status="open", votes={}) for i in run["issues"] if i["id"] in issue_ids]
                run.update(phase="discuss", status="working", selected=True, next_participant=0)
            elif action in ("steer", "decide"):
                if run["status"] not in ("working", "paused", "needs your input"):
                    raise ValueError("This breakout is finished; start a new review")
                if not text or not text.strip():
                    raise ValueError("An explicit user update is required")
                run["epoch"] += 1
                event_id = event_id or uid()
                run["messages"].append(dict(id=event_id, author="user", epoch=run["epoch"],
                                            content={"message": text}, created=time.time()))
                if action == "decide":
                    run["decisions"].append(dict(id=event_id, text=text, epoch=run["epoch"]))
                # Conservatively invalidate all conclusions; no model decides which user choices matter.
                run["proposal"] = ""
                run["proposal_revision"] = ""
                for p in run["participants"]:
                    p["reviewed_revision"] = ""
                    p['questions'] = []
                for issue in run["issues"]:
                    issue.update(status="open", votes={})
                run["next_participant"] = self.author_index(run)
            else:
                raise ValueError("Unknown control action")
            if event_id:
                run["events"][event_id] = action
            self.store.save(run)
            return run

    def reconcile(self, attempt_id, receipt=None, not_started=False):
        """Human/provider verification is required; never infer that an uncertain turn failed."""
        with self.lock, self.store.db:
            row = self.store.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None or row["status"] != "uncertain" or row["run_id"] in self.active:
                raise ValueError("Attempt is not eligible for reconciliation")
            if not_started == (receipt is not None):
                raise ValueError("Supply either a verified receipt or verified-not-started")
            if not_started:
                self.store.db.execute("UPDATE attempts SET status='not_started' WHERE id=?", (attempt_id,))
            else:
                validate(receipt["envelope"])
                run = self.store.get(row["run_id"])
                participant = run["participants"][row["participant"]]
                child = receipt.get("child_id")
                if not child or child == participant["session_id"] or (participant["child_id"] and child != participant["child_id"]):
                    raise ValueError("Receipt must identify the persistent child session")
                self.store.db.execute("UPDATE attempts SET status='received',response=? WHERE id=?", (json.dumps(receipt), attempt_id))
                self._apply(self.store.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone())
            return self.store.get(row["run_id"])

    def mailbox(self, provider, session_id):
        with self.lock:
            rows = self.store.db.execute("SELECT * FROM deliveries WHERE provider=? AND session_id=? AND status='queued'",
                                         (provider, session_id)).fetchall()
            return [dict(run_id=r["run_id"], revision=r["revision"], status=r["status"],
                         result=self.store.get(r['run_id']).get('results', {}).get(str(r['revision']), self.store.get(r['run_id'])['result'])) for r in rows]

    def acknowledge(self, run_id, revision, provider, session_id):
        with self.lock, self.store.db:
            row = self.store.db.execute("SELECT status FROM deliveries WHERE run_id=? AND revision=? AND provider=? AND session_id=?",
                                        (run_id, revision, provider, session_id)).fetchone()
            if row is None:
                raise ValueError("Unknown delivery")
            self.store.db.execute("UPDATE deliveries SET status='received' WHERE run_id=? AND revision=? AND provider=? AND session_id=?",
                                  (run_id, revision, provider, session_id))
            return {"status": "received"}
