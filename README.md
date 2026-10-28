# breakout-coordinator

[![tests](https://github.com/saranyan/breakout-coordinator/actions/workflows/tests.yml/badge.svg)](https://github.com/saranyan/breakout-coordinator/actions/workflows/tests.yml)

You're coding or designing with Claude or Codex, and you want a few agents to go
discuss something and come back with answers. This does that.

![Two agents review this README from a Claude Code conversation and report back](docs/images/breakout-demo.gif)

*Claude Code on the left asks two agents to review this README. The right side
shows them talking. Waits are sped up.*

Ask from your conversation ("break out on this design with an architect and a
skeptic"). The agents go off, argue it out, and the answer comes back to the same
conversation. You can then ask them follow-up questions or ask for changes. Mix
Claude, Codex, or OpenAI-compatible models. Your agent passes them the context, so
you don't have to.

## Try it

```sh
git clone https://github.com/saranyan/breakout-coordinator.git && cd breakout-coordinator
python3 -m breakout demo --document examples/review.txt   # pretend agents, no accounts
```

Python 3.11+, macOS. No dependencies.

## Use it

```sh
python3 -m venv .venv && .venv/bin/pip install -e . && export PATH="$PWD/.venv/bin:$PATH"
breakout install --provider claude --config-dir ~/.claude
breakout install --provider codex --config-dir ~/.codex
breakout daemon start
```

You need Claude Code or Codex installed and logged in. Restart your session and
ask for a breakout. See the [guide](docs/GUIDE.md).

## Good to know

- Agents only discuss. They can't run commands or edit your files.
- Answers show up at your next message, not instantly.
- Tested live with Claude and Codex. Everything else is covered by tests only; see
  [verification](docs/VERIFICATION.md).
- Next up: agents that build and test code, not just talk.
  See the [roadmap](ROADMAP.md).

[Reference](docs/REFERENCE.md) · [Contributing](CONTRIBUTING.md) ·
[Security](SECURITY.md) · [MIT license](LICENSE)

Sibling project: [agent-relay](https://github.com/saranyan/agent-relay), which
lets two coding sessions pass work back and forth.
