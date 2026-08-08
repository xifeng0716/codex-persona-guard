# Persona Guard Confirmed Requirements

This document records decisions confirmed during the design interview. It is updated as decisions are made.

## Product Boundary

- The guard runs at Codex `UserPromptSubmit`, before the main model responds.
- DeepSeek classifies only generic-assistant drift risk. It does not generate replies, infer user needs, or perform safety triage.
- Only a `HIT` decision injects a reminder. `HOT` is an observation state and never injects by itself.
- Detector context is fixed at six historical messages: three user messages and three assistant messages. The latest submitted user prompt is supplied separately and is not duplicated.
- The implementation must be the simplest one that satisfies current requirements: no preventive abstractions, unnecessary configuration layers, or developer-machine absolute paths in source and checked-in configuration.

## Bindings and Profiles

- Multiple Guard Bindings may coexist.
- A binding targets either one Codex thread or one exact workspace.
- Each binding has its own frontend-editable HIT Reminder so distinct personas, such as an intimate partner and a household assistant, can receive different reminders.
- An exact thread binding overrides a matching workspace binding. Reminder texts are never merged, and DeepSeek is called at most once for a submitted prompt.
- A target can have at most one binding.
- Guard State is isolated by Codex `session_id`, even when multiple threads use one workspace binding.
- Guard State remains keyed only by `session_id`; it does not track binding identity or add special binding-migration resets.
- Deleting a binding removes the directly associated thread state where applicable. The first version adds no broader state-reconciliation layer.

## Detector Policy

- All bindings share one global, frontend-editable Detector Policy.
- Saving the policy makes it active immediately, increments its revision, and resets all Guard States to `NORMAL`.
- Existing Calibration Records retain the exact older policy text/revision used for their decisions.
- Editing a binding's HIT Reminder does not reset Guard State.
- There is no draft/publish workflow or separate policy-version management UI in the first version.

## Transcript Window

- Detector history contains at most six completed historical messages, excluding the newly submitted prompt.
- A thread with sparse history uses every available completed user/assistant message up to three messages per role, without padding or duplication.
- Insufficient history is valid and does not prevent detection. An unreadable or unparseable transcript fails soft instead.
- When six messages are available, the window contains the latest three user messages and three assistant messages and preserves the latest assistant response whenever possible.

## Activation Controls

- The frontend has one global enabled switch and one enabled switch per binding.
- A disabled guard performs no DeepSeek call, injection, state transition, or prompt-content recording.
- Disabling does not erase bindings, Guard State, profiles, or existing Calibration Records. Re-enabling resumes the preserved state.
- Hook metadata reporting remains active while guards are disabled so targets can still be discovered.

## Target Discovery and Workspace Identity

- The normalized `cwd` received from the hook is the Workspace ID.
- Workspace matching is exact. A session launched from a child directory is a different workspace and is not covered by its parent's binding.
- The service discovers targets from `UserPromptSubmit` events and records only `session_id`, normalized `cwd`, and last-seen time before a target is bound.
- An unbound target's prompt text is not persisted or sent to DeepSeek.
- A thread appears in the frontend after any prompt has caused its first metadata-only hook event. Protection begins on a later prompt after the binding is created.
- The implementation does not enumerate Codex private databases or depend on them to discover old threads.

## Local Application

- One manually started local web service listens only on `127.0.0.1:43821`.
- The backend uses the Python standard library for HTTP, SQLite, DeepSeek requests, and static file serving.
- The frontend uses plain HTML, CSS, and JavaScript with no build step.
- There are no third-party runtime dependencies, tray application, operating-system service, automatic startup, or detector fallback inside the hook.
- If the local service is absent or fails, the hook fails soft and Codex continues without injection.

## Hook Installation

- Persona Guard installs one user-level `UserPromptSubmit` hook so a single service can cover bindings across multiple workspaces.
- The installer copies the lightweight hook client under `$HOME/.codex/persona-guard/` and safely merges its handler into `$HOME/.codex/hooks.json` without replacing unrelated hooks.
- The installed command uses `$HOME` rather than a developer-machine absolute path.
- Installation preserves a backup of an existing hook configuration.
- The uninstaller removes only Persona Guard's handler and installed hook files.
- Public documentation covers installation, Codex `/hooks` trust review, service startup, and removal.

## DeepSeek Runtime

- Detector calls use the official `https://api.deepseek.com` endpoint, model `deepseek-v4-flash`, with thinking disabled.
- API keys are read only from environment variables and are never stored, displayed, or logged.
- For compatibility with the owner's Lumen Nest environment, key discovery checks `GMEM_DEEPSEEK_API_KEY` and falls back to the portable `DEEPSEEK_API_KEY`.
- Public setup documentation presents `DEEPSEEK_API_KEY` as the standard option.
- The first version has no provider, model, endpoint, or API-key settings screen.
- Each DeepSeek request has a four-second timeout and is never retried.
- The hook allows at most five seconds for the complete local-service request.
- Timeouts, rate limits, server failures, network failures, and invalid responses fail soft, preserve Guard State, and create a key-free Calibration Record with an error category.

## Calibration Records

- Each detector attempt persists the exact six-message history and latest user prompt, Detector Policy text/revision, decision or failure, state before/after, matched binding, injection outcome, model, latency, and error category.
- Records remain in local SQLite until the user explicitly clears them.
- The frontend supports clearing one binding's records and clearing all records.
- Runtime data lives under `$XDG_STATE_HOME/persona-guard/guard.db`, falling back to `$HOME/.local/state/persona-guard/guard.db`.
- The state directory and database are restricted to the current user. The repository never contains the live database or conversation records.
- The first version has no custom data-path setting; public documentation explains the location, backup, and complete removal.

## Frontend Scope

- The first version is one responsive page with no routing, accounts, authentication, or application framework.
- It contains four areas: runtime status, binding management, Detector Policy editing, and filterable/expandable Calibration Records.
- Deleting a thread binding removes that thread's Guard State. Deleting a workspace binding performs no broad thread-state cleanup. Calibration Records remain as records of a deleted binding.
- The service listens on localhost only, so the first version has no authentication layer.
- The layout adapts for mobile: one-column cards, readable prompt transcripts, and touch targets of at least 44 pixels.
- Visual direction follows Lumen Nest's restrained warm dashboard language: warm paper background, white surfaces, coral primary actions, subtle borders, system fonts, and compact rounded cards.
- Persona Guard does not copy Lumen Nest's sidebar, theme system, Tailwind runtime, icon libraries, or multi-page complexity.
