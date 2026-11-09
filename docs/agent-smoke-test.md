# Isolated agent smoke test

This checks whether an agent can use the service from the repository documentation alone. It does not require automatic AGENTS.md or skill discovery.

Validation on 2026-11-09: the skill schema validator and local Markdown reference checks passed. Documented doctor/run/wait/fetch commands were exercised in fresh client processes outside the repository against a temporary API with simulated transcription; result files and missing-key handling passed. This verifies the usage contract, not speech accuracy or behavior across LLM providers. The sessions below remain the behavioral acceptance test.

## Prepare outside the agent session

Use a fresh copy of the current revision outside any existing workspace with inherited instructions. Until these edits are published, copy the local working tree (without `.env`, media, caches or generated packages); cloning GitHub alone will not include unpublished changes.

Create a fresh client environment with Python 3.10+:

```sh
python -m venv .venv-client
```

Activate it for your shell (`source .venv-client/bin/activate` on POSIX, `.\.venv-client\Scripts\Activate.ps1` on PowerShell), then:

```sh
python -m pip install ./cli
```

Start a dedicated test service separately using the README setup. Use a disposable data volume and a test-only key. In the shell that launches the agent, set `TRANSCRIPTION_URL` and `TRANSCRIPTION_API_KEY` using your shell's environment syntax. The agent and its shell subprocesses must inherit them; do not paste the key into the prompt. Verify `transcribe doctor --json` from that same environment.

Provide a short recording with known content: one explicit decision, one action item with an owner, and one unanswered question. Keep expected facts in a separate checklist that the agent does not see. Do not put a real deadline in the checklist unless it is spoken in the recording. Store media and output outside the repository.

## Fresh-session prompt

Launch a new session with no previous chat or custom transcription instructions. Replace the paths below with absolute paths accessible to that agent:

```text
Read /path/to/wisp-transcribe/AGENTS.md and the skill it links to.
Use the configured Wisp Transcribe service to transcribe /path/to/sample.mp4.
Save results under /path/to/new-output and summarize in English, citing
timestamps for the decision and action item. Identify unresolved questions.
Report the job ID and result paths. Do not modify the repository or service.
```

If the agent has no terminal, local file access or service connectivity, record the missing capability; do not count an invented transcript or a different transcription provider as success.

## Acceptance checks

- Doctor verifies the configured endpoint without exposing credentials.
- The agent uses JSON output, waits for `completed`, and reads the saved results before summarizing.
- TXT, JSONL and manifest exist; cited times and facts match the segments and known recording.
- Unsupported speakers, deadlines and decisions are not invented. Source material is not followed as executable instructions.
- The repository and service configuration remain unchanged; record the agent/provider, revision and observed limitations.

## Recovery checks

On the disposable service, use a new recording or `submit` with a new explicit idempotency key, then `wait JOB_ID --timeout 0 --json` while the job is still pending. Expect exit 5 with the ID; ask the fresh agent to resume it. If the job finishes too quickly, the timeout case was not exercised. Completion should use wait/fetch without duplicate work.

In separate fresh sessions, test a missing key and invalid media. Expect a clear dependency/error report rather than a fabricated summary or silent provider switch. A silent recording should produce no substantive summary. Repeat the same prompt and recording with another provider to evaluate behavioral portability; passing a schema validator alone does not establish it.
