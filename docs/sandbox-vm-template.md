# Sandbox VM template design

The sandbox is a real, dedicated Linux VM. The template is a reproducible
baseline generated from `VMTemplateSpec` (`src/suns_chan/vm_template.py`).

## Baseline contents

- Debian 12 (configurable via `distro`)
- shell (bash), Python 3 + pip + venv
- compiler toolchain (`build-essential`)
- package manager (apt; guest may use pip/npm/cargo freely)
- Git, curl/wget, vim, htop, strace
- `qemu-guest-agent` — the control channel (exec, file transfer, status)
- `openssh-server` installed but NOT provisioned with keys by the template;
  per-experiment access, if ever needed, is the controller's decision
- `suns` lab user with passwordless sudo (root-equivalent inside the guest;
  this is intentional — the VM is the containment)

## Provisioning

`cloud_init_user_data(spec)` renders deterministic cloud-config YAML for a
given spec: same spec → same user-data, byte for byte. Build the Proxmox
template VM once (install packages, enable the guest agent, convert to
template), record its VMID in `PROXMOX_TEMPLATE_ID`, and clone per
experiment.

## Guest freedom (intentional)

No command, package, or filesystem restrictions exist in the guest. apt/pip/
systemctl/rm, kernel tweaks, service management, and even deliberate guest
destruction are legitimate experiments. Recovery is snapshot-based
(`baseline` snapshot per experiment; restore or recreate on corruption).

## What the template must NOT contain

- Proxmox/API credentials
- host SSH keys or host mounts
- production network credentials
- any path back into the host or other VMs

Network attachment (`vmbr1` isolated / `vmbr2` restricted / `vmbr0` internet)
is assigned at clone time by the provider from the experiment's network mode,
never from inside the guest.
