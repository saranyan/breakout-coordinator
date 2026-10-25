# Contributing to breakout-coordinator

Thanks for helping. This project starts AI sessions on someone's machine and
carries their context to other vendors, so it values correctness and honesty over
features. Please read this page before opening a pull request.

## Development setup

```sh
git clone https://github.com/saranyan/breakout-coordinator.git && cd breakout-coordinator
python3 --version                                    # 3.11 or newer
python3 -m unittest discover -s tests -p 'test_*.py' # full suite, about a second
python3 -m breakout demo --document examples/review.txt
```

There are no dependencies to install. The coordinator uses only the Python
standard library, and that's deliberate. Please don't add runtime dependencies. If
a change seems to need one, open an issue first.

Point `BREAKOUT_HOME` (or `--home`) at a temporary folder while you work, so you
never touch your real runtime state. Keep the path short: Unix socket paths have a
length limit.

## What a good pull request looks like

- **Tests come with the change.** Every behaviour change needs a test that fails
  without it. Tests use deterministic fake agents and temporary folders. Never
  point a test at your real `~/.claude`, `~/.codex` or runtime directory.
- **The protocol stays in sync.** If you change the envelope or receipts, update
  `breakout/protocol.py` and `breakout/assets/envelope.schema.json` together.
- **The skill stays in sync.** `skills/breakout/SKILL.md` and
  `breakout/assets/SKILL.md` are the same file; change both.
- **Docs match behaviour.** User-visible changes update the README or the
  relevant page in `docs/`.

## Changes to provider adapters

`breakout/providers.py` and `breakout/http_adapter.py` start real Claude Code and
Codex sessions or send context to an API. For changes to them:

1. **Say how it was verified.** Name the CLI version or API, and say what you
   ran. Fake-agent tests are required, but they don't prove real-provider
   behaviour.
2. **Never widen what workers can do.** Workers are discussion-only: Codex runs in
   a read-only sandbox with tools disabled, and Claude runs with no tools.
3. **Never send context to a new endpoint without consent.** API connections
   need explicit first-send authorization.
4. **Never retry blindly.** A timeout is not evidence that a turn didn't start.
5. **Probes and trials run only on disposable sessions**, never on your working
   conversations.

## Reporting bugs

Open an issue with `breakout --version`, your OS, your Python version, and the
Claude Code or Codex version if one is involved. Include the steps to reproduce
it; `breakout status RUN_ID` output helps. **Remove secrets, tokens and private
conversation text before you paste anything.**

Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Code of conduct

Everyone taking part in this project is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
