"""v4 adversarial-loop scripts.

Bootstraps the sibling ``adversarial-common`` skill onto ``sys.path`` so the
phase modules under :mod:`scripts.phases` can ``import adversarial_common``.
Importing any ``scripts.*`` submodule runs this file first.
"""
import os
import sys

_COMMON = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "adversarial-common")
)
if os.path.isdir(_COMMON) and _COMMON not in sys.path:
    sys.path.insert(0, _COMMON)
