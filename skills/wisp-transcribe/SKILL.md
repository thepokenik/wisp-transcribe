---
name: wisp-transcribe
description: Transcribe audio or video with Wisp Transcribe and save TXT, JSONL, and manifest results with timestamps preserved.
---

# Transcribe audio or video

Produce a complete transcript with Wisp Transcribe. This skill ends at transcription: do not summarize, interpret, or extract action items. Keep the timestamped segments intact for later use.

Use the installed `transcribe` CLI with `TRANSCRIPTION_URL` and `TRANSCRIPTION_API_KEY` supplied through the environment. Verify access with `transcribe doctor --json`. Missing configuration or authentication is a dependency to report; never expose the key. This skill needs shell, file and network access, but no particular agent provider or native skill loader.

For example, choose a new output directory and run:

```sh
transcribe run recording.mp4 --output ./recording-results --block-seconds 600 --json
```

The default language is Portuguese; set `--language en` for English or `--language ""` for detection. Stdout is JSON; progress is on stderr. Inspect `ok`, `state` and `files` rather than assuming success.

For long files, `transcribe submit FILE --json` returns the ID immediately. Then use `transcribe wait JOB_ID --json` and `transcribe fetch JOB_ID --output ./recording-results --json`.

On completion, report the `job_id`, `state`, and absolute paths for:

- `files.text`: plain-text transcript (`transcript.txt`).
- `files.segments`: JSONL segments with `start`, `end` and `text` (`transcript.jsonl`).
- `files.manifest`: metadata and local paths (`manifest.json`).
- `blocks`: optional chunk files when `--block-seconds` is greater than zero; each keeps timestamps.

Do not remove, round or rewrite timestamps in the JSONL, blocks or manifest. Later interpretation may rely on them. If the user also asked for a summary, finish this transcription first, then give the saved files to the `summarize-transcript` skill.

Preserve any returned `job_id`. After a wait timeout, resume with `transcribe wait JOB_ID --json`, then `transcribe fetch JOB_ID --output ./recording-results --json`. A failed request may leave the server processing. If upload confirmation was lost, retry the same file and options with default idempotency; do not disable it merely to retry. Stop repeated attempts when configuration or media must change. Ctrl+C may leave the ID only in stderr.

An empty, failed or still-running job is not a transcript. Report the state and error instead of inventing content. Do not start another service, switch transcription providers or modify the server.

For setup, API-only integration or detailed failures, consult the repository [README](../../README.md); for operational limits, consult [REVIEW.md](../../REVIEW.md). If this skill is copied elsewhere, preserve those references or provide the repository URL alongside it: https://github.com/thepokenik/wisp-transcribe.
