# DEVELOPMENT

Requires Python 3.11+, no third-party runtime deps.

```powershell
cd "C:\Users\SURYA\Documents\Codex\2026-09-04\referenced-chatgpt-conversation-this-is-an-2\outputs\suns-chan-autonomous"
copy .env.example .env   # optional
python main.py --check
$env:PYTHONPATH="src"; python -m unittest discover -s tests -v
```

Note: `PYTHONPATH=src` is required because the package lives under `src/`
(src layout). The plain `python -m unittest discover -s tests` from README
fails with `ModuleNotFoundError` without it.

- `python main.py` boots with defaults (fake LLM, fake env, read-only tools).
- `python main.py --check` also runs one in-memory task end-to-end.
- `python main.py --chat` starts the terminal chat loop (decisions validated,
  persisted, policy-checked). Commands: `/recall <q>` `/knowledge <q>`
  `/sources <id>` `/reflect` `/consolidate [n]` `/audit` `/state`
  `/goals` `/quit`. Point at Ollama via `SUNS_LLM_PROVIDER=ollama`
  `SUNS_LLM_MODEL=llama3.1`; unreachable servers surface as visible chat
  errors, never fabricated replies.
- `python main.py --autonomy --session NAME --activities N [--no-sandbox]`
  runs a bounded self-directed session (mock VM sandbox by default;
  `SANDBOX_MODE=proxmox` + credentials + `SANDBOX_APPROVE_REAL=1` for real
  infrastructure). Prints per-step reports and a session summary.
- `Settings.load(path)` reads optional JSON then overlays `SUNS_*` env vars.
- Logs go to stdout + `logs/sunschan.log`; audit trail lives in the SQLite
  event ledger plus `logs/audit.log` when a tool auditor is wired.
- Tests use `TemporaryDirectory` + `FakeEnvironment` + `EchoProvider`; they
  never touch the host, network, or real models.
- VM sandbox: default `SANDBOX_MODE=mock` (in-memory, deterministic).
  Set `SANDBOX_MODE=proxmox` with `PROXMOX_HOST/USER/TOKEN_NAME/TOKEN_VALUE`
  (+ optional `PROXMOX_NODE`, `PROXMOX_TEMPLATE_ID`) to enable the real
  backend; `tests/test_proxmox_real.py` runs only then, otherwise it skips
  honestly. Mock results are always labeled MOCK, never presented as REAL.
- Phase 5 surfaces: sensors off by default (`SENSORS_ENABLED=1` + providers
  wired in code; no host shell involved). Voice/avatar default to `none`
  (text mode); `mock` enables deterministic stand-ins. Comms default
  disabled (`COMMS_ENABLED=1` + explicit per-proposal approval to send).
  Chat inspectors: `/environment /affect /graph <topic> /communications`.
- Learning: miners + `consolidate_learning()` run automatically after
  `python main.py --autonomy`; inspect with `/learning /patterns /skills
  /preferences /strategies /why <id>` (attach via chat extras or tests).
- Telemetry: disabled by default. `SUNS_TELEMETRY_ENABLED=1` +
  `SUNS_TELEMETRY_SOURCE=scripted|file` (+ `SUNS_TELEMETRY_PATH` for file),
  plus `SUNS_TELEMETRY_POLL/THRESHOLD/RETENTION/REDACT`. Chat inspector:
  `/telemetry`. Telemetry is read-only (the main server is observed, never
  written); significant events feed experience/understanding only.
