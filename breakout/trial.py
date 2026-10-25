"""Bounded live review using only verified disposable probe parents."""
import json
from pathlib import Path
import subprocess
import sys
import time

from .daemon import request
from .documents import snapshot
from .integration import atomic_write
from .render import result_markdown, transcript_markdown
from .store import uid


CHECKS = ('parent_created', 'native_fork_inherited_context',
          'persistent_child_continuation', 'original_history_isolated')


def select_fixtures(home, models, effort):
    reports = []
    for path in (Path(home) / 'probes').glob('*/report.json'):
        report = json.loads(path.read_text())
        if (report.get('status') == 'passed' and report.get('effort') == effort
                and all(report.get('checks', {}).get(c) is True for c in CHECKS)):
            reports.append(report)
    participants = []
    for provider in ('codex', 'claude'):
        matches = [r for r in reports if r.get('provider') == provider and r.get('model') == models[provider]]
        if not matches:
            raise ValueError(f'No passed {provider} probe for {models[provider]} at {effort} effort')
        report = max(matches, key=lambda r: r['finished'])
        participants.append(dict(provider=provider, session_id=report['parent_id'],
                                 model=report['model'], effort=effort, project=report['project'],
                                 title='Disposable breakout probe parent',
                                 boundary='Completed probe; independent parent history verified'))
    return participants


def save_artifacts(root, run):
    atomic_write(root / 'run.json', json.dumps(run, indent=2))
    atomic_write(root / 'discussion.md', transcript_markdown(run))
    if run.get('result'):
        atomic_write(root / 'result.md', result_markdown(run))


def trial(home, document, codex_model='gpt-6.1-sol', claude_model='claude-opus-5-5', effort='medium'):
    home = Path(home).expanduser().resolve()
    participants = select_fixtures(home, {'codex': codex_model, 'claude': claude_model}, effort)
    document = snapshot(document)
    # Short IDs keep the Unix socket pathname within macOS's sockaddr_un limit.
    root = home / 'trials' / uid()[:8]
    root.mkdir(parents=True, mode=0o700)
    summary = dict(status='starting', state_directory=str(root), participants=participants,
                   max_rounds=2, minutes=5, delivery='queued; not acknowledged by original sessions')
    atomic_write(root / 'trial.json', json.dumps(summary, indent=2))
    process = None
    run_id = None
    try:
        with (root / 'daemon.log').open('w') as log:
            process = subprocess.Popen([sys.executable, '-m', 'breakout', '--home', str(root), 'daemon', 'serve'],
                                       cwd=Path(__file__).resolve().parent.parent,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(50):
            try:
                request('ping', root, timeout=.5)
                break
            except OSError:
                if process.poll() is not None:
                    raise ValueError(f'Trial daemon could not start; inspect {root / "daemon.log"}')
                time.sleep(.1)
        else:
            raise ValueError('Trial daemon startup timed out')
        for participant in participants:
            request('register', root, **participant)
        run = request('create', root, document=document, participants=participants,
                      task='Review this local proposal. Return corrected document text and concrete resolutions. '
                           'Discuss only; do not edit files. When agreeing, reuse the exact proposed resolution '
                           'text from the issue ledger and review the current proposal hash. Identify evidence gaps honestly.',
                      issues=['Avoid replaying provider turns with uncertain outcomes',
                              'Define evidence-based completion and stale-document handling'],
                      simulate=False, max_rounds=2, minutes=5)
        run_id = run['id']
        summary.update(status='working', run_id=run_id)
        atomic_write(root / 'trial.json', json.dumps(summary, indent=2))
        print(f'Live trial {run_id}: at most four turns; results in {root}', file=sys.stderr, flush=True)
        deadline = time.monotonic() + 330
        while True:
            run = request('get', root, run_id=run_id)
            save_artifacts(root, run)
            if run['status'] != 'working':
                break
            if time.monotonic() >= deadline:
                request('control', root, run_id=run_id, action='end')
                run = request('get', root, run_id=run_id)
                save_artifacts(root, run)
                break
            time.sleep(.5)
        summary.update(status=run['status'], turns=run['turns'], diagnostic=run['diagnostic'],
                       partial=run['result']['partial'] if run.get('result') else True,
                       result_path=str(root / 'result.md') if run.get('result') else None,
                       transcript_path=str(root / 'discussion.md'),
                       deliveries=run.get('deliveries', []))
    except (OSError, ValueError, KeyError) as exc:
        summary.update(status='blocked', error=str(exc))
    except KeyboardInterrupt:
        summary.update(status='interrupted', error='User interrupted the trial; inspect persisted attempts before any retry')
    finally:
        if process is not None and process.poll() is None:
            try:
                request('shutdown', root, timeout=2)
            except (OSError, ValueError):
                process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                summary['shutdown'] = 'forced; inspect uncertain attempts before resume'
        atomic_write(root / 'trial.json', json.dumps(summary, indent=2))
    return summary
