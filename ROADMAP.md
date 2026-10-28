# Roadmap

breakout-coordinator works today as a discussion tool: a small team argues a
question out and hands back a reviewed answer. This page covers what's needed to
make that dependable, and where it goes next: rooms that build.

## Make today's breakouts dependable

- [ ] **Launch from Claude, live.** Run the full loop (launch, question, revision,
      close) from a Claude Code conversation, as was done from Codex. Launch,
      question and close have run live; a revision hasn't yet.
- [ ] **Bigger teams, live.** Run a three-member team with an advisory member.
- [ ] **API members, live.** Run an OpenAI-compatible member against a real
      endpoint, such as the Hugging Face router.
- [ ] **Restart recovery, live.** Kill the daemon mid-turn with real providers and
      show nothing is duplicated or lost.
- [ ] **Reconcile from provider evidence.** Today an uncertain turn needs a manual
      `reconcile`. Read the provider's own session state and do it with proof.
- [ ] **Wake an idle conversation.** Results currently appear at your next turn.
- [ ] **Cost.** CLI token usage is unknown. Report it where providers expose it.
- [ ] **Linux.** Developed on macOS; Linux is untested.

## Breakout Rooms

The [design proposal](docs/design/BREAKOUT-ROOMS.md) turns a breakout into a room
that can build and test code in a private clone and return a branch you can
import. It's staged so that each step has a pass or fail gate:

- [ ] **Stage 0: enforcement.** Prove, for each CLI version, that a member can
      write only inside the room's clone, with no network, and that checks can't
      change what they're checking. If it fails, code rooms stay off.
- [ ] **Stage 1: room recipes.** Room types (research, design, prototype), a
      receipt that shows the team, the checks and the limits, and readable old
      records.
- [ ] **Stage 2: workspace.** A clone with no remotes, checkpoints that survive
      interruption, and "apply" that only ever creates a new branch.
- [ ] **Stage 3: evidence.** Checks run by the coordinator on a read-only export.
      A member saying "tests pass" never counts.
- [ ] **Stage 4: a live pilot.** A mixed team builds a small CSV validator, with
      a reviewer's regression test shown failing first. It's compared with one
      agent working alone.
- [ ] **Stage 5: durability.** Kill the daemon during edits and checks; a
      three-member room.
- [ ] **Stage 6: usefulness.** Rooms against a single agent on matched tasks. Keep
      going only if the extra coordination pays off.

## Ideas

- **Competing implementations:** two members build the same thing in separate
  clones, and a third judges.
- **Research rooms with real sources:** explicit, permissioned access to the web
  or documents, not just what you hand over.
- **Shared personalities:** export a personality or a set of learnings to another
  project, or share them with a team.
- **Measured learning:** show, with blind comparisons, whether saved learnings
  actually improve results.

Ideas and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
