# Security policy

## Reporting a vulnerability

Please **don't** open a public issue for security problems. Report them
privately through GitHub's **Report a vulnerability** button on this repository
(Security → Advisories). Include what an attacker can do, the steps to
reproduce it, and the affected version (`breakout --version`).

You can expect an acknowledgement within a week. Please give us a reasonable
amount of time to release a fix before you disclose the issue publicly.

## Supported versions

breakout-coordinator is pre-1.0. Only the latest release receives fixes.

## Security model

The coordinator is designed for **one person, on one machine**. Please read these
boundaries before you report something. A report that shows the coordinator
crossing one of them is exactly what we want to hear about.

**In scope**

- **Local access:** another OS user reaching the daemon or its records. The
  runtime directory is mode 0700, and the socket and database are mode 0600.
- **Worker permissions:** a worker gaining tools, shell access, MCP servers or
  write access. Workers are discussion-only.
- **Context leaving the machine:** context sent to an API connection without
  first-send authorization, or to a different endpoint than the one authorized.
- **Credentials:** API keys or CLI credentials copied into the database, logs,
  results or transcripts. Configuration holds environment-variable references
  only.
- **Delivery:** a result delivered to a session other than the origin, a blind
  replay after an uncertain dispatch, or a child session accepted as an origin.
- **Hooks and skills:** install or uninstall changing settings it doesn't own.

**Out of scope, by design**

- **Same-user processes:** they can read and write the runtime directory.
- **What the origin agent chooses to share:** the context packet is written by
  the originating agent from your conversation. Keep secrets out of it.
- **Caller attestations:** flags such as `--comments-complete` are statements by
  the caller, not something the coordinator can check.
- **Model output:** agreement between agents is not evidence that their work is
  correct.
