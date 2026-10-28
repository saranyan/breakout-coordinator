import time
from .documents import freshness


def result_markdown(run):
    result = run.get("result")
    if not result:
        raise ValueError("No result is ready yet")
    parts = [f"# Breakout {run['id']}", f"Status: {run['status']}. {result['reason']}.",
             f"Simulated: {run['simulate']}. Partial: {result['partial']}.",
             f"Document freshness: {freshness(run['document'])}",
             "## Proposed revision", result["proposal"] or "No proposal was completed.", "## Issue ledger"]
    for issue in result["issues"]:
        parts.append(f"- **{issue['id']}: {issue['title']}** — {issue['status']}")
        for participant, vote in issue["votes"].items():
            p = run['participants'][int(participant)]
            author = p.get('name', p['provider'])
            parts.append(f"  - {author}: {vote['resolution']}" + (f" Objection: {vote['objection']}" if vote["objection"] else ""))
            if vote["evidence"]:
                parts.append("    Evidence: " + "; ".join(vote["evidence"]))
    parts += ["## User decisions"] + ["- " + d["text"] for d in result["decisions"]]
    parts += ["## Questions"] + ["- " + q for q in result["questions"]]
    if run.get('version', 1) >= 2:
        parts += ['## Alternatives considered'] + (['- ' + x for x in result.get('alternatives', [])] or ['No alternative was explicitly recorded.'])
        parts += ['## Open disagreement'] + (['- ' + i['title'] + ': ' + v['objection'] for i in result['issues'] for v in i['votes'].values() if v.get('objection')] or ['No remaining objection was recorded; this is not evidence of quality.'])
        parts += ['## Evidence gaps'] + (['- ' + i['title'] for i in result['issues'] if i['status'] == 'needs evidence'] or ['No explicit evidence gap was recorded.'])
        parts += ['## Review conversation']
        for reply in result.get('review_replies', []):
            parts += [f"### {reply.get('name', reply['author'])}", reply['content']['message']]
        parts += ['## Constraints kept', 'Explicit constraints supplied:']
        parts += ['- ' + c for c in run.get('context_packet', {}).get('constraints', [])]
        parts += ['Preservation is for human review; agent agreement alone is not evidence of quality.']
        parts += ['- ' + x for x in result.get('constraints_kept', [])]
        parts += ['## Team and limits', '```json\n' + __import__('json').dumps(run.get('receipt', {}), indent=2) + '\n```']
        if result.get('unavailable'):
            parts += ['Unavailable members: ' + ', '.join(result['unavailable'])]
        parts += ['## Next step', 'This team is closed.' if run['status'] == 'closed' else
                  'The team is parked. Ask a member, request a revision, accept the artifact, or close the breakout. Acknowledging receipt does not close it.']
    parts += ["## Delivery", "Results are queued until the original session explicitly acknowledges receipt."]
    return "\n\n".join(parts) + "\n"


def transcript_markdown(run):
    lines = [f"# Discussion: {run['id']}"]
    for message in run["messages"]:
        superseded = message.get('stale') or (message['author'] != 'user' and message['epoch'] != run['epoch'])
        lines += [f"## {message.get('name', message['author'])} ({message['id']})" + (" — superseded" if superseded else ""),
                  message["content"]["message"]]
        for issue in message["content"].get("issues", []):
            lines.append(f"- {issue['id']}: {issue['status']} — {issue['resolution']} {issue['objection']}")
        if message["content"].get("proposal"):
            lines += ["### Proposed text", message["content"]["proposal"]]
    return "\n\n".join(lines) + "\n"


def _clip(text, width=96):
    text = " ".join(text.split())
    end = text.find(". ") + 1
    text = text[:end] if 0 < end <= width else text
    return text if len(text) <= width else text[:width - 1] + "…"


def watch_lines(run, seen):
    """Lines for events in `run` not yet in `seen`; mutates `seen`."""
    stamp = lambda t: time.strftime("%H:%M:%S", time.localtime(t))
    name = lambda i: run["participants"][i].get("name", run["participants"][i]["provider"])
    lines = []
    if "header" not in seen:
        seen.add("header")
        lines += [f"Breakout {run['id']}", "Task: " + _clip(run["task"])]
        lines += [f"  {p.get('name', p['provider'])} · {p['provider']} {p.get('model') or ''} · {p.get('role', 'member')}"
                  for p in run["participants"]]
        lines.append("")
    state = ("status", run["status"], run["phase"])
    if state not in seen:
        seen.add(state)
        lines.append(f"{stamp(time.time())}  ● {run['status']}")
    for attempt in run.get("attempts", []):
        if ("attempt", attempt["id"]) not in seen and attempt["status"] == "running":
            seen.add(("attempt", attempt["id"]))
            lines.append(f"{stamp(attempt['created'])}  … {name(attempt['participant'])} is thinking")
    for message in run["messages"]:
        if ("message", message["id"]) in seen:
            continue
        seen.add(("message", message["id"]))
        author = "You" if message["author"] == "user" else message.get("name", message["author"])
        lines.append(f"{stamp(message['created'])}  {author}: {_clip(message['content']['message'])}")
        lines += [f"            {i['id']} {i['status']}" for i in message["content"].get("issues", [])]
    for delivery in run.get("deliveries", []):
        key = ("delivery", delivery["revision"], delivery["status"])
        if key not in seen:
            seen.add(key)
            verb = "queued for" if delivery["status"] == "queued" else "read by"
            lines.append(f"{stamp(time.time())}  ↩ result r{delivery['revision']} {verb} the {delivery['provider']} session")
    return lines
