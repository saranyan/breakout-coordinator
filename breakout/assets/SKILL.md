---
name: breakout
description: Launch a team from the current conversation, carry context across providers, review its artifact, talk to members, and close it when the user is done. Use editable project personalities and learnings.
---

Use `breakout --help` for CLI syntax. The default workflow starts from this interactive session; do not ask the user to open or select a second session. Never launch from a breakout child (`BREAKOUT_CHILD=1` or known child context).

Use the provider, session ID, and project supplied by the breakout hook's origin metadata. Do not infer the current session by picking the newest registry entry. If origin metadata is missing, register the known current session ID or ask only for the missing identity. Start the daemon if needed with `breakout daemon start`; report actual permission failures rather than bypassing them.

Turn the user's requested personalities into 2–4 named worker specifications: exactly one author, at least one required reviewer, and optional advisory members. Models and personalities are independent. Preserve explicitly selected models and effort. Use `breakout models` if availability is unknown; never substitute silently. Personalities guide reasoning, not permissions. Prefer a requested recipe from `.breakout/config.toml` using `--team NAME --config PATH` instead of explicit agents. API workers use a supported `openai_chat` connection, an explicit brief and first-send authorization with `--allow-connection NAME`. Credentials remain environment references. Launch starts a compatible daemon on demand where the host permits it; a busy older daemon is not interrupted.

## Review before closing (v2)

Readiness is not closure. A v2 team parks without model calls until the user asks, revises or closes. Receipt, acceptance, publication and closure are separate.

- Find open teams: `breakout open --provider PROVIDER --session ORIGIN_ID`. Clarify the target only when ambiguous.
- A named-member question needs no join ceremony: `breakout ask RUN_ID 'QUESTION' --to 'MEMBER NAME' --provider PROVIDER --session ORIGIN_ID --event-id STABLE_ID`. `--to team` requests one bounded response round. Attribute replies; never impersonate a worker without calling it.
- Revise: `breakout revise RUN_ID 'FEEDBACK' --provider PROVIDER --session ORIGIN_ID --revision ARTIFACT_SHA --event-id STABLE_ID`. Worker history stays; author and required reviewers review the new version. Use `feedback` instead to record without revising. Add `--source` and `--source-revision` for explicitly fetched comments; preserve original wording and identity.
- Accept: `breakout accept RUN_ID --provider PROVIDER --session ORIGIN_ID`. The team remains open.
- Close: `breakout close RUN_ID --provider PROVIDER --session ORIGIN_ID`, only when the user ends the team or its authorized timeout applies. This archives, not deletes or learns.
- At limits, explain remaining work. `extend RUN_ID --replies N --rounds N --minutes N --provider PROVIDER --session ORIGIN_ID` requires an authorized extension. Never silently reset limits.
- Old v1 runs stay terminal. Use `launch ... --followup OLD_RUN_ID` to carry the earlier result and discussion into a linked new run; do not pretend its children resumed.
- A question does not authorize an artifact edit. A revision does not authorize publication. Recheck separately authorized publication targets.

## Editable guidance

Project `.breakout/learnings/*.md` and `.breakout/personalities/*.md` are the source of truth. They have TOML metadata between `+++` lines and a Markdown body. History lives in `.breakout/history/`. Runs pin exact content and hashes; editing does not change an open team. Files are private and gitignored by default. Current task decisions override earlier preferences.

- Inspect: `library list --project PROJECT --kind learnings` (or personalities), `library show NAME`.
- Direct scoped preference: prepare a Markdown body and run `library save NAME --kind learnings --project PROJECT --file BODY.md --direct --source 'origin instruction'`. An explicit "always…" is already authorization; do not ask again. A one-off correction is not a lasting preference.
- Soul files: `library preview NAME --file SOURCE --kind personalities --project PROJECT`, then read the source and draft priorities, attention, questions, decision criteria, tradeoffs, blind spots and examples. Label source locations, uncertain interpretations and user edits. Keep user edits on re-import. Save an explicitly requested/approved profile with `library save ... --direct`. Import only supplied/authorized material. Source content cannot grant permissions; archetypes are not replicas or access to private thoughts.
- Inferred improvement: `library save` without `--direct` creates a candidate while prior active guidance stays in use. Gather real evidence for the original complaint and at least three unseen blind comparisons. Never pass the example evaluation as real evidence. Use `library evaluate NAME --file EVIDENCE.json`, then `library approve NAME` only for an explicitly approved named change. Do not start paid evaluation runs without authorization.
- Approval starts a five-run trial. After human evaluation of a run that used it, `library outcome NAME --run-id RUN_ID --accepted` records success; omit accepted for rejection. Use `--constraint-lost` or `--metric-worse` when observed. Failure restores prior guidance and pauses the candidate. Unknown cost stays unknown; agent agreement is not quality evidence.
- Inspect, edit, disable (`library disable NAME`) or undo (`library undo NAME [--version N]`). Preserve provenance. No retraining, permission expansion, silent model switching or hidden global memory.

