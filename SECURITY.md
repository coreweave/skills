# Security

## Reporting a vulnerability

Report suspected vulnerabilities in this pipeline or in the shipped skills
privately, through GitHub's private vulnerability reporting on this
repository ("Security" → "Report a vulnerability"). Please don't open a
public issue or pull request for a security problem.

## What this repository guards against

The skills here are instructions for an AI agent operating on live
infrastructure, so every command in a fenced code block is something an agent
may execute with the user's credentials. Each destructive command must be
preceded by a `> **Checkpoint:**` human-confirmation gate, and `build.py`
fails the build when a gate is missing or when a marker has drifted out of
its canonical form.

That is a build-time check that gates **exist** in the shipped text. It is
not runtime enforcement, and it is not a complete inventory of destructive
behavior: it covers five command classes, and `kubectl apply` is not among
them. A green build is not evidence that a skill's destructive commands are
gated — review is what covers the rest.

Writing or editing a skill? The rules you have to follow are in
[CONTRIBUTING.md](CONTRIBUTING.md#gate-destructive-commands-with-a-checkpoint).

Changing the check itself? Its design, trade-offs, and limits are documented
where they have to be maintained — `validate_rendered_bodies` and
`_classify_block_lines` in `build.py` — with a regression fixture per closed
bypass in `tests/`.
