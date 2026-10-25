"""Small CLI; machine-readable JSON by default, Markdown exports for review."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from . import __version__
from .daemon import request, serve
from .documents import snapshot
from .providers import doctor
from .render import result_markdown, transcript_markdown
from .store import home_path, uid


def parser():
    p = argparse.ArgumentParser(prog="breakout", description="Local Codex + Claude document breakouts")
    p.add_argument("--home", type=Path, default=home_path(), help="Private runtime state directory")
    p.add_argument("--version", action="version", version=f"breakout {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    daemon = sub.add_parser("daemon")
    daemon.add_argument("action", choices=["start", "serve", "stop", "status"])
    sub.add_parser("doctor")
    sub.add_parser("models")
    sub.add_parser('upgrade', help='Upgrade an idle coordinator and refresh owned Codex/Claude skills')
    demo = sub.add_parser('demo', help='Run a deterministic review without sockets or model calls')
    demo.add_argument('--document', required=True)
    probe = sub.add_parser('probe', help='Create disposable sessions and test native context inheritance')
    probe.add_argument('--provider', choices=['codex', 'claude'], required=True)
    probe.add_argument('--model', required=True)
    probe.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh', 'max', 'ultra'])
    trial = sub.add_parser('trial', help='Run a bounded live review using passed disposable probe parents')
    trial.add_argument('--document', required=True)
    trial.add_argument('--codex-model', default='gpt-6.1-sol')
    trial.add_argument('--claude-model', default='claude-opus-5-5')
    trial.add_argument('--effort', default='medium', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    sub.add_parser("sessions")
    sub.add_parser("list")
    register = sub.add_parser("register")
    register.add_argument("--provider", choices=["codex", "claude"], required=True)
    register.add_argument("--session", required=True)
    register.add_argument("--project", default=os.getcwd())
    register.add_argument("--title", required=True)
    register.add_argument("--activity", default="registered")
    register.add_argument("--boundary", default="registered checkpoint; exact turn unknown")
    register.add_argument("--model")
    register.add_argument("--child", action="store_true")
    start = sub.add_parser("start")
    start.add_argument("--document", required=True)
    start.add_argument("--snapshot", help="Local export for an authenticated URL")
    start.add_argument("--source-revision")
    start.add_argument("--comments-complete", action="store_true")
    start.add_argument("--task", required=True)
    start.add_argument("--issue", action="append", help="Explicit scope; omit to scan and select")
    start.add_argument("--simulate", action="store_true")
    start.add_argument("--codex-session")
    start.add_argument("--claude-session")
    start.add_argument("--codex-model")
    start.add_argument("--claude-model")
    start.add_argument('--codex-effort', choices=['low', 'medium', 'high', 'xhigh', 'max', 'ultra'])
    start.add_argument('--claude-effort', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    start.add_argument("--max-rounds", type=int, default=4)
    start.add_argument("--minutes", type=float, default=20)
    start.add_argument("--request-id", default=None, help="Stable ID for an idempotent start retry")
    launch = sub.add_parser('launch', help='Spawn 2–4 agents; return to the invoking session and keep the team open for review')
    launch.add_argument('--from-provider', choices=['codex', 'claude'], required=True)
    launch.add_argument('--from-session', required=True)
    launch.add_argument('--project', type=Path, default=Path.cwd(), help='Project for registering a known origin if its hook has not registered yet')
    launch.add_argument('--agent', action='append',
                        help='JSON: provider, model, effort, name, personality, role; repeat 2–4 times')
    launch.add_argument('--config', type=Path)
    launch.add_argument('--team')
    launch.add_argument('--followup', help='Carry the result and discussion of an earlier run into this linked follow-up')
    launch.add_argument('--allow-connection', action='append', default=[])
    launch.add_argument('--review-replies', type=int)
    launch.add_argument('--close-after-hours', type=float)
    launch.add_argument('--context-bytes', type=int)
    launch.add_argument('--constraint', action='append', default=[])
    launch.add_argument('--omission', action='append', default=[])
    launch.add_argument('--task', required=True)
    context = launch.add_mutually_exclusive_group()
    context.add_argument('--context', default='', help='Explicit context shared with both workers')
    context.add_argument('--context-file', type=Path, help='UTF-8 context handoff assembled by the origin agent')
    launch.add_argument('--document')
    launch.add_argument('--snapshot')
    launch.add_argument('--source-revision')
    launch.add_argument('--comments-complete', action='store_true')
    launch.add_argument('--issue', action='append')
    launch.add_argument('--simulate', action='store_true')
    launch.add_argument('--max-rounds', type=int)
    launch.add_argument('--minutes', type=float)
    launch.add_argument('--request-id')
    for name in ("status", "pause", "resume", "end", "cancel", "steer", "decide", "select", "result", "transcript"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run_id")
        if name in ("steer", "decide"):
            cmd.add_argument("text")
            cmd.add_argument("--event-id", default=None)
        if name == "select":
            cmd.add_argument("issue_ids", nargs="+")
        if name in ("result", "transcript"):
            cmd.add_argument("--output", type=Path)
        if name == 'result':
            cmd.add_argument('--revision', type=int)
    for name in ("mailbox", "ack", "open"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--provider", choices=["codex", "claude"], required=True)
        cmd.add_argument("--session", required=True)
        if name == "ack":
            cmd.add_argument("run_id")
            cmd.add_argument("revision", type=int)
    for name in ('ask', 'revise', 'feedback', 'accept', 'close', 'extend'):
        cmd = sub.add_parser(name)
        cmd.add_argument('run_id')
        cmd.add_argument('--provider', choices=['codex', 'claude'], required=True)
        cmd.add_argument('--session', required=True)
        cmd.add_argument('--event-id')
        cmd.add_argument('--revision', help='Expected artifact SHA256; rejects stale feedback')
        if name in ('ask', 'revise', 'feedback'):
            cmd.add_argument('text')
            cmd.add_argument('--source', default='origin chat')
            cmd.add_argument('--source-revision')
        if name == 'ask':
            cmd.add_argument('--to', default='team', dest='target')
        if name == 'extend':
            cmd.add_argument('--replies', type=int, default=0)
            cmd.add_argument('--rounds', type=int, default=0)
            cmd.add_argument('--minutes', type=float, default=0)
    library = sub.add_parser('library', help='Editable project Markdown guidance, evidence and undo')
    library.add_argument('action', choices=['list', 'show', 'preview', 'save', 'evaluate', 'approve', 'undo', 'disable', 'delete', 'outcome'])
    library.add_argument('name', nargs='?')
    library.add_argument('--project', type=Path, default=Path.cwd())
    library.add_argument('--kind', choices=['learnings', 'personalities'], default='learnings')
    library.add_argument('--file', type=Path, help='Body Markdown, soul source, or evaluation JSON')
    library.add_argument('--direct', action='store_true', help='Explicit human preference; otherwise save as inferred candidate')
    library.add_argument('--source', default='origin instruction')
    library.add_argument('--version', type=int)
    library.add_argument('--run-id')
    library.add_argument('--accepted', action='store_true')
    library.add_argument('--constraint-lost', action='store_true')
    library.add_argument('--metric-worse', action='store_true')
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("attempt_id")
    group = reconcile.add_mutually_exclusive_group(required=True)
    group.add_argument("--receipt", type=Path, help="Verified {child_id,envelope} JSON")
    group.add_argument("--verified-not-started", action="store_true")
    for name in ("install", "uninstall"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--provider", choices=["codex", "claude"], required=True)
        cmd.add_argument("--config-dir", type=Path, required=True, help="Provider config root; explicit to avoid changing the wrong scope")
    hook = sub.add_parser("hook")
    hook.add_argument("--provider", choices=["codex", "claude"], required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    call = lambda method, **params: request(method, args.home, **params)
    try:
        if args.command == "doctor":
            from .setup import host_report
            output = host_report(args.home)
        elif args.command == 'library':
            from .library import Library
            lib = Library(args.project)
            if args.action != 'list' and not args.name:
                raise ValueError('Supply a library entry name')
            if args.action in ('save', 'preview', 'evaluate') and not args.file:
                raise ValueError('This action requires --file')
            if args.action == 'list':
                output = lib.list(args.kind)
            elif args.action == 'show':
                output = lib.get(args.kind, args.name, args.version)
            elif args.action == 'preview':
                output = lib.preview(args.name, args.file)
            elif args.action == 'save':
                output = lib.save(args.kind, args.name, args.file.read_text(), source=args.source, direct=args.direct)
            elif args.action == 'evaluate':
                output = lib.evaluate(args.kind, args.name, json.loads(args.file.read_text()))
            elif args.action == 'approve':
                output = lib.approve(args.kind, args.name)
            elif args.action == 'undo':
                output = lib.undo(args.kind, args.name, args.version)
            elif args.action == 'disable':
                output = lib.disable(args.kind, args.name)
            elif args.action == 'delete':
                output = lib.delete(args.kind, args.name)
            elif args.action == 'outcome':
                if not args.run_id:
                    raise ValueError('A trial outcome needs --run-id')
                run = call('get', run_id=args.run_id)
                card = lib.get(args.kind, args.name)
                pinned = run.get('guidance', []) + [p['personality_card'] for p in run['participants'] if p.get('personality_card')]
                if not run.get('result') or not any(r['metadata']['name'] == args.name and r['text'] == card['text'] and r['metadata']['scope'] == str(args.project.resolve()) for r in pinned):
                    raise ValueError('This completed run did not use the trial guidance')
                output = lib.outcome(args.kind, args.name, args.run_id, args.accepted, args.constraint_lost, args.metric_worse)
        elif args.command == 'models':
            from .discovery import models
            output = models()
        elif args.command == 'upgrade':
            from .setup import ensure
            from .integration import install
            output = {'coordinator': ensure(args.home), 'skills': {}}
            for provider in ('codex', 'claude'):
                config_dir = Path.home() / ('.' + provider)
                if (config_dir / 'breakout-install.json').exists():
                    output['skills'][provider] = install(provider, config_dir, args.home)
                else:
                    output['skills'][provider] = {'status': 'not installed; unchanged'}
        elif args.command == 'probe':
            from .probe import probe
            output = probe(args.provider, args.model, args.home, effort=args.effort)
        elif args.command == 'trial':
            from .trial import trial
            output = trial(args.home, args.document, args.codex_model, args.claude_model, args.effort)
        elif args.command == 'demo':
            from .engine import Engine
            from .store import Store
            import tempfile
            with tempfile.TemporaryDirectory(prefix='breakout-demo-') as state:
                store = Store(state)
                engine = Engine(store)
                participants = [dict(provider=p, session_id='demo-parent-' + p, model='simulated', project=os.getcwd())
                                for p in ('codex', 'claude')]
                run = engine.create(snapshot(args.document), 'Review acceptance criteria', participants,
                                    issues=['Acceptance criteria', 'Requires experiment', 'User decision'], simulate=True)
                while engine.step(run['id']):
                    pass
                run = store.get(run['id'])
                print(result_markdown(run))
                store.db.close()
                return
        elif args.command == "daemon":
            if args.action == "serve":
                serve(args.home)
                return
            if args.action == "start":
                try:
                    output = call("ping")
                except PermissionError:
                    raise
                except (OSError, ValueError):
                    args.home.mkdir(parents=True, exist_ok=True, mode=0o700)
                    args.home.chmod(0o700)
                    log_path = args.home / "daemon.log"
                    with log_path.open("a") as log:
                        log_path.chmod(0o600)
                        proc = subprocess.Popen([sys.executable, "-m", "breakout", "--home", str(args.home.resolve()), "daemon", "serve"],
                                                cwd=Path(__file__).resolve().parent.parent,
                                                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                    for _ in range(50):
                        try:
                            output = call("ping")
                            break
                        except OSError:
                            if proc.poll() is not None:
                                raise ValueError(f"Daemon failed to start; inspect {log_path}")
                            time.sleep(.1)
                    else:
                        raise ValueError(f"Daemon startup timed out; inspect {log_path}")
                if output.get('protocol_version') != 2:
                    from .setup import ensure
                    output = ensure(args.home)
            else:
                output = call("shutdown" if args.action == "stop" else "ping")
        elif args.command == "register":
            output = call("register", provider=args.provider, session_id=args.session, project=str(Path(args.project).resolve()),
                          title=args.title, activity=args.activity, boundary=args.boundary, model=args.model,
                          child=args.child or os.environ.get("BREAKOUT_CHILD") == "1")
        elif args.command in ("sessions", "list"):
            output = call(args.command)
        elif args.command == 'launch':
            if os.environ.get('BREAKOUT_CHILD') == '1':
                raise ValueError('Breakout children cannot start breakouts')
            if args.snapshot and not args.document:
                raise ValueError('--snapshot requires --document')
            document = snapshot(args.document, args.snapshot, args.source_revision, args.comments_complete) if args.document else None
            shared_context = args.context_file.read_text() if args.context_file else args.context
            from .setup import ensure
            ensure(args.home)
            sessions = call('sessions')
            if not any(s['provider'] == args.from_provider and s['session_id'] == args.from_session for s in sessions):
                call('register', provider=args.from_provider, session_id=args.from_session,
                     project=str(args.project.resolve()), title=args.project.name + ' · ' + args.from_session[:8],
                     activity='explicit origin launch', child=False, boundary='origin supplied known session; provider fork boundary at dispatch')
            recipe = None
            limits = {}
            if args.team or args.config:
                if not args.team or args.agent:
                    raise ValueError('Use --team with --config, or explicit --agent specifications')
                from .config import resolve
                sessions = call('sessions')
                origin = next((s for s in sessions if s['provider'] == args.from_provider and s['session_id'] == args.from_session), None)
                if not origin:
                    raise ValueError('Origin session is not registered')
                recipe = resolve(args.config or Path(origin['project']) / '.breakout/config.toml', args.team, origin['project'])
                agents, limits = recipe['agents'], recipe['limits']
            else:
                agents = [json.loads(a) for a in (args.agent or [])]
            run = call('launch', provider=args.from_provider, session_id=args.from_session,
                       timeout=120,
                       agents=agents, task=args.task, document=document,
                       shared_context=shared_context, issues=args.issue, simulate=args.simulate,
                       max_rounds=args.max_rounds if args.max_rounds is not None else limits.get('rounds', 4),
                       minutes=args.minutes if args.minutes is not None else limits.get('wall_clock_minutes', 20),
                       review_replies=args.review_replies if args.review_replies is not None else limits.get('review_replies', 10),
                       close_after_hours=args.close_after_hours if args.close_after_hours is not None else limits.get('close_after_hours'),
                       context_bytes=args.context_bytes or limits.get('context_bytes', 1048576),
                       omissions=args.omission, constraints=args.constraint, recipe=recipe, parent_run=args.followup,
                       allow_connections=args.allow_connection, request_id=args.request_id or uid())
            output = {k: run[k] for k in ('id', 'status', 'phase', 'origin', 'participants', 'simulate', 'receipt')}
        elif args.command == "start":
            if os.environ.get("BREAKOUT_CHILD") == "1":
                raise ValueError("Breakout children cannot start breakouts")
            participants = []
            sessions = [] if args.simulate else call("sessions")
            for provider in ("codex", "claude"):
                session_id = getattr(args, provider + "_session")
                model = getattr(args, provider + "_model")
                if args.simulate:
                    participants.append(dict(provider=provider, session_id=session_id or "fake-parent-" + provider,
                                             model=model or "simulated", project=os.getcwd()))
                else:
                    if not session_id or not model:
                        raise ValueError("Live mode requires both selected sessions and explicit models")
                    selected = next((s for s in sessions if s["provider"] == provider and s["session_id"] == session_id), None)
                    if selected is None:
                        raise ValueError(f"Register/select the {provider} session first")
                    participants.append(dict(selected, model=model, effort=getattr(args, provider + '_effort')))
            document = snapshot(args.document, args.snapshot, args.source_revision, args.comments_complete)
            run = call("create", document=document, task=args.task, participants=participants,
                       issues=args.issue, simulate=args.simulate, max_rounds=args.max_rounds,
                       minutes=args.minutes, request_id=args.request_id or uid())
            output = {k: run[k] for k in ("id", "status", "phase", "participants", "simulate")}
        elif args.command == "status":
            output = call("get", run_id=args.run_id)
        elif args.command in ("pause", "resume", "end", "cancel", "steer", "decide", "select"):
            run = call("control", run_id=args.run_id, action=args.command, text=getattr(args, "text", None),
                       event_id=getattr(args, "event_id", None), issue_ids=getattr(args, "issue_ids", None))
            output = {k: run[k] for k in ("id", "status", "phase", "diagnostic")}
        elif args.command in ("result", "transcript"):
            run = call("get", run_id=args.run_id)
            if args.command == 'result' and args.revision:
                result = run.get('results', {}).get(str(args.revision))
                if result is None and run.get('result', {}).get('revision') == args.revision:
                    result = run['result']
                if result is None:
                    raise ValueError('Unknown result revision')
                run['result'] = result
            text = result_markdown(run) if args.command == "result" else transcript_markdown(run)
            if args.output:
                args.output.write_text(text)
                output = {"written": str(args.output.resolve())}
            else:
                print(text, end="")
                return
        elif args.command in ('ask', 'revise', 'feedback', 'accept', 'close', 'extend'):
            params = {k: getattr(args, k) for k in ('text', 'event_id', 'target', 'revision', 'replies', 'rounds', 'minutes', 'source', 'source_revision') if hasattr(args, k)}
            run = call('review', run_id=args.run_id, action=args.command, provider=args.provider, session_id=args.session, **params)
            output = {k: run.get(k) for k in ('id', 'status', 'phase', 'diagnostic', 'acceptance')}
        elif args.command in ("mailbox", 'open'):
            output = call(args.command, provider=args.provider, session_id=args.session)
        elif args.command == "ack":
            output = call("acknowledge", run_id=args.run_id, revision=args.revision,
                          provider=args.provider, session_id=args.session)
        elif args.command == "reconcile":
            receipt = json.loads(args.receipt.read_text()) if args.receipt else None
            output = call("reconcile", attempt_id=args.attempt_id, receipt=receipt, not_started=args.verified_not_started)
        elif args.command in ("install", "uninstall"):
            from .integration import install, uninstall
            output = (install if args.command == "install" else uninstall)(args.provider, args.config_dir, args.home)
        elif args.command == "hook":
            from .integration import hook
            hook(args.provider, args.home)
            return
        print(json.dumps(output, indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError) as exc:
        print(f"breakout: {exc}", file=sys.stderr)
        raise SystemExit(1)
