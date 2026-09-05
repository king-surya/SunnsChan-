"""Proxmox VE backend for the VM sandbox.

Speaks the Proxmox API2 JSON API over stdlib urllib (no new dependencies).
Guest communication uses the QEMU guest agent channel — no SSH keys, no host
mounts. Credentials live ONLY in ProxmoxCredentials (built from environment);
they are attached to infrastructure HTTP headers and never appear in guest
payloads, logs, prompts, memory, or artifacts. Use redacted() for anything
observable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
import ssl
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .vm_sandbox import (
    BACKEND_REAL,
    ArtifactRecord,
    GuestResult,
    SnapshotInfo,
    VMSpec,
)


@dataclass(frozen=True)
class ProxmoxCredentials:
    host: str  # e.g. "proxmox.lan:8006"
    user: str  # e.g. "suns@pve"
    token_name: str
    token_value: str
    verify_tls: bool = True
    node: str = "pve"

    @classmethod
    def from_env(cls) -> "ProxmoxCredentials":
        host = os.environ.get("PROXMOX_HOST", "")
        user = os.environ.get("PROXMOX_USER", "")
        token_name = os.environ.get("PROXMOX_TOKEN_NAME", "")
        token_value = os.environ.get("PROXMOX_TOKEN_VALUE", "")
        if not all([host, user, token_name, token_value]):
            raise ValueError(
                "missing Proxmox credentials: set PROXMOX_HOST, PROXMOX_USER,"
                " PROXMOX_TOKEN_NAME, PROXMOX_TOKEN_VALUE"
            )
        return cls(
            host=host,
            user=user,
            token_name=token_name,
            token_value=token_value,
            verify_tls=os.environ.get("PROXMOX_INSECURE", "0") != "1",
            node=os.environ.get("PROXMOX_NODE", "pve"),
        )

    def redacted(self) -> dict[str, str]:
        return {"host": self.host, "user": self.user, "token_name": self.token_name, "node": self.node}


def _base64_decode(data: str) -> str:
    import base64

    return base64.b64decode(data).decode("utf-8", errors="replace")


class ProxmoxSandboxProvider:
    """REAL backend. Every GuestResult carries backend="REAL". Never used in unit tests."""

    backend = BACKEND_REAL

    def __init__(
        self,
        credentials: ProxmoxCredentials,
        *,
        timeout_secs: float = 30.0,
        max_snapshots: int = 5,
    ) -> None:
        self._creds = credentials
        self._timeout = timeout_secs
        self._max_snapshots = max_snapshots
        self._vmids: dict[str, int] = {}  # sandbox_id -> vmid

    # ---- low-level API ----

    def _api(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"https://{self._creds.host}/api2/json{path}"
        data = urlencode(payload or {}).encode() if payload else None
        request = Request(
            url,
            data=data,
            method=method,
            headers={"Authorization": f"PVEAPIToken={self._creds.user}!{self._creds.token_name}={self._creds.token_value}"},
        )
        context = None if self._creds.verify_tls else ssl._create_unverified_context()
        try:
            with urlopen(request, timeout=self._timeout, context=context) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (URLError, OSError) as exc:
            raise RuntimeError(f"proxmox unreachable at {self._creds.host}: {exc}") from exc
        except ValueError as exc:
            raise RuntimeError(f"proxmox returned invalid JSON: {exc}") from exc
        return body.get("data", body)

    def _vmid(self, sandbox_id: str) -> int:
        try:
            return self._vmids[sandbox_id]
        except KeyError:
            raise ValueError(f"unknown sandbox: {sandbox_id}") from None

    # ---- lifecycle ----

    def create(self, spec: VMSpec) -> str:
        template_id = int(os.environ.get("PROXMOX_TEMPLATE_ID", "9000"))
        data = self._api("GET", "/cluster/nextid")
        vmid = int(data if isinstance(data, int) else data.get("data", data))
        sandbox_id = f"pve-{vmid}"
        self._api("POST", f"/nodes/{self._creds.node}/qemu/{template_id}/clone",
                  {"newid": vmid, "name": spec.name, "full": 1})
        bridge = {"isolated": "vmbr1", "restricted": "vmbr2", "internet": "vmbr0"}.get(
            spec.network_mode, "vmbr1")
        self._api("PUT", f"/nodes/{self._creds.node}/qemu/{vmid}/config", {
            "cores": spec.resources.vcpus,
            "memory": spec.resources.ram_mb,
            "net0": f"virtio,bridge={bridge}",
        })
        self._vmids[sandbox_id] = vmid
        return sandbox_id

    def start(self, sandbox_id: str) -> None:
        self._api("POST", f"/nodes/{self._creds.node}/qemu/{self._vmid(sandbox_id)}/status/start")

    def stop(self, sandbox_id: str) -> None:
        self._api("POST", f"/nodes/{self._creds.node}/qemu/{self._vmid(sandbox_id)}/status/stop")

    def restart(self, sandbox_id: str) -> None:
        vmid = self._vmid(sandbox_id)
        self._api("POST", f"/nodes/{self._creds.node}/qemu/{vmid}/status/reboot")

    def status(self, sandbox_id: str) -> str:
        data = self._api("GET", f"/nodes/{self._creds.node}/qemu/{self._vmid(sandbox_id)}/status/current")
        raw = data.get("status", "unknown") if isinstance(data, dict) else "unknown"
        mapping = {"running": "running", "stopped": "stopped", "paused": "stopped"}
        return mapping.get(raw, "absent" if raw == "unknown" else "corrupted")

    def wait_ready(self, sandbox_id: str, timeout_secs: float = 180.0) -> None:
        vmid = self._vmid(sandbox_id)
        deadline = time.monotonic() + timeout_secs
        while time.monotonic() < deadline:
            try:
                self._api("POST", f"/nodes/{self._creds.node}/qemu/{vmid}/agent/ping")
                return
            except RuntimeError:
                time.sleep(5.0)
        raise RuntimeError(f"guest agent not ready after {timeout_secs}s")

    # ---- snapshots ----

    def snapshot(self, sandbox_id: str, name: str) -> SnapshotInfo:
        if not name.strip():
            raise ValueError("snapshot name must be non-empty")
        vmid = self._vmid(sandbox_id)
        data = self._api("GET", f"/nodes/{self._creds.node}/qemu/{vmid}/snapshot")
        existing = [s.get("name") for s in (data if isinstance(data, list) else []) if isinstance(s, dict)]
        while len(existing) >= self._max_snapshots and existing:
            oldest = existing.pop(0)
            self._api("DELETE", f"/nodes/{self._creds.node}/qemu/{vmid}/snapshot/{oldest}")
        self._api("PUT", f"/nodes/{self._creds.node}/qemu/{vmid}/snapshot", {"snapname": name})
        return SnapshotInfo(name, datetime.now(UTC))

    def restore(self, sandbox_id: str, name: str) -> None:
        self._api("POST", f"/nodes/{self._creds.node}/qemu/{self._vmid(sandbox_id)}/snapshot/{name}/rollback")

    # ---- guest channel (qemu-guest-agent; runs INSIDE the sandbox) ----

    def build_guest_command(self, command: str) -> dict:
        """Guest payload constructor, kept separate so tests can prove it
        carries no credentials. The guest shell interprets the command —
        full freedom inside the VM is intentional."""
        if not command.strip():
            raise ValueError("command must be non-empty")
        return {"command": command}

    def execute(self, sandbox_id: str, command: str, timeout_secs: float = 120.0) -> GuestResult:
        started = time.monotonic()
        vmid = self._vmid(sandbox_id)
        payload = self.build_guest_command(command)
        data = self._api("POST", f"/nodes/{self._creds.node}/qemu/{vmid}/agent/exec", payload)
        pid = data.get("pid") if isinstance(data, dict) else None
        if pid is None:
            raise RuntimeError("guest agent did not return a pid")
        deadline = started + timeout_secs
        while True:
            state = self._api("GET", f"/nodes/{self._creds.node}/qemu/{vmid}/agent/exec-status?pid={pid}")
            if not isinstance(state, dict) or not state.get("exited"):
                if time.monotonic() > deadline:
                    raise RuntimeError(f"guest command timed out after {timeout_secs}s")
                time.sleep(2.0)
                continue
            stdout = _base64_decode(state.get("out-data", "") or "")
            stderr = _base64_decode(state.get("err-data", "") or "")
            return GuestResult(command, int(state.get("exitcode", 1)), stdout, stderr,
                               round(time.monotonic() - started, 3), BACKEND_REAL)

    def upload(self, sandbox_id: str, path: str, content: bytes) -> None:
        import base64

        vmid = self._vmid(sandbox_id)
        encoded = base64.b64encode(content).decode()
        # Write via the guest agent: file-write + content is guest-side data only.
        self.execute(sandbox_id, f"python3 -c \"import base64,pathlib; pathlib.Path({path!r}).write_bytes(base64.b64decode({encoded!r}))\"")

    def download(self, sandbox_id: str, path: str) -> bytes:
        import base64

        result = self.execute(
            sandbox_id,
            f"python3 -c \"import base64,pathlib,sys; sys.stdout.write(base64.b64encode(pathlib.Path({path!r}).read_bytes()).decode())\"",
        )
        if result.exit_code != 0:
            raise ValueError(f"guest file not found: {path}")
        return base64.b64decode(result.stdout.strip())

    def collect(self, sandbox_id: str, experiment_id: str, paths: list[str]) -> list[ArtifactRecord]:
        import hashlib

        now = datetime.now(UTC)
        records = []
        for path in paths:
            content = self.download(sandbox_id, path)
            records.append(ArtifactRecord(experiment_id, sandbox_id, path, len(content),
                                          hashlib.sha256(content).hexdigest(), now))
        return records

    def destroy(self, sandbox_id: str) -> None:
        vmid = self._vmid(sandbox_id)
        try:
            self._api("POST", f"/nodes/{self._creds.node}/qemu/{vmid}/status/stop")
        except RuntimeError:
            pass
        self._api("DELETE", f"/nodes/{self._creds.node}/qemu/{vmid}")
        del self._vmids[sandbox_id]

    # ---- introspection for tests/audit (redacted) ----

    def describe(self) -> dict:
        return {"backend": BACKEND_REAL, "credentials": self._creds.redacted(),
                "known_sandboxes": sorted(self._vmids)}
