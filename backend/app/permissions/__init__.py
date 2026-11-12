"""Permissions: what AURA may do, and who decides (spec 10-permissions.md).

This package grows over several work packages. So far: the tool catalogue
(`catalogue`), the rule matchers (`match`) and the shell parser (`shellparse`).
Nothing here is consulted when a tool runs yet; the gate that does so arrives
with `permissions.gate`. Keep this module free of imports so any submodule can
be loaded on its own.
"""
