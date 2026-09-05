"""Isolated file+fetch broker. No subprocess, no host commands, no pip exec.

The broker owns disposable workspaces under a root directory. Every path is
resolved inside the workspace (``..`` escapes rejected). Network fetch is
limited to an explicit domain allowlist with size/time caps. Command and
install actions are intentionally NOT executed here; they need a container
runner plus human approval, so they raise NotImplementedError.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
import shutil
from urllib.parse import urlparse
from urllib.request import Request, urlopen


_SANDBOX_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class SandboxPolicy:
    allowed_domains: tuple[str, ...] = ()
    allowed_packages: tuple[str, ...] = ()
    max_bytes_per_file: int = 1_000_000
    max_total_bytes: int = 10_000_000
    max_files: int = 100
    fetch_timeout_secs: float = 15.0
    max_fetch_bytes: int = 5_000_000


@dataclass(frozen=True)
class SandboxFileResult:
    relpath: str
    size: int
    sha256: str


class SandboxBroker:
    def __init__(self, root: str | Path, policy: SandboxPolicy | None = None) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._policy = policy or SandboxPolicy()

    @property
    def policy(self) -> SandboxPolicy:
        return self._policy

    def create(self, sandbox_id: str) -> Path:
        workspace = self._workspace_path(sandbox_id)
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def destroy(self, sandbox_id: str) -> None:
        workspace = self._workspace_path(sandbox_id)
        if workspace.exists():
            shutil.rmtree(workspace)

    def write_text(self, sandbox_id: str, relpath: str, content: str) -> SandboxFileResult:
        data = content.encode("utf-8")
        if len(data) > self._policy.max_bytes_per_file:
            raise ValueError("file exceeds per-file limit")
        target = self._resolve(sandbox_id, relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._file_count(sandbox_id) >= self._policy.max_files and not target.exists():
            raise ValueError("sandbox file count quota exceeded")
        target.write_bytes(data)
        if self.disk_usage(sandbox_id) > self._policy.max_total_bytes:
            target.unlink(missing_ok=True)
            raise ValueError("sandbox disk quota exceeded")
        return SandboxFileResult(relpath, len(data), hashlib.sha256(data).hexdigest())

    def read_text(self, sandbox_id: str, relpath: str) -> str:
        target = self._resolve(sandbox_id, relpath)
        if not target.is_file():
            raise ValueError("file does not exist in sandbox")
        return target.read_text(encoding="utf-8")

    def list_files(self, sandbox_id: str) -> list[str]:
        workspace = self._workspace_path(sandbox_id)
        if not workspace.exists():
            return []
        return sorted(
            str(path.relative_to(workspace)).replace("\\", "/")
            for path in workspace.rglob("*")
            if path.is_file()
        )

    def disk_usage(self, sandbox_id: str) -> int:
        workspace = self._workspace_path(sandbox_id)
        if not workspace.exists():
            return 0
        return sum(path.stat().st_size for path in workspace.rglob("*") if path.is_file())

    def fetch_to_file(self, sandbox_id: str, url: str, relpath: str) -> SandboxFileResult:
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme not in {"http", "https"}:
            raise ValueError("only http/https URLs are allowed")
        if not self._domain_allowed(host.lower()):
            raise ValueError(f"domain not allowlisted: {host}")
        request = Request(url, headers={"User-Agent": "suns-chan-sandbox/0.1"})
        with urlopen(request, timeout=self._policy.fetch_timeout_secs) as response:  # noqa: S310
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > self._policy.max_fetch_bytes:
                    raise ValueError("fetch exceeds max fetch bytes")
                chunks.append(chunk)
        data = b"".join(chunks)
        if len(data) > self._policy.max_bytes_per_file:
            raise ValueError("fetched file exceeds per-file limit")
        target = self._resolve(sandbox_id, relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if self.disk_usage(sandbox_id) > self._policy.max_total_bytes:
            target.unlink(missing_ok=True)
            raise ValueError("sandbox disk quota exceeded")
        return SandboxFileResult(relpath, len(data), hashlib.sha256(data).hexdigest())

    def is_package_allowed(self, package: str) -> bool:
        name = package.strip().lower()
        if not _PACKAGE_RE.match(name):
            return False
        return name in {p.lower() for p in self._policy.allowed_packages}

    def _domain_allowed(self, host: str) -> bool:
        for domain in self._policy.allowed_domains:
            d = domain.lower()
            if host == d or host.endswith("." + d):
                return True
        return False

    def _workspace_path(self, sandbox_id: str) -> Path:
        if not _SANDBOX_ID_RE.match(sandbox_id):
            raise ValueError("invalid sandbox_id")
        return self._root / sandbox_id

    def _resolve(self, sandbox_id: str, relpath: str) -> Path:
        if not relpath.strip() or Path(relpath).is_absolute():
            raise ValueError("relpath must be a relative path")
        workspace = self._workspace_path(sandbox_id)
        target = (workspace / relpath).resolve()
        workspace_resolved = workspace.resolve()
        if target != workspace_resolved and workspace_resolved not in target.parents:
            raise ValueError("path escapes sandbox workspace")
        return target

    def _file_count(self, sandbox_id: str) -> int:
        workspace = self._workspace_path(sandbox_id)
        if not workspace.exists():
            return 0
        return sum(1 for path in workspace.rglob("*") if path.is_file())
