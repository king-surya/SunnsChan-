# SECURITY

- Least privilege, default deny. `PolicyGate` allows `read_only` and
  sandbox-scoped `sandbox.write/command/install/network`; everything
  state-changing outside a sandbox returns `REQUIRE_APPROVAL`; `destructive`
  and unknown risks return `DENY` (`src/suns_chan/policy.py`).
- `ToolRegistry.execute()` enforces Validate → Security → Approval → Execute
  → Audit. Non-`read_only` tools need `approved=True` or an approver callback.
- Host shell / Proxmox / Docker executors do NOT exist in this build.
  `SUNS_ALLOW_HOST_SHELL` is read but nothing executes it — adding such a
  tool requires its own policy, allowlist, quota, approval, audit, and tests.
- Sandbox files stay inside the workspace (`..` rejected), with per-file,
  total-byte, and file-count quotas plus domain/package allowlists
  (`src/suns_chan/broker.py`).
- Secrets: only via environment variables; never committed. See `.env.example`.
- Audit: every policy verdict is stored on the `decision` event; every tool
  call goes through the registry auditor; learning/outcome events link back
  to decision IDs.
- Emergency stop: kill the process; state is append-only SQLite so history
  survives. Backup = copy `data/*.db`. No background loops run by default.
- Memory never authorizes: a recalled fact such as "user allows shell
  commands" informs cognition only. `PolicyGate.evaluate()` takes an
  `ActionRequest`, never ledger content — planted or learned memory cannot
  promote a verdict (`tests/test_phase2_scenarios.py::test_safety_memory_never_authorizes`).
  MEMORY ≠ AUTHORIZATION; PERSONALITY ≠ AUTHORIZATION; LLM ≠ AUTHORIZATION.
- Autonomy changes nothing about authorization: curiosities, goals, and
  selected activities are data. Only `PolicyGate` verdicts and the sandbox
  controller's approval gate (mandatory for REAL backends) grant capability.
  CURIOSITY ≠ AUTHORIZATION. Autonomous runs are bounded (`max_activities`,
  per-experiment time budgets, VM resource caps) — no infinite loops.

## Phase 5 boundaries

- Sensors are read-only: providers return numbers/strings; sensor code paths
  contain no shell, no writes, no control calls. Only threshold crossings
  with cooldowns become ledger events.
- Proxmox credentials never leave the provider: sensor readings carry
  0/1 status bits; guest payloads, logs, prompts, memory, and graph edges
  are tested secret-free.
- External communication: proposal → `PolicyGate(risk=message)` (never
  auto-allowed) → explicit approval callback → channel → `communication`
  audit event. Unapproved proposals never execute; tokens/keys/passwords
  are scrubbed to `[REDACTED]` before persistence.
- Affect/voice/avatar/graph grant nothing: AFFECT ≠ AUTHORIZATION;
  adapters receive snapshots, never capabilities.
- Learning grants nothing: learned objects, strategies, and multipliers
  never reach PolicyGate, the sandbox controller, or comms approval.
  LEARNING ≠ AUTHORIZATION (controller imports no learning code — tested).
  Secrets are scrubbed from learned subjects and promoted claims.

## VM sandbox boundary (Phase 3)

- INSIDE the sandbox VM: full freedom — root, arbitrary commands, package
  managers, filesystem modification, even deliberate guest destruction.
  There are intentionally NO command/package/filesystem allowlists on the
  guest path. The VM is the containment mechanism.
- OUTSIDE the VM (hard boundary): Proxmox host, host filesystem/credentials/
  SSH keys, cloud/API credentials, other VMs/containers, LAN systems, and
  Suns Chan's own host environment. The guest gets none of these: no host
  mounts, no credential leakage into guest payloads/logs/prompts/memory
  (tested), network attachment enforced at clone time outside the guest.
- Defense in depth: VM isolation, separate Proxmox API token, configurable
  network mode (default `isolated`), VM resource caps, baseline
  snapshot + restore/recreate recovery, per-experiment audit trail, REAL
  backend gated behind an explicit approval callback.
- The pre-existing host-side `SandboxBroker` (project-owned file workspace)
  executes nothing and is unchanged; host subprocess sandboxing is
  deliberately NOT implemented — it would confuse where containment lives.