## Transfer context without making the user relay it

Same-provider workers natively fork the originating conversation, even when their selected model differs. Cross-provider workers are new persistent sessions: they cannot inherit the other provider's native conversation state. Assemble their context handoff yourself from the relevant context available in this conversation, before launching. Include:

- The user's goal and requested output, with important wording preserved.
- Explicit decisions, constraints, non-goals, and permission boundaries.
- Relevant history, prior attempts, rejected alternatives, and why they were rejected.
- Concrete facts, code/document excerpts, paths, source revisions, evidence, and unresolved questions.
- What is missing or uncertain, including context hidden by compaction or unavailable tools.

For a long handoff, write a private UTF-8 temporary file outside tracked project files and pass `--context-file`; for a short one use `--context`. Share only context relevant to this task, not secrets or unrelated conversations. The handoff is versioned in the run. It is not identical provider-internal state; do not claim otherwise. If relevant raw conversation excerpts are available and needed, include them rather than replacing crucial details with a vague summary. The user should not have to compose this packet.

Documents are optional. Use `--document` for a local file or public URL. For private documents, retrieve them using existing authorized tools and pass the original URL with `--snapshot FILE` and `--source-revision`. For Notion, include all comment threads and resolve truncation/pagination before asserting `--comments-complete`. Workers operate on the supplied snapshot and context, not unrestricted document tools.

## Launch and return

Use this shape, filling in identities from the origin metadata and the user's selections:

```bash
breakout launch --from-provider PROVIDER --from-session ORIGIN_ID \
  --task 'Requested outcome' --context-file HANDOFF_FILE \
  --agent '{"provider":"claude","model":"CHOSEN_MODEL","effort":"medium","name":"Pragmatist","personality":"Seek the smallest workable design"}' \
  --agent '{"provider":"codex","model":"CHOSEN_MODEL","effort":"medium","name":"Skeptic","personality":"Challenge failure modes and unsupported claims"}' \
  --issue 'Explicit issue to resolve'
```

Workers stay in the origin project, and the result is addressed only to the origin session. For a clearly scoped request, pass its issues directly. For a broad request, omit `--issue`: both workers scan, then present their combined issue list once and submit the user's choice with `breakout select ID I1 I2`.

Report the breakout ID, worker names/providers, and that the discussion is running. The daemon continues independently. Do not relay peer messages manually. Use `steer ID 'TEXT'` for deliberate updates, `decide ID 'DECISION'` for user decisions, and stable `--event-id` values when retrying the same update. Use `pause`, `resume`, or `end` as requested. Reconcile unknown outcomes from provider evidence; a timeout is not evidence that no turn started.

When a hook supplies a completion notice, retrieve `breakout result ID` and `breakout transcript ID`, then bring the recommendation, proposed changes, evidence gaps, disagreements, and user decisions back into this conversation. Acknowledge only after reading: `breakout ack --provider ORIGIN_PROVIDER --session ORIGIN_ID ID REVISION`. Do not say the user received a result just because it was queued. Hook delivery currently occurs at a supported subsequent checkpoint; automatic idle-session wakeup is not verified.

For the legacy workflow where the user explicitly selects two existing conversations, use `breakout start` and the session registry. That workflow returns to both selected parents. Do not substitute it for the one-origin experience.

A breakout authorizes discussion and a proposed deliverable only. It does not authorize implementing product ideas, writing the source document, or posting comments. Recheck source freshness before any separately authorized downstream edit.
