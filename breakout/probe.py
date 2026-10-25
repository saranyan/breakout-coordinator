"""Opt-in live feasibility probe using disposable native parent sessions."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from .integration import atomic_write
from .providers import CLIAdapter
from .store import uid


def run_command(command, text, cwd, timeout=90):
    proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True,
                            env=dict(os.environ, BREAKOUT_CHILD='1'))
    try:
        stdout, _ = proc.communicate(text, timeout=timeout)
        if proc.returncode:
            raise ValueError(f'Provider command exited {proc.returncode}; inspect provider login/runtime locally')
        return stdout
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


def parse_text(provider, raw):
    if provider == 'claude':
        data = json.loads(raw)
        if data.get('is_error'):
            raise ValueError('Claude reported an API error')
        return data['session_id'], data['result']
    child, message, done = None, None, False
    for line in raw.splitlines():
        event = json.loads(line)
        if event.get('type') in ('error', 'turn.failed'):
            raise ValueError('Codex reported a failed turn')
        if event.get('type') == 'thread.started':
            child = event['thread_id']
        if event.get('type') == 'turn.completed':
            done = True
        if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
            message = event['item']['text']
    if not child or message is None or not done:
        raise ValueError('Provider returned an incomplete response')
    return child, message


def probe(provider, model, home, effort=None):
    effort_flags = CLIAdapter.effort_flags(provider, effort)
    root = Path(home).expanduser().resolve() / 'probes' / uid()
    root.mkdir(parents=True, mode=0o700)
    report = dict(provider=provider, model=model, effort=effort, status='running', project=str(root),
                  started=time.time(), checks={}, delivery='queued mailbox; no verified interactive wakeup')
    report_path = root / 'report.json'
    atomic_write(report_path, json.dumps(report, indent=2))
    adapter = CLIAdapter()
    restrictions = []

    def execute(command, text):
        if provider == 'codex':
            command[2:2] = restrictions
        return parse_text(provider, run_command(command, text, root))

    try:
        if provider == 'codex':
            restrictions = adapter.codex_restrictions(str(root))
            command = ['codex', 'exec', '-s', 'read-only', '-c', 'approval_policy="never"',
                       '--skip-git-repo-check', '--model', model, '--json', '-']
            command[-1:-1] = effort_flags
        else:
            command = ['claude', '-p', '--model', model, '--output-format', 'json', '--tools', '',
                       '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--permission-mode', 'plan',
                       '--permission-prompts', 'none']
            command += effort_flags
        marker = 'BREAKOUT-PARENT-' + uid()
        parent, text = execute(command, 'Disposable integration test. Remember this marker: ' + marker +
                               '. Return the marker only. Never use tools.')
        report['parent_id'] = parent
        report['checks']['parent_created'] = marker in text
        report['source_boundary'] = {'kind': 'completed disposable parent turn', 'completed_at': time.time()}
        participant = dict(provider=provider, session_id=parent, model=model, effort=effort, project=str(root))
        child, text = execute(adapter.command(participant, structured=False),
                              'Return only the marker you were explicitly asked to remember earlier. Do not use tools.')
        report['child_id'] = child
        report['checks']['native_fork_inherited_context'] = child != parent and marker in text
        if child == parent:
            raise ValueError('Fork reused the original session')
        atomic_write(report_path, json.dumps(report, indent=2))
        participant['child_id'] = child
        child_marker = 'BREAKOUT-CHILD-' + uid()
        continued, _ = execute(adapter.command(participant, structured=False),
                                'Also remember this new marker: ' + child_marker + '. Return it only. Do not use tools.')
        again, text = execute(adapter.command(participant, structured=False),
                              'List all markers you were explicitly asked to remember. Do not use tools.')
        report['checks']['persistent_child_continuation'] = continued == again == child and marker in text and child_marker in text
        participant['child_id'] = parent  # resume the test parent, never an unrelated user's session
        original, text = execute(adapter.command(participant, structured=False),
                                 'List all markers you were explicitly asked to remember. Do not use tools.')
        report['checks']['original_history_isolated'] = original == parent and marker in text and child_marker not in text
        report['status'] = 'passed' if all(report['checks'].values()) else 'failed'
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        report['status'] = 'blocked'
        report['error'] = str(exc)
    report['finished'] = time.time()
    atomic_write(report_path, json.dumps(report, indent=2))
    report['report_path'] = str(report_path)
    return report
