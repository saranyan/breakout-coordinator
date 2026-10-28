# Verification

What has actually run against real Claude Code and Codex, and what only the
automated tests cover. The tests use deterministic fake agents, so they prove the
coordinator's logic, not how a real provider behaves. Agreement between agents is
never counted as evidence.

## Live runs

**2026-10-15: native forks.** Disposable probes on Codex CLI 0.162.1
(`gpt-6.1-sol`) and Claude Code 2.1.299 (`claude-opus-5-5`), both at medium
effort. Each created a parent session, forked it, and showed that the fork
inherited a private marker, kept going across several turns, and left the
parent's history untouched. All checks passed for both providers.

**2026-10-15: a bounded discussion through the daemon.** `breakout trial` on
`examples/review.txt`: four alternating Codex and Claude turns in 114 seconds
over the real Unix socket, with every attempt applied and one persistent child
session per member. The result was correctly *partial*: it hit the four-turn
budget before the author had confirmed the final revision, and it said so.

**2026-10-16: the full loop from a Codex conversation.** A team launched from an
existing Codex conversation: an Architect on `claude-opus-5-5`, briefed with a
context packet, and Sol on `gpt-6.1-sol` as a native fork, both at medium effort.

- Both members approved the same version of the initial artifact.
- After the origin read the result, a question addressed only to Sol resumed Sol's
  original session and was answered without changing the artifact. Retrying the
  same question ID produced exactly one reply.
- A requested revision changed the artifact, cleared the earlier approvals, and
  both members approved the new version in their original sessions.
- Every result went only to the origin and was acknowledged after reading. The
  team was then closed explicitly, after nine work turns and two review replies.

**2026-10-28: the full loop from a Claude conversation.** The README demo GIF.
Claude Code 2.1.312 launched the `rfc` recipe from `.breakout/config.toml`: the
Architect on `claude-opus-5-5` as a native fork, and Sol on `gpt-6.1-sol`
briefed with a context packet, both at medium effort.

- With no explicit issues, both members scanned the README and the origin selected
  all six of their issues.
- The discussion finished within its two-round budget. The result reached the
  Claude conversation through the hook at its next message and was acknowledged.
- A question addressed only to Sol was answered without changing the artifact,
  and the team was closed explicitly.

## Automated only

85 tests pass, in about a second. Beyond the live paths above, they cover:

- all four origin and team-vendor combinations;
- teams of three and four, advisory members, and an author other than the first
  member;
- the review window: targeted and team questions, revisions, accept versus close,
  extensions, timeouts and idle time excluded from budgets;
- restarts while parked, uncertain turns that need reconciliation, cancellation
  that really terminates the child process, and idempotent messages;
- personalities and learnings: Markdown parsing, pinning, candidates, evaluation
  records, trials, rollback and undo;
- strict configuration validation, first-send consent for API connections, and
  credential-safe errors;
- hook and skill install and uninstall that preserve existing settings;
- the full CLI workflow through the daemon's request handling.

The OpenAI-compatible adapter is tested against mocked transport only. That isn't
evidence about any real API.

## Not verified

- API and Hugging Face members against real endpoints.
- Teams larger than two, live.
- Restart recovery against real providers.
- Waking an idle conversation when a result is ready. Results appear at your next
  turn, or when you ask.
- Whether learnings actually improve results. Nothing has been measured.
