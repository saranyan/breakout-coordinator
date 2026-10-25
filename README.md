# breakout-coordinator

[![tests](https://github.com/saranyan/breakout-coordinator/actions/workflows/tests.yml/badge.svg)](https://github.com/saranyan/breakout-coordinator/actions/workflows/tests.yml)

**Pull a small team of AI agents out of the conversation you're in (Claude, Codex,
or any OpenAI-compatible model) and get a reviewed answer back.**

## Why

You're deep in a conversation with one agent and you want a second opinion: from
another vendor's model, or from a skeptic and a pragmatist arguing it out. Today
that means opening another session and **carrying the context yourself**: pasting
the plan, the decisions and the constraints, then copying the answer back.

A breakout does that hop for you. You say what you want, and your agent puts the
team together and hands it the context. One member writes, the others must approve
the exact final version, and the result comes back to the conversation you started
from. Teammates from the same vendor fork your conversation, so they already know
everything. Teammates from another vendor get a context packet your agent writes
for them. You never write it yourself.

It's tested with Claude Code and Codex, and any OpenAI-compatible API can join.

## A breakout, start to finish

1. **Ask**, in your Claude or Codex conversation:
   > Break out on this recovery design. A pragmatic architect on Opus and a
   > skeptical reviewer on Sol. Keep my local-only decision and bring back a
   > recommendation.
2. **Launch.** The `/breakout` skill starts the team in the background and shows
   you who's on it, what they were told and the limits. You keep working.
3. **Work.** The architect drafts and the reviewer pushes back, turn by turn, until
   both approve the same version or the budget runs out. Disagreements stay
   visible; nothing is papered over.
4. **Return.** The result waits in your conversation's mailbox and shows up at
   your next turn: the recommendation, the evidence gaps and the decisions left
   for you.
5. **Review.** Ask a member why ("Sol, why reject that?"), ask for a revision,
   accept it, and close the team when you're done. Until then, it waits without
   spending anything.

Here's the same flow at the command line, the way the skill runs it, with pretend
agents. The commands and their output are real.

![A breakout launched from a saved team, its result in the mailbox, a question to one member, then accept and close](docs/images/breakout-demo.gif)

## Try it in two minutes

The demo uses pretend agents, so it needs no accounts and makes no model calls.
You need macOS (or another Unix) and Python 3.11 or newer.

```sh
git clone https://github.com/saranyan/breakout-coordinator.git && cd breakout-coordinator
python3 -m breakout demo --document examples/review.txt
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Use it from your conversation

With the Claude Code and/or Codex CLIs installed and logged in:

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
export PATH="$PWD/.venv/bin:$PATH"
breakout install --provider claude --config-dir "$HOME/.claude"   # and/or codex
breakout daemon start
breakout doctor
```

`install` adds the `/breakout` skill and three small hooks, and keeps your existing
settings. Review and trust the hooks the way your client asks, restart the
session, and then just ask for a breakout. `breakout uninstall` removes exactly
what was added. The [guide](docs/GUIDE.md) covers teams, personalities and the
review loop.

## What it can and can't do

- **Discussion only.** Members read the task, the context and any document
  snapshot, and write. They can't run commands, edit your files or post anywhere.
  Codex members run in a read-only sandbox, and Claude members run with no tools.
- **Results arrive at your next turn**, not the instant they're ready. Waking an
  idle conversation isn't verified, so the mailbox is the dependable path.
- **Agreement isn't proof.** Two agents approving the same text is a coordination
  signal, not evidence that the text is right.
- **Your context, your call.** Your agent decides what goes into the packet for
  other vendors. API members only receive context after you allow that connection.
- **One person, one machine.** A local daemon, private to your user.

## What you can use it for

**Today:**

- **Design review before you build.** One member drafts, another attacks the
  failure modes, and you get back a revised design plus the questions only you
  can answer.
- **RFC and document review.** Snapshot a local file, a public URL or an exported
  Notion page with its comments, and the team works on that exact version.
- **A second vendor's opinion** without leaving your conversation or retelling it.
- **Personalities that learn.** Personalities ("skeptical reviewer", "visionary
  designer") and your preferences are Markdown files in your project. You can
  edit them, they're versioned, and you can undo changes. Improvements proposed
  from feedback only stick after a trial.

**Next: Breakout Rooms.** The [design proposal](docs/design/BREAKOUT-ROOMS.md) turns
a breakout into a room that can *build*: a private clone, one member implementing
and another writing tests, checks run by the coordinator itself, and a branch you
can import when you're happy. It's proposed, not built. See the
[roadmap](ROADMAP.md).

## Status

| Part | State |
|---|---|
| Launch from Codex, Claude + Codex team, question, revision, close | **Verified live**, 2026-10-16 (Claude Opus 5.5 and Codex Sol, medium effort) |
| Two-agent discussion through the daemon, persistent members | **Verified live**, 2026-10-15 |
| Launch from Claude | Covered by automated tests, not yet run live |
| Teams of 3–4, advisory members | Covered by automated tests |
| OpenAI-compatible API members | Covered with mocked transport only |
| Personalities and learnings with trials and undo | Covered by automated tests; no improvement measured yet |
| Restart recovery, uncertain turns | Covered by automated tests |
| Waking an idle conversation | Not verified; results show at your next turn |
| Rooms that build and test code | Proposed ([design](docs/design/BREAKOUT-ROOMS.md)) |

Details are in [verification](docs/VERIFICATION.md).

## Documentation

| Document | What it covers |
|---|---|
| [Guide](docs/GUIDE.md) | Setup, teams, personalities, the review loop, feedback |
| [Reference](docs/REFERENCE.md) | Every command, configuration, documents, hooks, recovery, limits |
| [Verification](docs/VERIFICATION.md) | What has run live, and what only automated tests cover |
| [Breakout Rooms](docs/design/BREAKOUT-ROOMS.md) | The design proposal for rooms that build code |
| [Roadmap](ROADMAP.md) | What's next |
| [Changelog](CHANGELOG.md) | Release notes |

A sibling project, [agent-relay](https://github.com/saranyan/agent-relay), keeps
two working sessions (Codex and Claude Code) passing turns to each other while you
stay in charge.

## Contributing

Ideas, experiments and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md), and report security issues privately through
[SECURITY.md](SECURITY.md). This project follows the
[Contributor Covenant](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE)
