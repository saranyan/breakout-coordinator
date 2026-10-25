# Guide

How to set up breakouts, shape teams, and work with a team's result. The commands
behind each step are in the [reference](REFERENCE.md).

## Set up

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
export PATH="$PWD/.venv/bin:$PATH"          # the skill calls `breakout`
breakout install --provider claude --config-dir "$HOME/.claude"
breakout install --provider codex  --config-dir "$HOME/.codex"
breakout daemon start
breakout doctor
breakout models
```

- **`install`** merges three hooks (`SessionStart`, `UserPromptSubmit`,
  `SessionEnd`) into the client's settings, installs the `/breakout` skill, and
  records what it owns. Your existing entries are kept. Review and trust the hooks
  through the client's normal controls, then restart the session.
- **The hooks** only register which conversation you're in and show a notice when
  a result is waiting. They never run the scheduler or start the daemon. Sessions
  started *by* a breakout set `BREAKOUT_CHILD=1`, so their hooks do nothing.
- **Runtime state** lives in `~/Library/Application Support/Breakout` (override
  with `BREAKOUT_HOME` or `--home`), outside your repositories. It's mode 0700.
- **`breakout models`** lists what your installed CLIs advertise. Models are always
  explicit, and there's no fallback model.

To update later, run `breakout upgrade` from a normal terminal. It refreshes an
idle daemon and the skills you haven't edited.

## Your first breakout

From your Claude or Codex conversation, describe the outcome and the team:

> Break out on this design with a pragmatic Architect on Opus and a skeptical
> reviewer on Sol, both medium. Bring the recommendation back here.

The skill turns that into a `breakout launch` from your conversation. Under the
hood:

- **Same vendor means a fork.** A Claude member started from a Claude
  conversation is a native fork, even on a different model, so it carries your
  full conversation, including what's been compacted. A member from another
  vendor starts a new persistent session.
- **The context packet** is how cross-vendor members catch up. Your agent writes
  it from the conversation: your goal in your words, decisions, constraints and
  non-goals, prior attempts and why they failed, relevant excerpts and paths, and
  what's still uncertain. It's hashed and stored with the run. A cross-vendor
  launch with an empty packet is refused rather than pretending.
- **Roles:** exactly one author writes the deliverable, at least one required
  reviewer must approve the exact final version, and advisory members comment
  without blocking. Teams have 2 to 4 members.
- **Limits:** by default four work rounds, a wall-clock budget and ten review
  replies. At a limit the team stops and says what's unfinished; it never extends
  itself.

The launch receipt lists the members, their models, how each got its context,
the limits and the guidance pinned for the run.

### Saved teams

Instead of describing a team each time, define it once in `.breakout/config.toml`
(start from [`examples/access-map.toml`](../examples/access-map.toml)):

- **connections:** how to reach a provider (`codex_cli`, `claude_cli`, or
  `openai_chat` for an API);
- **profiles:** a connection plus a model and effort;
- **personalities:** short instructions for a perspective;
- **teams:** named members, each with a profile, a personality and a role.

Then ask for the team by name ("break out with the rfc team"). Invalid settings
fail before anything is sent.

## Review the result

When a result is ready, the hook shows a notice at your next turn. Your agent
reads the result and the discussion, brings back the recommendation,
disagreements, evidence gaps and decisions for you, and then acknowledges it.
Acknowledging only records that the result was read.

The team then **waits without making model calls** until you act:

| You say | What happens |
|---|---|
| "Sol, why did you choose that?" | Only Sol answers. The artifact doesn't change. |
| "Team, what's the biggest remaining risk?" | One bounded reply from each member. |
| "Revise: make it simpler." | The author revises. Earlier approvals stop counting, and reviewers approve the new version. |
| "Accept it." | Records your approval of that exact version. The team stays open. |
| "Close the team." | Archives it. History is kept. |

A question never edits the artifact, a revision never publishes anything, and
receiving a result accepts nothing. If a limit is reached, you can extend it
explicitly. To pick up a closed run later, start a linked follow-up that carries
its result and discussion into a new team.

## Personalities and learnings

Your project's `.breakout/` folder holds editable guidance:

```text
.breakout/
  config.toml
  personalities/visionary_designer.md
  learnings/plain-language.md
  history/...            numbered versions, for undo
```

Each file has TOML metadata between `+++` lines, then Markdown. Runs pin the exact
content they used, so editing a file changes future breakouts, not an open one.
The folder is private and gitignored by default.

- **A preference you state** ("always write in plain language") is saved directly
  with `library save --direct`. No extra approval is needed.
- **A one-off correction** stays with that run. It doesn't become a preference.
- **Personalities from source material:** `library preview` reads a profile you
  supply (a "soul file") and flags anything that looks like it's trying to grant
  permissions. Your agent drafts the personality: priorities, questions, decision
  criteria, trade-offs and blind spots. Personalities shape how a member reasons.
  They never change permissions, models or roles.

### Improvements from feedback

Lessons the system infers are held to a higher bar than preferences you state:

1. A proposed change is saved as a **candidate**; the current version stays in use.
2. It's compared with the current version on the original complaint plus at least
   three unseen examples, judged blind. `library evaluate` checks that the evidence
   is complete. It doesn't run models itself.
3. You approve it by name. It then runs as a **five-run trial**.
4. After you judge each run that used it, record the outcome. A failure restores
   the earlier version and pauses the candidate.

Nothing is promoted because two agents agree, and unknown cost never becomes a
claim of lower cost.

## API members

Any OpenAI-compatible endpoint can be a member through an `openai_chat`
connection: `/models` for availability, `/chat/completions` for discussion. Keep
the key in an environment variable (`api_key_env`), never in the file.

- Context is sent only after you authorize that connection for the project
  (`--allow-connection NAME`). The authorization is tied to the connection's
  settings.
- HTTPS is required (except loopback for development), redirects are refused,
  and options the connection doesn't declare are rejected.
- Calls time out after at most 60 seconds, and an uncertain call is never retried
  blindly.

This path is covered with mocked transport only; see [verification](VERIFICATION.md).

## Documents

A breakout can work on an immutable snapshot of a document: a local UTF-8 file,
a public HTTPS URL, or a private page you exported yourself (for example a Notion
page with every comment thread). Members work on the snapshot, not with document
tools. Local sources are rechecked when you view the result. External snapshots
stay marked as unverified for freshness until someone checks the source again.
