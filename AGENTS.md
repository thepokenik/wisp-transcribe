# Wisp Transcribe

Wisp Transcribe is a CPU transcription service with an HTTP API and a Python CLI. The server runs Whisper; the consuming agent produces the summary. No LLM provider account, SDK, MCP server, or provider-specific tools are required by this project.

## Using the service

For transcription or summarization, read [the skill](skills/wisp-transcribe/SKILL.md). It can also be supplied as plain Markdown to agents without skill discovery. Agents that do not load AGENTS.md automatically should be told to read this file explicitly.

The client needs Python 3.10+, shell execution, local file access, and network access to the service. Install it with `python -m pip install ./cli` in an isolated environment. The command is `transcribe`; `python -m transcription_cli` is equivalent. Configure `TRANSCRIPTION_URL` and `TRANSCRIPTION_API_KEY` in the agent process environment. The CLI does not load `.env`; do not print the key.

Use the [README](README.md) for server setup, API-only clients, CLI flags, and exit codes. If setup or credentials are missing, report that dependency rather than installing a server or choosing another transcription provider implicitly. Starting or publishing the service is separate from using an existing endpoint.

## Changing the project

- `service.py`: HTTP contract, job persistence and single CPU worker.
- `cli/src/transcription_cli/`: client, commands and local result blocks; standard library only.
- `index.html`: browser client. `REVIEW.md`: known limitations and compatibility notes.

When changing a contract, check both the CLI and browser consumers. Preserve `schema_version`, JSON stdout/stderr separation, exit codes, timestamps and resumable job IDs unless the task explicitly changes them. Queue state is in memory; interrupted jobs require re-upload. One shared key accesses all jobs.

Use Python 3.12 for the server. For Python edits, `python -m compileall -q service.py cli/src` checks syntax. Compose configuration can be checked with `docker compose --env-file .env.example config --quiet`. Choose integration checks for changed behavior; there is no committed automated test suite. Simulated transcription does not validate speech accuracy. Do not include `.env`, media, results, caches or generated packages in commits.
