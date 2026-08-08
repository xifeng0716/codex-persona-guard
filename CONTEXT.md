# Persona Guard

Persona Guard protects selected Codex conversations from drifting into a generic assistant stance before the main model responds.

## Language

**Guard Binding**:
A saved association between one Guard Profile and either a Codex thread or a workspace. Multiple bindings may coexist; an exact thread binding takes precedence over a matching workspace binding, and bindings are never merged.
_Avoid_: Hook binding, target config

**Guard Profile**:
The editable persona-specific reminder used by a Guard Binding, such as the reminder for an intimate partner or a household assistant.
_Avoid_: Hook prompt, persona config

**HIT Reminder**:
The Guard Profile text injected only on a turn whose Detector Decision is `HIT`. Merely being in `HOT` never injects it.
_Avoid_: HOT prompt, automatic HOT reminder

**Guard State**:
The per-thread observation state carried between turns: `NORMAL`, `ARMED`, or `HOT`. Threads never share Guard State, including threads covered by the same workspace binding.
_Avoid_: Binding state, workspace state

**Detector Decision**:
The DeepSeek result for one submitted user turn: `NONE`, `WATCH`, or `HIT`, together with its risk type.
_Avoid_: Risk score, safety decision

**Detector Policy**:
The globally shared, frontend-editable instructions DeepSeek uses to make Detector Decisions. Each recorded decision identifies the policy version that produced it so calibration remains traceable.
_Avoid_: Guard Profile, per-thread prompt

**Calibration Record**:
A local, persistent record of one detector attempt, including the exact detector input, policy version, decision or failure, state transition, matched binding, injection outcome, model, and latency. Records remain until explicitly cleared.
_Avoid_: Transcript archive, application log
