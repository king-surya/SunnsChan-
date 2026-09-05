"""Suns Chan boot sequence. `python main.py [--check]` starts cleanly with defaults."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from suns_chan import (  # noqa: E402
    AgentCore,
    AgentRuntime,
    AutonomyEngine,
    CapabilityStore,
    CuriosityStore,
    EventBus,
    EventLedger,
    FakeEnvironment,
    IdentitySeed,
    KnowledgeStore,
    LearnedStore,
    MockSandboxProvider,
    PolicyGate,
    SandboxController,
    SessionStore,
    Settings,
    TelemetryCollector,
    TelemetryState,
    UnderstandingStore,
    VMResources,
    build_chat_decide,
    default_registry,
    handle_command,
    health_check,
    mine_understanding,
    provider_from_settings,
    setup_logging,
)


def build_runtime(settings: Settings, bus: EventBus | None = None) -> tuple[AgentRuntime, EventLedger, KnowledgeStore]:
    db_path = Path(settings.database.path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = EventLedger(db_path)
    store = KnowledgeStore(settings.database.path)
    identity_path = Path(__file__).resolve().parent / "config" / "identity_seed.json"
    if not identity_path.exists():
        raise FileNotFoundError(f"identity seed not found: {identity_path}")
    identity = IdentitySeed.from_file(identity_path)
    core = AgentCore(ledger, PolicyGate(), identity)
    tools = default_registry()
    env = FakeEnvironment()
    runtime = AgentRuntime(core, PolicyGate(), tools, env, bus or EventBus())
    return runtime, ledger, store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suns Chan runtime")
    parser.add_argument("--check", action="store_true", help="verify boot + run a fake task")
    parser.add_argument("--chat", action="store_true", help="start the terminal chat loop")
    parser.add_argument("--autonomy", action="store_true",
                        help="run a bounded autonomous session (mock sandbox by default)")
    parser.add_argument("--session", default="main", help="autonomy session id")
    parser.add_argument("--activities", type=int, default=3, help="max activities per run")
    parser.add_argument("--no-sandbox", action="store_true", help="cognitive activities only")
    parser.add_argument("--config", default="", help="optional JSON config file")
    args = parser.parse_args(argv)

    settings = Settings.load(args.config or None)
    logger = setup_logging(settings.logging.level, settings.logging.log_path)
    logger.info("suns-chan boot provider=%s model=%s db=%s", settings.llm.provider, settings.llm.model, settings.database.path)

    runtime, ledger, store = build_runtime(settings)
    health = health_check()
    print(f"Suns Chan ready ({health['status']}). db={settings.database.path} env=fake tools={len(runtime.tools.list_specs())}")

    if args.check:
        from suns_chan import Decision, Task

        result = runtime.run_task(Task(title="boot self-test", max_steps=2), lambda ctx: Decision(f"ok: {ctx.observation}"))
        print(f"check: outcome={result.outcome} stages={len(result.steps)}")
        ledger.close()
        store.close()
        return 0

    if args.chat:
        return run_chat(settings, runtime, ledger, store)

    if args.autonomy:
        ledger.close()
        store.close()
        return run_autonomy(settings, args.session, args.activities, use_sandbox=not args.no_sandbox)
    return 0


def build_telemetry(settings: Settings, db_path) -> tuple[TelemetryCollector | None, TelemetryState | None]:
    """Build a telemetry collector from configuration (or None when disabled).

    Implemented sources: `scripted` (deterministic, empty by default) and
    `file` (REAL read-only log tail). Other source types are provider-dependent
    and not bundled; they fail closed with a clear message.
    """
    tcfg = settings.telemetry
    if not tcfg.enabled or tcfg.source in ("", "none"):
        return None, None
    from suns_chan import FileTelemetrySource, ScriptedTelemetrySource

    if tcfg.source == "scripted":
        source = ScriptedTelemetrySource(name="main-server", script=[])
    elif tcfg.source == "file":
        if not tcfg.source_path:
            print("telemetry: file source requires SUNS_TELEMETRY_PATH")
            return None, None
        source = FileTelemetrySource(name="main-server", path=tcfg.source_path)
    else:
        print(f"telemetry: unsupported source {tcfg.source!r} (scripted|file)")
        return None, None
    state = TelemetryState(db_path, retention=tcfg.retention)
    collector = TelemetryCollector(source, state,
                                   importance_threshold=tcfg.importance_threshold,
                                   redact=tcfg.redact)
    return collector, state


def run_autonomy(settings: Settings, session_id: str, max_activities: int, *, use_sandbox: bool) -> int:
    """Real autonomous environment (not the fake chat/task path).

    Backend follows SANDBOX_MODE: mock by default; proxmox requires
    PROXMOX_* credentials AND explicit SANDBOX_APPROVE_REAL=1 for this run.
    """
    import os

    db_path = Path(settings.database.path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = EventLedger(db_path)
    store = KnowledgeStore(db_path)
    curiosities = CuriosityStore(db_path)
    sessions = SessionStore(db_path)
    learned = LearnedStore(db_path)
    understanding = UnderstandingStore(db_path)
    capabilities = CapabilityStore(db_path)
    identity_path = Path(__file__).resolve().parent / "config" / "identity_seed.json"
    if not identity_path.exists():
        raise FileNotFoundError(f"identity seed not found: {identity_path}")
    core = AgentCore(ledger, PolicyGate(), IdentitySeed.from_file(identity_path))

    mode = settings.sandbox.mode
    if mode == "proxmox":
        from suns_chan import ProxmoxCredentials, ProxmoxSandboxProvider

        if os.environ.get("SANDBOX_APPROVE_REAL", "0") != "1":
            print("Refusing REAL run: set SANDBOX_APPROVE_REAL=1 to approve this invocation.")
            return 2
        try:
            provider = ProxmoxSandboxProvider(ProxmoxCredentials.from_env())
        except ValueError as exc:
            print(f"Proxmox not configured: {exc}")
            return 2
        approver = lambda proposal: True  # this CLI invocation is the human approval
    elif mode == "mock":
        provider = MockSandboxProvider()
        approver = None
    else:
        print(f"Unknown SANDBOX_MODE: {mode!r} (expected mock|proxmox)")
        return 2
    controller = SandboxController(
        provider, template=settings.sandbox.template,
        default_resources=VMResources(
            vcpus=settings.sandbox.vm_vcpus, ram_mb=settings.sandbox.vm_ram_mb,
            disk_gb=settings.sandbox.vm_disk_gb),
        approver=approver,
    )
    try:
        llm = provider_from_settings(settings.llm.provider, settings.llm.model,
                                     base_url=settings.llm.base_url,
                                     timeout_secs=settings.llm.timeout_secs)
    except ValueError as exc:
        print(f"LLM provider error: {exc}")
        return 2
    engine = AutonomyEngine(ledger, core, sessions, knowledge=store,
                            curiosities=curiosities, sandbox_controller=controller,
                            affect_enabled=True, learned=learned,
                            understanding=understanding, capabilities=capabilities)
    collector, tel_state = build_telemetry(settings, db_path)
    from suns_chan import SensorHub

    hub = SensorHub([], [])
    if settings.sensors.enabled:
        print("(sensor providers: none configured in this build; watching ledger sensor events)")
    for new_id in hub.poll(ledger):
        print(f"sensor event #{new_id}")
    wake = hub.evaluate_wake(ledger)
    if wake.reason:
        ledger.record("wake", wake.reason, {"trigger": "sensor",
                                            "event_ids": list(wake.event_ids)})
        print(f"wake: {wake.reason}")
    print(f"Autonomy session '{session_id}' backend={controller.backend} "
          f"sandbox={use_sandbox} max_activities={max_activities}")
    if collector is not None:
        tel_report = collector.collect(ledger)
        print(f"telemetry: {tel_report.status} new={tel_report.new_events} "
              f"significant={tel_report.significant} dedup={tel_report.deduplicated}")
    report = engine.run(session_id, build_chat_decide(llm), max_activities=max_activities,
                        use_sandbox=use_sandbox)
    if collector is not None:
        tel_report = collector.collect(ledger)
        print(f"telemetry: {tel_report.status} new={tel_report.new_events} "
              f"significant={tel_report.significant} dedup={tel_report.deduplicated}")
        from suns_chan import promote_telemetry

        experience_id, touched = promote_telemetry(ledger, understanding)
        if experience_id or touched:
            print(f"telemetry: promoted experience=#{experience_id} understanding={len(touched)}")
    for step in report.steps:
        print(f"- [{step.category}] {step.activity_key}: {step.result} ({step.status})")
    view = engine.describe(session_id)
    print(f"status={view['status']} completed={view['activities_completed']} "
          f"open_curiosities={len(view['open_curiosities'])} next={view['next']}")
    from suns_chan import consolidate_learning, consolidation_due

    due, why = consolidation_due(ledger, learned)
    if not due:
        print(f"learning: skipped ({why})")
    else:
        learning = consolidate_learning(ledger, store, learned, budget=20)
        print(f"learning: {why}; patterns={learning.patterns} skills={learning.skills} "
              f"preferences={learning.preferences} strategies={learning.strategies} "
              f"promoted={learning.promoted} demoted={learning.demoted}")
        if learning.errors:
            print(f"learning warnings: {learning.errors[:3]}")
    mined = mine_understanding(ledger, understanding)
    if mined:
        print(f"understanding: derived {len(mined)} record(s) about Surya from this session")
    ledger.close()
    store.close()
    curiosities.close()
    sessions.close()
    learned.close()
    understanding.close()
    capabilities.close()
    return 0


def run_chat(settings: Settings, runtime: AgentRuntime, ledger: EventLedger, store: KnowledgeStore) -> int:
    understanding = UnderstandingStore(settings.database.path)
    capabilities = CapabilityStore(settings.database.path)
    try:
        provider = provider_from_settings(
            settings.llm.provider,
            settings.llm.model,
            base_url=settings.llm.base_url,
            timeout_secs=settings.llm.timeout_secs,
        )
    except ValueError as exc:
        print(f"LLM provider error: {exc}")
        understanding.close()
        capabilities.close()
        return 2
    decide = build_chat_decide(provider, understanding_store=understanding)
    print("Suns Chan chat. Commands: /recall <q> /knowledge <q> /sources <id> /reflect /consolidate [n] /audit /state /goals /understanding <q> /capabilities /capability <n> /agents /acquisitions /environment /affect /graph <topic> /communications /quit")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        handled, output = handle_command(ledger, line, store=store, understanding=understanding,
                                         capabilities=capabilities)
        if handled:
            if output == "__quit__":
                break
            print(output)
            continue
        try:
            decision, verdict = runtime.core.turn(line, decide)
        except ValueError as exc:
            print(f"[invalid decision recorded: {exc}]")
            continue
        print(f"suns> {decision.response}")
        if verdict is not None:
            print(f"  (policy: {verdict.value} action={decision.action.name if decision.action else None})")
        mine_understanding(ledger, understanding)
    ledger.close()
    store.close()
    understanding.close()
    capabilities.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
