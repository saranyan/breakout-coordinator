# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `breakout --version`.
- License, contributing guide, security policy and code of conduct.
- A rewritten README, a guide, a reference, a verification page, a roadmap, and
  the Breakout Rooms design proposal. Tests run on GitHub Actions.

### Fixed

- The package reported version 0.1.0 while being 0.2.0.

## [0.2.0] - 2026-10-16

### Added

- **Review window:** a finished team parks without model calls. You can ask a
  named member or the whole team a question, request a revision (which the
  reviewers must approve again at the exact new version), accept, and close.
- **Team recipes:** an access map (`.breakout/config.toml`) with connections,
  profiles, personalities and 2–4 member teams (one author, required reviewers,
  optional advisory members).
- **OpenAI-compatible API members**, with explicit first-send consent and
  credentials kept as environment-variable references.
- **Editable guidance:** versioned personality cards and learnings as Markdown
  files in the project, pinned per run, with candidates, evaluation records,
  trials and undo.
- **Linked follow-ups** that carry an earlier run's result into a new team.
- `breakout upgrade` for an idle installation.

### Verified live

- A team launched from Codex with Claude Opus 5.5 and Codex Sol (both medium)
  produced an artifact, answered a member-addressed question, completed a
  requested revision, and both members approved the final version.

## [0.1.0] - 2026-10-15

### Added

- **One-origin launch:** start a two-agent discussion from the current Claude or
  Codex conversation. Same-vendor agents fork it natively; other vendors receive
  a context packet. The result returns only to the origin's mailbox.
- **Two-parent discussions** (`start`) between registered sessions, with an issue
  ledger, steering, user decisions, pause/resume and bounded rounds.
- **Durable daemon:** an owner-only Unix socket and SQLite records, with attempt
  IDs committed before dispatch and manual reconciliation of uncertain turns.
- **Hooks and the `/breakout` skill**, installed and removed explicitly.
- **Probes and trials** for verifying real providers on disposable sessions.
- A simulated demo that needs no credentials.
