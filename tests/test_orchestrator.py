"""Provider-registry wiring tests for the code-loop orchestrator."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

_SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts import adversarial_loop as orch
from scripts.phases import (
    phase_arbiter,
    phase_build,
    phase_fix,
    phase_review,
    phase_verify,
)

from adversarial_common import (
    NoProviderAvailable,
    ProviderConfig,
    ProviderEntry,
    RunResult,
)


def _config():
    entry = ProviderEntry(alias="primary", command="echo provider")
    return ProviderConfig(
        roles={
            "dev": (entry,),
            "review": (entry,),
            "verify": (entry,),
            "arbiter": (entry,),
        },
        quota_cmd="quota-check",
    )


def test_parser_exposes_registry_and_force_options():
    args = orch.build_parser().parse_args([
        "--spec", "spec.md",
        "--provider-config", "providers.yaml",
        "--force",
        "--force-provider", "dev:primary",
        "--force-provider", "verify:fallback",
    ])

    assert args.provider_config == "providers.yaml"
    assert args.force is True
    assert args.force_provider == [
        ("dev", "primary"), ("verify", "fallback")
    ]


def test_main_loads_config_and_constructs_one_resolver(
        tmp_path, monkeypatch):
    seen = {}
    config = _config()

    def load(path):
        seen["path"] = path
        return config

    class Resolver:
        def __init__(self, loaded, quota_cmd):
            seen["resolver_args"] = (loaded, quota_cmd)

    monkeypatch.setattr(orch, "load_provider_config", load)
    monkeypatch.setattr(orch, "QuotaResolver", Resolver)

    code = orch.main([
        "--spec", str(tmp_path / "missing.md"),
        "--workdir", str(tmp_path / "missing-workdir"),
        "--provider-config", str(tmp_path / "providers.yaml"),
    ])

    assert code == orch.EXIT_USAGE
    assert seen["path"] == str(tmp_path / "providers.yaml")
    assert seen["resolver_args"] == (config, config.quota_cmd)


def test_all_phases_route_to_expected_roles(tmp_path, monkeypatch):
    calls = []

    def run_phase_cmd(**kwargs):
        calls.append(kwargs)
        phase = kwargs["phase_name"]
        if phase == "review":
            stdout = json.dumps({"findings": [], "verdict": "APPROVE"})
        elif phase == "verify":
            stdout = json.dumps({
                "results": [{
                    "id": "A1", "status": "resolved", "evidence": "fixed",
                    "confidence": "high", "basis": "code",
                }],
                "epistemic_distribution": {
                    "confidence": {"high": 1, "medium": 0, "low": 0},
                    "basis": {
                        "spec": 0, "code": 1, "inference": 0, "external": 0,
                    },
                },
                "verdict": "APPROVE",
            })
        elif phase == "arbiter":
            stdout = json.dumps({"verdict": "APPROVE", "conditions": []})
        else:
            stdout = "built"
        return RunResult((stdout, "", 0))

    monkeypatch.setattr(phase_build, "run_phase_cmd", run_phase_cmd)
    monkeypatch.setattr(phase_fix, "run_phase_cmd", run_phase_cmd)
    monkeypatch.setattr(phase_review, "run_phase_cmd", run_phase_cmd)
    monkeypatch.setattr(phase_verify, "run_phase_cmd", run_phase_cmd)
    monkeypatch.setattr(phase_arbiter, "run_phase_cmd", run_phase_cmd)
    monkeypatch.setattr(phase_build.gitops, "commit_all", lambda *_args: None)
    monkeypatch.setattr(phase_build.gitops, "head_sha", lambda *_args: "abc")

    resolver = object()
    assert phase_build.run_build(
        "spec", "", str(tmp_path), 10, "demo", resolver
    )["exit_code"] == 0
    assert phase_fix.run_fix(
        [{"id": "A1"}], "", str(tmp_path), 10, "demo", 1, resolver
    )["exit_code"] == 0
    assert phase_review.run_review(
        "diff", "", resolver, orch.jsonio, workdir=str(tmp_path)
    )["exit_code"] == 0
    assert phase_verify.run_verify(
        [{"id": "A1"}], "diff", "", resolver, orch.jsonio,
        workdir=str(tmp_path),
    )["exit_code"] == 0
    assert phase_arbiter.run_arbiter(
        [{"id": "A1"}], "", "", "", resolver, workdir=str(tmp_path)
    )["exit_code"] == 0

    assert [(call["phase_name"], call["role"]) for call in calls] == [
        ("build", "dev"), ("fix", "dev"), ("review", "review"),
        ("verify", "verify"), ("arbiter", "arbiter"),
    ]


def test_explicit_dev_command_bypasses_resolver(tmp_path, monkeypatch):
    class ResolverMustNotRun:
        def resolve(self, *_args, **_kwargs):
            raise AssertionError("quota resolver was consulted")

    monkeypatch.setattr(phase_build.gitops, "commit_all", lambda *_args: None)
    monkeypatch.setattr(phase_build.gitops, "head_sha", lambda *_args: "abc")

    result = phase_build.run_build(
        "spec",
        "unused legacy command",
        str(tmp_path),
        10,
        "demo",
        ResolverMustNotRun(),
        explicit_cmd="python3 -c 'print(\"custom\")'",
    )

    assert result["exit_code"] == 0
    assert "custom" in result["stdout"]
    assert result["provider_history"] == []


def test_force_modes_are_selected_per_role():
    args = SimpleNamespace(
        force=True,
        _force_providers={"dev": "fallback", "verify": "verifier-two"},
    )

    assert orch._provider_call_args(args, "dev", None) == {
        "explicit_cmd": None,
        "force": True,
        "force_provider": "fallback",
    }
    assert orch._provider_call_args(args, "review", None)["force_provider"] is None
    assert orch._provider_call_args(args, "verify", "echo explicit") == {
        "explicit_cmd": "echo explicit",
        "force": True,
        "force_provider": "verifier-two",
    }


def test_no_provider_available_exits_three_with_snapshots(
        tmp_path, monkeypatch, capsys):
    spec = tmp_path / "spec.md"
    spec.write_text("A sufficiently detailed specification for provider testing.")

    monkeypatch.setattr(orch, "load_provider_config", lambda _path: None)
    monkeypatch.setattr(orch.gitops, "ensure_git_available", lambda: (True, ""))
    monkeypatch.setattr(orch, "resolve_role_cmd", lambda *_args: "echo legacy")
    monkeypatch.setattr(orch, "_restore", lambda *_args: None)

    def fail(*_args, **_kwargs):
        raise NoProviderAvailable(
            "dev",
            {"one": {"used_pct": 100}, "two": {"status": 429}},
            {"one": "rate limited", "two": "rate limited"},
        )

    monkeypatch.setattr(orch, "_pipeline", fail)

    code = orch.main([
        "--spec", str(spec), "--workdir", str(tmp_path),
        "--out", str(tmp_path / "out"),
    ])

    assert code == 3
    stderr = capsys.readouterr().err
    assert "no provider available" in stderr
    assert "one" in stderr and "used_pct" in stderr
    assert "two" in stderr and "429" in stderr


def test_finish_writes_collected_provider_history(tmp_path, monkeypatch):
    decision = {
        "phase": "build", "alias": "primary", "quota_state": "OK",
        "fallback": False, "forced": False, "reason": "eligible",
        "raw_snapshot": {"primary": {"used_pct": 10}},
    }
    state = {
        "parent_branch": "main",
        "branch": "loop/demo/1",
        "completed": [],
        "provider_history": [decision],
        "findings": [],
    }
    args = SimpleNamespace(no_merge=True)
    monkeypatch.setattr(
        orch.phase_git,
        "finalize_git",
        lambda *_args, **_kwargs: {"exit_code": 0, "merged": False},
    )

    code = orch._finish(
        args, str(tmp_path), "demo", tmp_path, state, "APPROVED"
    )

    assert code == orch.EXIT_APPROVED
    payload = json.loads((tmp_path / "final.json").read_text())
    assert payload["provider_history"] == [decision]
