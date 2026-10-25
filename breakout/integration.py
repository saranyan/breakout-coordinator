"""Reversible provider hook/skill installation, with ownership manifests."""
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from .daemon import request
from .protocol import digest


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".breakout-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install(provider, config_dir, home):
    root = Path(config_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "breakout-install.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['provider'] != provider:
            raise ValueError('Provider does not match installation')
        skill = root / 'skills/breakout/SKILL.md'
        source = Path(__file__).resolve().parent.parent / 'skills/breakout/SKILL.md'
        if not source.exists():
            source = Path(__file__).parent / 'assets/SKILL.md'
        content = source.read_text()
        if skill.exists() and digest(skill.read_text()) != manifest['skill_hash']:
            raise ValueError('Installed breakout skill was edited; refusing to overwrite user changes')
        if not skill.exists() or digest(content) != manifest['skill_hash']:
            atomic_write(skill, content)
            manifest['skill_hash'] = digest(content)
            atomic_write(manifest_path, json.dumps(manifest, indent=2) + '\n')
            return {'status': 'skill updated', 'note': 'Existing hooks and trust settings preserved; reload the skill in the session.'}
        return {"status": "already installed", "manifest": str(manifest_path)}
    config = root / ("hooks.json" if provider == "codex" else "settings.json")
    data = json.loads(config.read_text()) if config.exists() else {}
    skill = root / "skills/breakout/SKILL.md"
    if skill.exists():
        raise ValueError("An existing breakout skill is present; refusing to overwrite it")
    repo = Path(__file__).resolve().parent.parent
    source = repo / "skills/breakout/SKILL.md"
    if not source.exists():
        # Wheel installations carry the same skill as package data.
        source = Path(__file__).parent / "assets/SKILL.md"
    content = source.read_text()
    command = (f"cd {shlex.quote(str(repo))} && " + shlex.join(
        [sys.executable, "-m", "breakout", "--home", str(Path(home).expanduser().resolve()), "hook", "--provider", provider]))
    entry = {"hooks": [{"type": "command", "command": command, "timeout": 3}]}
    hooks = data.setdefault("hooks", {})
    for event in ("SessionStart", "UserPromptSubmit", "SessionEnd"):
        hooks.setdefault(event, []).append(entry)
    manifest = dict(provider=provider, config=str(config), skill=str(skill), skill_hash=digest(content),
                    entry=entry, events=["SessionStart", "UserPromptSubmit", "SessionEnd"], config_existed=config.exists())
    # Record ownership before mutation, so an interrupted install remains uninstallable.
    atomic_write(manifest_path, json.dumps(manifest, indent=2) + "\n")
    atomic_write(skill, content)
    atomic_write(config, json.dumps(data, indent=2) + "\n")
    return {"status": "installed", "config": str(config), "skill": str(skill),
            "note": "Review and trust the new hooks in your provider. No trust controls were bypassed. Restart the session."}


def uninstall(provider, config_dir, home=None):
    root = Path(config_dir).expanduser().resolve()
    manifest_path = root / "breakout-install.json"
    if not manifest_path.exists():
        return {"status": "not installed"}
    manifest = json.loads(manifest_path.read_text())
    if manifest["provider"] != provider:
        raise ValueError("Provider does not match installation")
    config = root / ("hooks.json" if provider == "codex" else "settings.json")
    skill = root / "skills/breakout/SKILL.md"
    if config.exists():
        data = json.loads(config.read_text())
        for event in manifest["events"]:
            entries = data.get("hooks", {}).get(event, [])
            # Remove only the exact installed entry; preserve entries edited by the user.
            if manifest["entry"] in entries:
                entries.remove(manifest["entry"])
            if not entries:
                data.get("hooks", {}).pop(event, None)
        if not data.get("hooks"):
            data.pop("hooks", None)
        if not data and not manifest["config_existed"]:
            config.unlink()
        else:
            atomic_write(config, json.dumps(data, indent=2) + "\n")
    preserved = False
    if skill.exists():
        if digest(skill.read_text()) == manifest["skill_hash"]:
            skill.unlink()
            try:
                skill.parent.rmdir()
            except OSError:
                pass
        else:
            preserved = True
    manifest_path.unlink()
    return {"status": "uninstalled", "modified_skill_preserved": preserved,
            "note": "Runtime state, source sessions, and unrelated hooks are preserved."}


def hook(provider, home):
    # Headless children inherit this flag. Never launch the daemon or a new breakout in a hook.
    if os.environ.get("BREAKOUT_CHILD") == "1":
        return
    try:
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return
        event = json.loads(raw)
        session_id = event.get("session_id")
        if not session_id:
            return
        project = event.get("cwd") or os.getcwd()
        name = event.get("hook_event_name", "SessionStart")
        # Do not save prompt/transcript text; only explicit metadata and checkpoint IDs.
        request("register", home, provider=provider, session_id=session_id, project=project,
                title=event.get("session_title") or Path(project).name + " · " + session_id[:8],
                activity="closed" if name == "SessionEnd" else name, child=False,
                boundary=event.get("turn_id") or "hook checkpoint", timeout=.6)
        if name not in ("SessionStart", "UserPromptSubmit"):
            return
        pending = request("mailbox", home, provider=provider, session_id=session_id, timeout=.6)
        text = ('Breakout origin metadata (not a task): ' + json.dumps(
            {'provider': provider, 'session_id': session_id, 'project': project}) +
            '. For a user-requested breakout, use this session as the origin of breakout launch; '
            'do not require a second interactive session. Assemble the relevant context handoff yourself '
            'for cross-provider workers, and preserve the user-selected models and personalities. ')
        text += ('V2 review: use breakout open --provider ' + provider + ' --session ' + shlex.quote(session_id) +
                 ' to find open teams. Address a member with breakout ask RUN_ID TEXT --to NAME --provider ' + provider +
                 ' --session ' + shlex.quote(session_id) + '. Use revise for artifact changes; close only when the user ends the team. '
                 'Receipt is not approval or closure. Project guidance lives in .breakout/learnings and .breakout/personalities. ')
        if pending:
            refs = [{"run_id": r["run_id"], "revision": r["revision"]} for r in pending]
            text += ("Breakout results are ready for review: " + json.dumps(refs) +
                    ". Retrieve with breakout result RUN_ID and breakout transcript RUN_ID. "
                    "After reading a result, acknowledge using breakout ack --provider " + provider +
                    " --session " + shlex.quote(session_id) + " RUN_ID REVISION. Queued does not mean received. "
                    "Read the result and bring its recommendation, disagreements, and user decisions back into this conversation.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}))
    except (OSError, ValueError, KeyError):
        # A stopped daemon must never block or break an original conversation.
        return
