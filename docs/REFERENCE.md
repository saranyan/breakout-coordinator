# Reference

The commands, settings and guarantees behind the [README](../README.md) and the
[guide](GUIDE.md). `breakout <command> --help` shows every option. Commands print
JSON by default; `result` and `transcript` can write Markdown with `--output`.

## Commands

**Setup and checks**

| Command | What it does |
|---|---|
| `install` / `uninstall --provider P --config-dir DIR` | Add or remove the `/breakout` skill and hooks for one client |
| `upgrade` | Refresh an idle daemon and unmodified owned skills |
| `daemon start\|stop\|status` | Run the background coordinator |
| `doctor` | Check installed CLIs, logins and fork support |
| `models` | List models the installed CLIs advertise |
| `probe --provider P --model M [--effort E]` | Create disposable sessions and test that a fork inherits context |
| `trial --document FILE` | A bounded live review on passed probe sessions, with its own daemon |
| `demo --document FILE` | A simulated review: no sockets, no model calls |

**Breakouts from your conversation**

| Command | What it does |
|---|---|
| `launch --from-provider P --from-session ID --task T ...` | Start a 2–4 member team. Members come from `--agent JSON` (repeated) or `--config FILE --team NAME`. Context from `--context` or `--context-file`. |
| `open --provider P --session ID` | List your open teams |
| `ask RUN 'TEXT' --to NAME\|team` | A question to one member or the whole team |
| `revise RUN 'TEXT'` | Ask for a revision; reviewers approve the new version |
| `feedback RUN 'TEXT'` | Record feedback without revising |
| `accept RUN` / `close RUN` | Approve the current version / archive the team |
| `extend RUN --rounds N --replies N --minutes N` | Raise a limit explicitly |
| `mailbox --provider P --session ID` | Results waiting for a conversation |
| `result RUN [--revision N]` / `transcript RUN` | Read the result / the full discussion |
| `ack --provider P --session ID RUN REVISION` | Record that a result was read |

`ask`, `revise`, `feedback`, `accept`, `close` and `extend` also take `--provider`
and `--session` (the origin), `--event-id` (so a retry is applied once) and
`--revision SHA` (rejects feedback on a stale version).

Useful `launch` options: `--issue` (repeat for explicit scope), `--constraint`,
`--omission` (record what was deliberately left out), `--document` /
`--snapshot` / `--source-revision` / `--comments-complete`, `--max-rounds`,
`--minutes`, `--review-replies`, `--close-after-hours`, `--context-bytes`,
`--allow-connection NAME`, `--followup OLD_RUN` and `--simulate`.

**Running teams**

| Command | What it does |
|---|---|
| `list` / `status RUN` | All runs / one run, with attempt IDs |
| `pause` / `resume` / `end` / `cancel RUN` | Stop scheduling, continue, or stop and keep partial work |
| `steer RUN 'TEXT'` / `decide RUN 'TEXT'` | Send an update / record your decision (both take `--event-id`) |
| `select RUN I1 I2` | Choose issues after a broad scan |
| `reconcile ATTEMPT --receipt FILE \| --verified-not-started` | Resolve an uncertain turn from provider evidence |

**Guidance library**

`library list|show|preview|save|evaluate|approve|outcome|undo|disable|delete [NAME]`
with `--kind learnings|personalities`, `--file`, `--direct`, `--version`,
`--run-id`, `--accepted`, `--constraint-lost` and `--metric-worse`. See the
[guide](GUIDE.md#personalities-and-learnings).

**Two existing sessions**

An older workflow runs a discussion between two conversations you register
yourself, and returns the result to both. `register --provider P --session ID
--title T` records a conversation, `sessions` lists them, and `start --codex-session
A --claude-session B --task T --document FILE` starts the discussion. Without
`--issue`, both agents scan first and the run waits for `select`.

## Configuration

| Setting | Purpose |
|---|---|
| `BREAKOUT_HOME` / `--home` | Runtime state (default `~/Library/Application Support/Breakout`). Keep it short: Unix socket paths have a length limit. |
| `.breakout/config.toml` | Saved connections, profiles, personalities and teams. See [`examples/access-map.toml`](../examples/access-map.toml). |
| `.breakout/personalities/`, `.breakout/learnings/` | Editable guidance, versioned under `.breakout/history/` |
| `api_key_env` in a connection | The environment variable holding an API key. Keys are never stored. |

Defaults: four rounds, 20 minutes, ten review replies, and a 1 MiB context limit.
Time spent waiting for you doesn't count against a team's working time. Oversized
context fails rather than silently dropping decisions. Effort is passed on every
creation, fork and resumed turn; leaving it out keeps the provider's default.

## Hooks and delivery

Results are stored and queued only for the conversation that launched the team
(or for both sessions with `start`). A notice appears at that conversation's next
supported hook point. **Immediate wake-up of an idle conversation isn't
promised.** The CLI is always the fallback: `mailbox`, `result`, then `ack`.
Acknowledge only after reading. Results are never injected by editing a client's
transcript files. A macOS notification is attempted once; it's separate from
delivery, and a blocked notification never loses a result.

`uninstall` removes only the exact hooks and unmodified skill content it
installed. Edited entries stay yours.

## Recovery

One daemon holds a lock per state directory. It commits an attempt ID before each
dispatch and a validated receipt before applying it. After a restart, saved
receipts are applied exactly once. A dispatch that was interrupted becomes
*uncertain* and pauses the run. Nothing is retried blindly, and the daemon never
adopts or kills leftover provider processes.

To resolve an uncertain turn, check the provider's own state and make sure no old
turn is still running, then either import a verified receipt
(`{"child_id": "...", "envelope": {...}}`, see `breakout/protocol.py`) or confirm
it never started:

```sh
breakout reconcile ATTEMPT_ID --receipt verified-receipt.json
breakout reconcile ATTEMPT_ID --verified-not-started   # only with positive evidence
breakout resume RUN_ID
```

A timeout or an ambiguous error is not evidence that a turn didn't start.

## Guarantees and limits

- **Workers are discussion-only.** Codex runs in a read-only sandbox with apps,
  plugins, browser, shell and configured MCP servers disabled (and rechecked).
  Claude runs with no built-in tools, no MCP servers and plan permissions.
- **A result is complete** only when every required reviewer approved the exact
  final version and every issue is agreed, needs evidence, or needs you.
  Mismatched resolutions stay unresolved. A budget limit yields a partial result.
- **Steering** is deduplicated by event ID, and invalidates earlier conclusions.
- **Fork point:** the source is forked at the provider's dispatch-time boundary;
  exact turn-level snapshots aren't available through the CLIs.
- **Not authentication across users:** the runtime directory is mode 0700, and the
  socket and database 0600. Same-user processes can read them.
- **Cost:** CLI token cost is unknown; turns and time are the limits. API usage is
  recorded when the endpoint reports it.
- **No publishing:** there is no endpoint that posts comments or writes the
  source document.

## Repository layout

```text
breakout/engine.py         scheduling, issue ledger, revision agreement, recovery, steering
breakout/store.py          SQLite records and the session registry
breakout/review.py         the review window: questions, revisions, accept, close
breakout/providers.py      Claude and Codex CLI adapters; http_adapter.py for APIs
breakout/library.py        personalities and learnings
breakout/daemon.py, cli.py the owner-only socket service and the command line
breakout/integration.py    hooks and skill install/uninstall
skills/breakout/SKILL.md   the /breakout skill (same as breakout/assets/SKILL.md)
tests/                     unittest suite with deterministic agents
examples/                  a sample document, an access map, an evaluation format
```
