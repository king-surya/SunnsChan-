"""Reproducible sandbox VM baseline: template spec + cloud-init generator.

The template is a general-purpose Linux lab image: shell, Python, toolchain,
package manager, git, qemu-guest-agent (the control channel), and logging.
The package list stays configurable; the generated user-data is deterministic
for a given spec so the baseline is reproducible. No credentials are embedded
here — access is provisioned per-experiment by the controller.
"""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_PACKAGES = (
    "python3",
    "python3-pip",
    "python3-venv",
    "git",
    "build-essential",
    "curl",
    "wget",
    "vim",
    "htop",
    "strace",
    "qemu-guest-agent",
    "openssh-server",
)


@dataclass(frozen=True)
class VMTemplateSpec:
    distro: str = "debian-12"
    packages: tuple[str, ...] = DEFAULT_PACKAGES
    lab_user: str = "suns"
    enable_guest_agent: bool = True
    extra_runcmd: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.distro.strip():
            raise ValueError("distro must be non-empty")
        if not self.packages:
            raise ValueError("package list must be non-empty")
        if not self.lab_user.strip() or " " in self.lab_user:
            raise ValueError("lab_user must be a single username")


def cloud_init_user_data(spec: VMTemplateSpec) -> str:
    """Deterministic cloud-config YAML for the baseline image."""
    packages = "\n".join(f"  - {package}" for package in spec.packages)
    runcmd = ["  - systemctl enable --now qemu-guest-agent"] if spec.enable_guest_agent else []
    runcmd += [f"  - {command}" for command in spec.extra_runcmd]
    commands = "\n".join(runcmd) if runcmd else "  - echo baseline-ready"
    return (
        "#cloud-config\n"
        f"hostname: suns-sandbox\n"
        f"manage_etc_hosts: true\n"
        f"users:\n"
        f"  - name: {spec.lab_user}\n"
        f"    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        f"    shell: /bin/bash\n"
        f"packages:\n{packages}\n"
        f"runcmd:\n{commands}\n"
    )


@dataclass
class TemplateBuild:
    """Record of a built template image (Proxmox template VM)."""

    template_name: str
    template_id: int
    spec: VMTemplateSpec = field(default_factory=VMTemplateSpec)

    def describe(self) -> dict:
        return {
            "template_name": self.template_name,
            "template_id": self.template_id,
            "distro": self.spec.distro,
            "packages": list(self.spec.packages),
        }
