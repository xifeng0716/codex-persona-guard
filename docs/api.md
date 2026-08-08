# Persona Guard Local API

The browser and the installed hook client use this localhost-only JSON API. All responses use `application/json`; errors use `{ "error": { "code": "...", "message": "..." } }`.

## Hook

### `POST /api/hook`

Accepts the unchanged Codex `UserPromptSubmit` input object. The service always refreshes discovery metadata. If no enabled binding applies, the response is `{}` and the submitted prompt is not persisted.

For a successful `HIT`, the response is the Codex hook output:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "<binding HIT Reminder>"
  }
}
```

Every failure returns `{}` with HTTP 200 so the installed client can fail soft. The service records failures only for enabled, bound targets.

## Status and Global Switch

- `GET /api/status`
- `PUT /api/status` with `{ "enabled": true | false }`

Status includes service health, global enabled state, whether a supported key variable is present, active detector model, policy revision, binding count, and record count. It never returns a key value.

## Discoveries

- `GET /api/discoveries`

Returns recently observed threads (`session_id`, `cwd`, `last_seen`) and distinct exact workspaces derived from normalized `cwd` values. It never returns unbound prompt text.

## Bindings

- `GET /api/bindings`
- `POST /api/bindings`
- `PUT /api/bindings/{id}`
- `DELETE /api/bindings/{id}`

A binding write uses:

```json
{
  "name": "爱人",
  "target_type": "thread",
  "target_value": "<session id or normalized cwd>",
  "enabled": true,
  "reminder": "<HIT Reminder>"
}
```

`target_type` is `thread` or `workspace`. `(target_type, target_value)` is unique. Thread matches take precedence over workspace matches.

## Detector Policy

- `GET /api/policy`
- `PUT /api/policy` with `{ "text": "..." }`

A successful update immediately increments `revision` and resets every Guard State to `NORMAL`. Empty policy text is rejected.

## Calibration Records

- `GET /api/records?binding_id=&result=&limit=&before_id=`
- `DELETE /api/records` clears all records.
- `DELETE /api/records?binding_id={id}` clears records for one current or deleted binding.

The list is newest first. `result` accepts `HIT`, `WATCH`, `NONE`, or `ERROR`. Each record includes exact detector history/current prompt/policy snapshot, decision or error category, state before/after, binding snapshot, injection flag, model, and latency.

## Browser Behavior

The dashboard polls status, discoveries, bindings, and the newest records every two seconds while visible. Mutations refresh immediately. Destructive actions require an in-page confirmation.
