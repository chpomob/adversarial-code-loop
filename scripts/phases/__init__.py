"""Phase modules for the v4 adversarial loop.

Each module implements exactly one phase of the pipeline and exposes a single
public ``run_*`` (or ``setup_*``/``finalize_*``) function. Shared utilities are
imported from :mod:`adversarial_common`; ``providers`` and ``jsonio`` are passed
in by the orchestrator to keep the modules free of globals.
"""
