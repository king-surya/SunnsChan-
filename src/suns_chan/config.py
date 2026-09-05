"""Centralized configuration. Env vars > file > defaults. No secrets hardcoded."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "fake"  # fake | ollama | llamacpp | vllm
    model: str = "fake-echo"
    base_url: str = ""
    timeout_secs: float = 30.0


@dataclass(frozen=True)
class MemorySettings:
    database_path: str = "data/sunschan.db"
    recall_limit: int = 8


@dataclass(frozen=True)
class DatabaseSettings:
    path: str = "data/sunschan.db"


@dataclass(frozen=True)
class ToolSettings:
    allow_sandbox_write: bool = True
    allow_host_shell: bool = False  # never enabled by default
    default_timeout_secs: float = 30.0


@dataclass(frozen=True)
class SecuritySettings:
    require_approval_outside_sandbox: bool = True
    audit_log_path: str = "logs/audit.log"


@dataclass(frozen=True)
class EnvironmentSettings:
    provider: str = "fake"  # fake | homelab (future)
    poll_interval_secs: int = 60


@dataclass(frozen=True)
class InterfaceSettings:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    log_path: str = "logs/sunschan.log"


@dataclass(frozen=True)
class APISettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class SensorSettings:
    enabled: bool = False
    poll_interval_secs: int = 300


@dataclass(frozen=True)
class CommsSettings:
    enabled: bool = False
    allowed_channels: tuple[str, ...] = ("console",)


@dataclass(frozen=True)
class VoiceSettings:
    input_provider: str = "none"  # none | mock
    output_provider: str = "none"  # none | mock


@dataclass(frozen=True)
class AvatarSettings:
    provider: str = "none"  # none | mock


@dataclass(frozen=True)
class TelemetrySettings:
    enabled: bool = False
    source: str = "none"  # none | scripted | file | journald | syslog
    source_path: str = ""  # for `file` source
    poll_interval_secs: int = 30
    importance_threshold: str = "normal"  # low | normal | important | critical
    retention: int = 1000
    redact: bool = True


@dataclass(frozen=True)
class SandboxSettings:
    mode: str = "mock"  # mock | proxmox
    network_mode: str = "isolated"  # isolated | restricted | internet
    template: str = "debian-12-baseline"
    vm_vcpus: int = 2
    vm_ram_mb: int = 4096
    vm_disk_gb: int = 40
    # Proxmox credentials are NEVER read from config files — env only,
    # and only the infrastructure layer consumes them.
    proxmox_configured: bool = False


@dataclass(frozen=True)
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    environment: EnvironmentSettings = field(default_factory=EnvironmentSettings)
    interface: InterfaceSettings = field(default_factory=InterfaceSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    api: APISettings = field(default_factory=APISettings)
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)
    sensors: SensorSettings = field(default_factory=SensorSettings)
    comms: CommsSettings = field(default_factory=CommsSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    avatar: AvatarSettings = field(default_factory=AvatarSettings)
    telemetry: TelemetrySettings = field(default_factory=TelemetrySettings)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        """Load from JSON file (if given) then overlay env vars. File is optional."""
        data: dict = {}
        if path is not None and Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        llm = data.get("llm", {})
        memory = data.get("memory", {})
        database = data.get("database", {})
        tools = data.get("tools", {})
        security = data.get("security", {})
        environment = data.get("environment", {})
        interface = data.get("interface", {})
        logging_cfg = data.get("logging", {})
        api = data.get("api", {})
        sandbox = data.get("sandbox", {})
        sensors_cfg = data.get("sensors", {})
        comms_cfg = data.get("comms", {})
        voice_cfg = data.get("voice", {})
        avatar_cfg = data.get("avatar", {})
        telemetry_cfg = data.get("telemetry", {})
        return cls(
            llm=LLMSettings(
                provider=_env("SUNS_LLM_PROVIDER", str(llm.get("provider", "fake"))),
                model=_env("SUNS_LLM_MODEL", str(llm.get("model", "fake-echo"))),
                base_url=os.environ.get("SUNS_LLM_BASE_URL", str(llm.get("base_url", ""))),
                timeout_secs=float(os.environ.get("SUNS_LLM_TIMEOUT", str(llm.get("timeout_secs", 30.0)))),
            ),
            memory=MemorySettings(
                database_path=_env("SUNS_DB_PATH", str(memory.get("database_path", "data/sunschan.db"))),
                recall_limit=_env_int("SUNS_RECALL_LIMIT", int(memory.get("recall_limit", 8))),
            ),
            database=DatabaseSettings(
                path=_env("SUNS_DB_PATH", str(database.get("path", "data/sunschan.db"))),
            ),
            tools=ToolSettings(
                allow_sandbox_write=_env_bool("SUNS_ALLOW_SANDBOX_WRITE", bool(tools.get("allow_sandbox_write", True))),
                allow_host_shell=_env_bool("SUNS_ALLOW_HOST_SHELL", False),
                default_timeout_secs=float(os.environ.get("SUNS_TOOL_TIMEOUT", str(tools.get("default_timeout_secs", 30.0)))),
            ),
            security=SecuritySettings(
                require_approval_outside_sandbox=_env_bool(
                    "SUNS_REQUIRE_APPROVAL", bool(security.get("require_approval_outside_sandbox", True))
                ),
                audit_log_path=_env("SUNS_AUDIT_LOG", str(security.get("audit_log_path", "logs/audit.log"))),
            ),
            environment=EnvironmentSettings(
                provider=_env("SUNS_ENV_PROVIDER", str(environment.get("provider", "fake"))),
                poll_interval_secs=_env_int("SUNS_ENV_POLL", int(environment.get("poll_interval_secs", 60))),
            ),
            interface=InterfaceSettings(
                host=_env("SUNS_HOST", str(interface.get("host", "127.0.0.1"))),
                port=_env_int("SUNS_PORT", int(interface.get("port", 8765))),
            ),
            logging=LoggingSettings(
                level=_env("SUNS_LOG_LEVEL", str(logging_cfg.get("level", "INFO"))),
                log_path=_env("SUNS_LOG_PATH", str(logging_cfg.get("log_path", "logs/sunschan.log"))),
            ),
            api=APISettings(
                enabled=_env_bool("SUNS_API_ENABLED", bool(api.get("enabled", False))),
                host=_env("SUNS_API_HOST", str(api.get("host", "127.0.0.1"))),
                port=_env_int("SUNS_API_PORT", int(api.get("port", 8765))),
            ),
            sandbox=SandboxSettings(
                mode=_env("SANDBOX_MODE", str(sandbox.get("mode", "mock"))),
                network_mode=_env("SANDBOX_NETWORK", str(sandbox.get("network_mode", "isolated"))),
                template=_env("SANDBOX_TEMPLATE", str(sandbox.get("template", "debian-12-baseline"))),
                vm_vcpus=_env_int("SANDBOX_VCPUS", int(sandbox.get("vm_vcpus", 2))),
                vm_ram_mb=_env_int("SANDBOX_RAM_MB", int(sandbox.get("vm_ram_mb", 4096))),
                vm_disk_gb=_env_int("SANDBOX_DISK_GB", int(sandbox.get("vm_disk_gb", 40))),
                proxmox_configured=all(
                    os.environ.get(key, "") != ""
                    for key in ("PROXMOX_HOST", "PROXMOX_USER", "PROXMOX_TOKEN_NAME", "PROXMOX_TOKEN_VALUE")
                ),
            ),
            sensors=SensorSettings(
                enabled=_env_bool("SENSORS_ENABLED", bool(sensors_cfg.get("enabled", False))),
                poll_interval_secs=_env_int("SENSORS_POLL", int(sensors_cfg.get("poll_interval_secs", 300))),
            ),
            comms=CommsSettings(
                enabled=_env_bool("COMMS_ENABLED", bool(comms_cfg.get("enabled", False))),
                allowed_channels=tuple(comms_cfg.get("allowed_channels", ("console",))),
            ),
            voice=VoiceSettings(
                input_provider=_env("VOICE_INPUT", str(voice_cfg.get("input_provider", "none"))),
                output_provider=_env("VOICE_OUTPUT", str(voice_cfg.get("output_provider", "none"))),
            ),
            avatar=AvatarSettings(
                provider=_env("AVATAR_PROVIDER", str(avatar_cfg.get("provider", "none"))),
            ),
            telemetry=TelemetrySettings(
                enabled=_env_bool("SUNS_TELEMETRY_ENABLED", bool(telemetry_cfg.get("enabled", False))),
                source=_env("SUNS_TELEMETRY_SOURCE", str(telemetry_cfg.get("source", "none"))),
                source_path=_env("SUNS_TELEMETRY_PATH", str(telemetry_cfg.get("source_path", ""))),
                poll_interval_secs=_env_int("SUNS_TELEMETRY_POLL", int(telemetry_cfg.get("poll_interval_secs", 30))),
                importance_threshold=_env("SUNS_TELEMETRY_THRESHOLD", str(telemetry_cfg.get("importance_threshold", "normal"))),
                retention=_env_int("SUNS_TELEMETRY_RETENTION", int(telemetry_cfg.get("retention", 1000))),
                redact=_env_bool("SUNS_TELEMETRY_REDACT", bool(telemetry_cfg.get("redact", True))),
            ),
        )
