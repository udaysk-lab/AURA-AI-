"""Local device control.

This package is what turns AURA from a hosted assistant, whose tools only ever
touched its own database, into one that operates the computer it runs on: it
runs shell commands, reads and writes files, launches apps, drives the screen,
and reports on the system.

The shape is deliberate: a trusted control plane (the agent loop and its
autonomy gate) in front of an execution surface (this package) that assumes
nothing and is governed by deterministic policy. All of it is built on AURA's
own stack and its existing four-tier trust model, so a device action is gated
by exactly the machinery that already gates sending an email.

Two invariants hold everywhere in here:

1.  **Nothing runs unless `settings.device_control_enabled` is true.** Every
    entry point checks it. On a server that flag is off, so none of this is
    reachable and the device tools never reach the model.
2.  **The filesystem is fenced.** Every path is resolved to an absolute real
    path and checked against the configured allowed roots and protected paths
    before it is touched, so the agent cannot escape them with `..`, a symlink,
    or a `~`.

Risk beyond that — whether a given command or click needs your say-so — is
decided by the autonomy tier and the exec policy, in the agent loop, exactly
like every other tool. See `app/agent/autonomy.py` and `app/device/policy.py`.
"""

from __future__ import annotations

from app.device.runtime import DeviceUnavailable, capabilities, require_enabled

__all__ = ["DeviceUnavailable", "capabilities", "require_enabled"]
