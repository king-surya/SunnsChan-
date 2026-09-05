"""VM sandbox models, provider protocol, and mock backend.

Containment model: the VM IS the sandbox. Inside it, freedom is total —
no command/package/filesystem allowlists exist anywhere in this module.
The hard boundary is the hypervisor/host line: providers never expose host
paths, host credentials, or infrastructure secrets to the guest, the model,
or memory. Every result is labeled with its backend mode (MOCK vs REAL) so
a mock run can never be mistaken for real infrastructure.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
from typing import Protocol


NETWORK_MODES = ("isolated", "restricted", "internet")
DEFAULT_NETWORK_MODE = "isolated"

BACKEND_MOCK = "MOCK"
BACKEND_REAL = "REAL"


@dataclass(frozen=True)
class VMResources:
    vcpus: int = 2
    ram_mb: int = 4096
    disk_gb: int = 40
    lifetime_secs: int = 3600

    def __post_init__(self) -> None:
        if self.vcpus < 1 or self.ram_mb < 512 or self.disk_gb < 5 or self.lifetime_secs < 60:
            raise ValueError("unreasonably small VM resources")


@dataclass(frozen=True)
class VMSpec:
    name: str
    template: str
    resources: VMResources = field(default_factory=VMResources)
    network_mode: str = DEFAULT_NETWORK_MODE

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("vm name must be non-empty")
        if not self.template.strip():
            raise ValueError("vm template must be non-empty")
        if self.network_mode not in NETWORK_MODES:
            raise ValueError(f"unknown network mode: {self.network_mode!r}")


@dataclass(frozen=True)
class GuestResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_secs: float
    backend: str  # MOCK | REAL — never mislabel


@dataclass(frozen=True)
class SnapshotInfo:
    name: str
    created_at: datetime


@dataclass(frozen=True)
class ArtifactRecord:
    experiment_id: str
    sandbox_id: str
    path: str
    size: int
    sha256: str
    collected_at: datetime


class SandboxProvider(Protocol):
    backend: str

    def create(self, spec: VMSpec) -> str:
        """Create/clone the VM. Returns sandbox_id."""
        ...

    def start(self, sandbox_id: str) -> None:
        ...

    def stop(self, sandbox_id: str) -> None:
        ...

    def restart(self, sandbox_id: str) -> None:
        ...

    def status(self, sandbox_id: str) -> str:
        """One of: creating | running | stopped | corrupted | absent."""
        ...

    def wait_ready(self, sandbox_id: str, timeout_secs: float = 120.0) -> None:
        ...

    def snapshot(self, sandbox_id: str, name: str) -> SnapshotInfo:
        ...

    def restore(self, sandbox_id: str, name: str) -> None:
        ...

    def execute(self, sandbox_id: str, command: str, timeout_secs: float = 120.0) -> GuestResult:
        """Run an ARBITRARY guest command. No allowlists. Guest freedom is total."""
        ...

    def upload(self, sandbox_id: str, path: str, content: bytes) -> None:
        ...

    def download(self, sandbox_id: str, path: str) -> bytes:
        ...

    def collect(self, sandbox_id: str, experiment_id: str, paths: list[str]) -> list[ArtifactRecord]:
        ...

    def destroy(self, sandbox_id: str) -> None:
        ...


@dataclass
class _MockVM:
    spec: VMSpec
    status: str = "creating"
    files: dict[str, bytes] = field(default_factory=dict)
    installed_packages: list[str] = field(default_factory=list)
    command_history: list[str] = field(default_factory=list)
    snapshots: dict[str, dict] = field(default_factory=dict)


class MockSandboxProvider:
    """Deterministic in-memory VM stand-in. Simulates guest behavior for tests.

    Accepts ARBITRARY command strings (apt/rm/systemctl/... never blocked —
    that is the point of the VM model). Nothing executes on the host.
    `rm -rf /` corrupts the mock guest to exercise recovery. Every result
    carries backend="MOCK".
    """

    backend = BACKEND_MOCK

    def __init__(self) -> None:
        self._vms: dict[str, _MockVM] = {}
        self._counter = 0

    def create(self, spec: VMSpec) -> str:
        self._counter += 1
        sandbox_id = f"mock-vm-{self._counter}"
        self._vms[sandbox_id] = _MockVM(spec=spec)
        return sandbox_id

    def _require(self, sandbox_id: str) -> _MockVM:
        try:
            return self._vms[sandbox_id]
        except KeyError:
            raise ValueError(f"unknown sandbox: {sandbox_id}") from None

    def start(self, sandbox_id: str) -> None:
        vm = self._require(sandbox_id)
        if vm.status == "corrupted":
            raise RuntimeError("vm is corrupted; restore or recreate first")
        vm.status = "running"

    def stop(self, sandbox_id: str) -> None:
        self._require(sandbox_id).status = "stopped"

    def restart(self, sandbox_id: str) -> None:
        vm = self._require(sandbox_id)
        if vm.status == "corrupted":
            raise RuntimeError("vm is corrupted; restore or recreate first")
        vm.status = "running"

    def status(self, sandbox_id: str) -> str:
        return self._require(sandbox_id).status

    def wait_ready(self, sandbox_id: str, timeout_secs: float = 120.0) -> None:
        if self.status(sandbox_id) != "running":
            raise RuntimeError("vm is not running")

    def snapshot(self, sandbox_id: str, name: str) -> SnapshotInfo:
        vm = self._require(sandbox_id)
        if not name.strip():
            raise ValueError("snapshot name must be non-empty")
        vm.snapshots[name] = {
            "files": copy.deepcopy(vm.files),
            "installed_packages": list(vm.installed_packages),
            "status": vm.status,
        }
        return SnapshotInfo(name, datetime.now(UTC))

    def restore(self, sandbox_id: str, name: str) -> None:
        vm = self._require(sandbox_id)
        try:
            saved = vm.snapshots[name]
        except KeyError:
            raise ValueError(f"unknown snapshot: {name}") from None
        vm.files = copy.deepcopy(saved["files"])
        vm.installed_packages = list(saved["installed_packages"])
        vm.status = "running" if saved["status"] in {"running", "corrupted"} else saved["status"]

    def execute(self, sandbox_id: str, command: str, timeout_secs: float = 120.0) -> GuestResult:
        vm = self._require(sandbox_id)
        if not command.strip():
            raise ValueError("command must be non-empty")
        if vm.status != "running":
            raise RuntimeError(f"vm is not running (status={vm.status})")
        vm.command_history.append(command)
        text = command.strip()
        lowered = text.lower()
        if lowered in {"reboot", "sudo reboot", "shutdown -r now"}:
            vm.status = "running"  # reboot bounce; stays usable
            return GuestResult(command, 0, "", "", 0.0, BACKEND_MOCK)
        if lowered in {"rm -rf /", "rm -rf /*", ":(){ :|:& };:"}:
            vm.status = "corrupted"
            return GuestResult(command, 0, "", "guest destroyed itself", 0.0, BACKEND_MOCK)
        if lowered.startswith("echo "):
            return GuestResult(command, 0, text[5:] + "\n", "", 0.0, BACKEND_MOCK)
        if lowered.startswith("apt install ") or lowered.startswith("apt-get install "):
            package = text.split()[-1]
            vm.installed_packages.append(package)
            return GuestResult(command, 0, f"installed {package} (mock)\n", "", 0.0, BACKEND_MOCK)
        if lowered.startswith("pip install "):
            package = text.split()[-1]
            vm.installed_packages.append(package)
            return GuestResult(command, 0, f"installed {package} (mock)\n", "", 0.0, BACKEND_MOCK)
        if lowered.startswith("write "):
            _, _, rest = text.partition(" ")
            path, _, content = rest.partition(" ")
            vm.files[path] = content.encode()
            return GuestResult(command, 0, f"wrote {path} (mock)\n", "", 0.0, BACKEND_MOCK)
        # Full guest freedom: anything else is accepted and recorded, simulated as success.
        return GuestResult(command, 0, "", "", 0.0, BACKEND_MOCK)

    def upload(self, sandbox_id: str, path: str, content: bytes) -> None:
        vm = self._require(sandbox_id)
        if not path.strip():
            raise ValueError("path must be non-empty")
        vm.files[path] = bytes(content)

    def download(self, sandbox_id: str, path: str) -> bytes:
        vm = self._require(sandbox_id)
        try:
            return vm.files[path]
        except KeyError:
            raise ValueError(f"guest file not found: {path}") from None

    def collect(self, sandbox_id: str, experiment_id: str, paths: list[str]) -> list[ArtifactRecord]:
        vm = self._require(sandbox_id)
        now = datetime.now(UTC)
        records: list[ArtifactRecord] = []
        for path in paths:
            try:
                content = vm.files[path]
            except KeyError:
                raise ValueError(f"guest file not found: {path}") from None
            records.append(
                ArtifactRecord(experiment_id, sandbox_id, path, len(content),
                               hashlib.sha256(content).hexdigest(), now)
            )
        return records

    def destroy(self, sandbox_id: str) -> None:
        if sandbox_id in self._vms:
            del self._vms[sandbox_id]
