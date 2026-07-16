#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical v4 orchestrator.

``adversarial_loop_v4`` is the sole implementation. Importers of the
historical ``adversarial_loop`` module receive that module directly so
monkeypatching and private helper imports continue to behave exactly as they
did before the implementations were consolidated.  This includes the provider
registry CLI and its process-scoped quota resolver.
"""
from importlib import import_module
import sys


_MODULE_NAME = (
    f"{__package__}.adversarial_loop_v4"
    if __package__
    else "adversarial_loop_v4"
)
_implementation = import_module(_MODULE_NAME)


if __name__ == "__main__":
    raise SystemExit(_implementation.main())

# A module-level ``from ... import *`` would omit v4's private helpers and
# would make monkeypatches affect this wrapper instead of the implementation.
# Aliasing the module preserves the complete historical import contract while
# keeping exactly one copy of the orchestrator logic.
sys.modules[__name__] = _implementation
