# Persona Guard

Persona Guard is a small localhost service plus a Codex `UserPromptSubmit`
hook. It asks a lightweight DeepSeek detector whether the next reply is at
risk of drifting into a generic assistant stance. Only a detector result of
`HIT` adds the configured reminder; `WATCH` and `HOT` never inject a reminder
by themselves.

## Requirements

- Python 3.10 or newer with its standard library
- Codex hooks enabled and a local Codex installation with `/hooks`
- A DeepSeek API key in the environment used to start the service

There are no third-party runtime or test dependencies.

## Configure DeepSeek

The portable, documented variable is `DEEPSEEK_API_KEY`:

```sh
export DEEPSEEK_API_KEY='your-key'
```

For compatibility with an existing GMEM environment, Persona Guard also
accepts `GMEM_DEEPSEEK_API_KEY`. The backend checks that variable first and
falls back to `DEEPSEEK_API_KEY`. Keys are read from the inherited process
environment; they are never written to the repository, database, or logs.

`.env.example` is a template only. `scripts/run-server` does not load dotenv
files, so export the variable in the shell that starts the service.

## Install the Codex hook

From this repository, run:

```sh
./scripts/install-hook
```

The installer copies the standalone client to
`$HOME/.codex/persona-guard/hook_client.py` and merges one command handler
into `$HOME/.codex/hooks.json`. Existing hooks are retained. When an existing
hooks file is changed, it is copied first to
`$HOME/.codex/hooks.json.persona-guard.bak` (or a timestamped sibling if that
name already exists). Re-running the installer is safe and idempotent.

Open Codex and review/trust the new command through `/hooks`. The installer
does not alter Codex trust state.

## Start and bind a target

Start the service manually in a shell where the key is exported:

```sh
./scripts/run-server
```

It listens only on `127.0.0.1:43821`. Keep it running while using Codex.

The first prompt from a new session or workspace is metadata-only: the
service discovers the `session_id` and normalized `cwd`, but an unbound target
does not send or persist prompt text. Open the local dashboard at
`http://127.0.0.1:43821`, bind the discovered thread or exact workspace, and
then submit the next prompt. Protection begins after the binding is saved.
An exact thread binding takes precedence over a matching workspace binding.
The dashboard lets each binding use its own HIT reminder and lets you edit the
global detector policy, so the included relationship-oriented defaults are a
starting point rather than a required persona.

## Privacy, data, and backups

Runtime data is stored locally in SQLite at:

```text
$XDG_STATE_HOME/persona-guard/guard.db
```

If `XDG_STATE_HOME` is unset, the fallback is
`$HOME/.local/state/persona-guard/guard.db`. The service listens on localhost
only and restricts the state directory to the current user. Calibration
records are created only for enabled, bound targets and retain the detector
history/current prompt, policy snapshot, decision or error, state transition,
and binding snapshot for local calibration. Unbound and disabled prompts are
not recorded or sent to DeepSeek. Clear records from the dashboard when they
are no longer wanted.

Before moving or deleting the database, stop the service and copy the file
and its SQLite sidecar files (`-wal` and `-shm`) together. The hook installer
also leaves the hooks configuration backups described above.

## Fail-soft behavior

The installed client reads one JSON object from stdin and gives it to the
local service with a five-second total request budget. A malformed input,
missing service, timeout, local/HTTP error, or invalid JSON response exits
zero and prints nothing, so Codex continues normally. A valid service JSON
response is forwarded unchanged. Detector failures likewise preserve the
existing guard state and do not inject a reminder.

## Tests

Run the focused tests with:

```sh
python3 -m unittest discover -s tests -v
```

The installer tests use temporary isolated `HOME` directories and never
write to the real user home.

## Uninstall

To remove Persona Guard's command and copied client:

```sh
./scripts/uninstall-hook
```

The uninstaller removes only the exact Persona Guard handler and its copied
client/bytecode. It preserves unrelated hooks and is safe to run repeatedly.
It does not delete the local SQLite calibration database. Remove that data
only after making any desired backup, using the same state path described
above.

## License

Persona Guard is released under the [MIT License](LICENSE).
