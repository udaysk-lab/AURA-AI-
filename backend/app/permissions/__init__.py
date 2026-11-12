"""Permissions: what AURA may do, and who decides (spec 10-permissions.md).

Submodules: `catalogue` (every tool, its system and kind), `match` and `shellparse`
(rule matching), `floors` (what nobody can override), `rules` (storage and
lookup), `events` (the hash-chained log), `triggers` (limits per trigger),
`legacy` (the 1.0 mapping: its record in the user's log), `reviewer`
(the auto-mode safety reviewer), `gate` (the single decision function) and
`approvals` (parked approvals: queueing, answering, expiry).

The public names the task loop uses are available here (`permissions.decide`,
`GateContext`, `Decision`, `is_read_only`, `expiry_for`, `mode_label`,
`build_context`); they load lazily so any submodule can still be imported on its
own without pulling in the database layer.
"""

from __future__ import annotations

_GATE_NAMES = (
    "decide", "decide_approved", "console_decision", "ui_decision", "build_context", "GateContext",
    "Decision", "RuleDraft", "is_read_only", "expiry_for", "mode_label", "effective_mode", "args_digest",
    "is_sealed", "proposed_rule_for",
)

__all__ = list(_GATE_NAMES)


def __getattr__(name: str):
    if name in _GATE_NAMES:
        from app.permissions import gate

        return getattr(gate, name)
    raise AttributeError(f"module 'app.permissions' has no attribute {name!r}")
