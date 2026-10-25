"""Provider-specific commands. Never substitute a fresh chat or a different model."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from .protocol import prompt, validate
from .store import uid


class Interrupted(Exception):
    pass


class FakeAdapter:
    def turn(self, participant, context, cancel, timeout):
        if cancel.is_set():
            raise Interrupted("Cancelled")
        if context['phase'] == 'review':
            return participant.get('child_id') or 'fake-' + uid(), validate(dict(
                message=f"Simulated answer from {participant['name']}: " + context['review_question']['content']['message'],
                issues=[], proposal='', reviewed_revision='', questions=[]))
        issues = context["issues"] or [{"id": "I1", "title": "Clarify acceptance criteria"}]
        result = []
        for issue in issues:
            status = "open" if context["phase"] == "scan" else "agreed"
            if "experiment" in issue["title"].lower():
                status = "needs evidence"
            if "decision" in issue["title"].lower():
                status = "needs user"
            result.append(dict(id=issue["id"], title=issue["title"], status=status,
                               resolution="Make acceptance criteria explicit.", objection="",
                               evidence=["document:" + context["document"]["sha256"]]))
        proposal = ""
        if context["phase"] != "scan" and context['role'] == 'author' and (not context["proposal"] or context.get('revision_requested')):
            proposal = (context['proposal'] or context["document"]["text"]) + "\n\nProposed clarification: validate acceptance criteria before release.\n"
        return participant.get("child_id") or "fake-" + uid(), validate({
            "message": "Simulated review (no model calls).", "issues": result,
            "proposal": proposal, "reviewed_revision": context["proposal_revision"] if not proposal else "",
            "questions": [i["title"] for i in result if i["status"] == "needs user"]})


class CLIAdapter:
    def preflight(self, participant):
        provider = participant['provider']
        try:
            version = subprocess.run([provider, '--version'], capture_output=True, text=True, timeout=5, check=True)
            auth_cmd = ['codex', 'login', 'status'] if provider == 'codex' else ['claude', 'auth', 'status', '--json']
            auth = subprocess.run(auth_cmd, capture_output=True, text=True, timeout=10)
            logged_in = auth.returncode == 0
            if provider == 'claude' and logged_in:
                logged_in = json.loads(auth.stdout).get('loggedIn') is True
            if not logged_in:
                raise ValueError(provider + ' is not authenticated in the coordinator environment; log in there first')
            help_cmd = ['codex', 'exec', 'fork', '--help'] if provider == 'codex' else ['claude', '--help']
            help_result = subprocess.run(help_cmd, capture_output=True, text=True, timeout=5, check=True)
            if 'fork' not in help_result.stdout:
                raise ValueError(provider + ' does not advertise native fork support; update the CLI')
            return dict(version=version.stdout.strip(), authenticated=True, fork_advertised=True,
                        model_availability='provider validates the exact selected model at dispatch; no fallback')
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            raise ValueError(provider + ' preflight failed; check installed version and login in the coordinator environment') from None

    @staticmethod
    def effort_flags(provider, effort):
        if effort is None:
            return []
        if effort not in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra') or (provider == 'claude' and effort == 'ultra'):
            raise ValueError('Unsupported effort level for ' + provider)
        if provider == 'codex':
            return ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        return ['--effort', effort]

    @staticmethod
    def codex_restrictions(project):
        flags = []
        for feature in ('apps', 'plugins', 'browser_use', 'computer_use', 'shell_tool', 'unified_exec'):
            flags += ['--disable', feature]
        listed = subprocess.run(['codex', 'mcp', 'list', '--json'] + flags,
                                cwd=project, capture_output=True, text=True, timeout=10, check=True)
        servers = json.loads(listed.stdout)
        for server in servers:
            name = server['name']
            # The CLI splits override paths on dots; quoted segments are NOT TOML keys.
            # Refuse names that cannot be represented safely rather than enabling tools.
            if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                raise ValueError('Cannot safely disable an MCP server with this name using CLI overrides')
            flags += ['-c', 'mcp_servers.' + name + '.enabled=false']
        # Fail closed if the installed CLI cannot enforce the selected tool configuration.
        checked = subprocess.run(['codex', 'mcp', 'list', '--json'] + flags,
                                 cwd=project, capture_output=True, text=True, timeout=10, check=True)
        if any(s.get('enabled') for s in json.loads(checked.stdout)):
            raise ValueError('Could not disable Codex MCP servers for snapshot-only review')
        return flags

    def command(self, participant, structured=True):
        model = participant["model"]
        session = participant.get("child_id") or participant["session_id"]
        fresh = not participant.get('child_id') and participant.get('source_mode') == 'brief'
        if participant["provider"] == "codex":
            command = ['codex', 'exec', '-s', 'read-only', '-c', 'approval_policy="never"']
            if not fresh:
                command += ['resume' if participant.get('child_id') else 'fork', session]
            command += ['--model', model, '--json', '--skip-git-repo-check']
            if structured:
                command += ['--output-schema', str(Path(__file__).parent / 'assets/envelope.schema.json')]
            command += self.effort_flags('codex', participant.get('effort'))
            return command + ['-']
        cmd = ["claude", "--model", model, "-p", "--output-format", "json",
               "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--permission-mode", "plan", "--permission-prompts", "none"]
        if not fresh:
            cmd += ['--resume', session]
            if not participant.get('child_id'):
                cmd += ['--fork-session']
        if structured:
            cmd += ['--json-schema', (Path(__file__).parent / 'assets/envelope.schema.json').read_text()]
        cmd += self.effort_flags('claude', participant.get('effort'))
        return cmd

    @staticmethod
    def parse(provider, output, existing=None):
        if provider == "claude":
            obj = json.loads(output)
            if obj.get("is_error"):
                raise ValueError("Claude reported a failed turn")
            child = obj.get("session_id")
            envelope = obj.get("structured_output") or json.loads(obj["result"])
        else:
            child, result, completed = existing, None, False
            for line in output.splitlines():
                event = json.loads(line)
                if event.get("type") in ("error", "turn.failed"):
                    raise ValueError("Codex reported a failed turn")
                if event.get("type") == "thread.started":
                    child = event["thread_id"]
                if event.get("type") == "turn.completed":
                    completed = True
                item = event.get("item", {})
                if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                    result = item["text"]
            if result is None or not completed:
                raise ValueError("Codex returned no completed final response")
            envelope = json.loads(result)
        if not isinstance(child, str) or not child:
            raise ValueError("Provider did not return a child session ID")
        return child, validate(envelope)

    def turn(self, participant, context, cancel, timeout):
        env = dict(os.environ, BREAKOUT_CHILD="1", BREAKOUT_RUN_ID=context["run_id"])
        deadline = time.monotonic() + timeout
        command = self.command(participant)
        if participant['provider'] == 'codex':
            command[2:2] = self.codex_restrictions(participant['project'])
        if cancel.is_set() or time.monotonic() >= deadline:
            raise Interrupted('Cancelled before dispatch')
        proc = subprocess.Popen(command, cwd=participant["project"], env=env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True)
        payload = prompt(context)
        try:
            while True:
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise Interrupted("Turn cancelled or timed out; reconcile before resuming")
                try:
                    out, _ = proc.communicate(payload, timeout=.25)
                    break
                except subprocess.TimeoutExpired:
                    payload = None
            if proc.returncode:
                # Do not copy potentially sensitive provider stderr into the database/logs.
                raise RuntimeError(f"{participant['provider']} exited {proc.returncode}; inspect provider diagnostics locally")
            child, envelope = self.parse(participant["provider"], out, participant.get("child_id"))
            if child == participant["session_id"]:
                raise ValueError("Provider reused original session instead of forking")
            if participant.get("child_id") and child != participant["child_id"]:
                raise ValueError("Provider changed the persistent child session")
            return child, envelope
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()


def doctor():
    result = {}
    for provider, args in (("codex", ["exec", "fork", "--help"]), ("claude", ["--help"])):
        try:
            version = subprocess.run([provider, "--version"], capture_output=True, text=True, timeout=5)
            help_result = subprocess.run([provider] + args, capture_output=True, text=True, timeout=5)
            result[provider] = {"version": version.stdout.strip(), "installed": version.returncode == 0,
                                "fork_flag_present": "fork" in help_result.stdout,
                                "live_fork_verified": False, "delivery": "queued mailbox"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            result[provider] = {"installed": False, "error": str(exc)}
    return result
