"""
ARBITER phase: resolve disputed findings after max-loops.

Runs the ARBITER model over the unresolved disputes and parses a final
APPROVE/REJECT verdict, per-finding decisions, epistemic distribution, minimal
patch, and any conditions that must hold for approval. Prefers a JSON response;
falls back to scanning a ``VERDICT:`` line and bullet points.
"""
import json
import re
from typing import Any

from adversarial_common import jsonio

__all__ = ["run_arbiter"]

_VALID_VERDICTS = {"APPROVE", "REJECT"}


def _empty_epistemic_distribution() -> dict:
    return {
        "confidence": {"high": 0, "medium": 0, "low": 0},
        "basis": {"spec": 0, "code": 0, "inference": 0, "external": 0},
    }


def _parse(text: str) -> dict:
    """Extract the complete judge schema from arbiter output.

    JSON wins if present; otherwise a ``VERDICT: <X>`` line and ``-``/``*``
    bullets are scanned. Defaults to REJECT when nothing parses (safe — never
    auto-approve), while retaining stable empty values for the rich fields.
    """
    result = {
        "verdict": "REJECT",
        "conditions": [],
        "decisions": [],
        "epistemic_distribution": _empty_epistemic_distribution(),
        "minimal_patch": "",
        "summary": "",
    }
    if text:
        payload = jsonio.parse_json_output(text)
        if isinstance(payload, dict):
            token = str(payload.get("verdict", "")).upper()
            if token in _VALID_VERDICTS:
                result["verdict"] = token
            conds = payload.get("conditions")
            if isinstance(conds, list):
                result["conditions"] = [str(c) for c in conds]
            if isinstance(payload.get("decisions"), list):
                result["decisions"] = payload["decisions"]
            if isinstance(payload.get("epistemic_distribution"), dict):
                result["epistemic_distribution"] = payload["epistemic_distribution"]
            if isinstance(payload.get("minimal_patch"), str):
                result["minimal_patch"] = payload["minimal_patch"]
            if isinstance(payload.get("summary"), str):
                result["summary"] = payload["summary"]
            return result
        match = re.search(r"VERDICT\s*[:=]\s*([A-Z_]+)", text.upper())
        if match:
            token = match.group(1)
            if token in ("APPROVED", "APPROVE"):
                result["verdict"] = "APPROVE"
            elif token in ("REJECT", "REJECTED"):
                result["verdict"] = "REJECT"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped[:1] in ("-", "*") and len(stripped) > 2:
                result["conditions"].append(stripped.lstrip("-* ").strip())
    return result


def run_arbiter(
    findings: list,
    dev_cmd: str,
    review_cmd: str,
    arbiter_cmd: str,
    providers: Any,
) -> dict:
    """Run the ARBITER model with unresolved disputes.

    Returns the complete judge schema plus ``phase`` and ``exit_code``.
    """
    try:
        prompt = (
            "You are the arbiter. Resolve the following disputed findings and "
            "issue a final APPROVE or REJECT verdict. Output ONLY valid JSON "
            "matching this schema: "
            "{\"verdict\": \"APPROVE|REJECT\", \"conditions\": [\"...\"], "
            "\"decisions\": [{\"id\": \"A1\", "
            "\"outcome\": \"uphold|overturn|conditional\", "
            "\"evidence\": \"...\", \"confidence\": \"high|medium|low\", "
            "\"basis\": \"spec|code|inference|external\"}], "
            "\"epistemic_distribution\": {\"confidence\": {\"high\": 0, "
            "\"medium\": 0, \"low\": 0}, \"basis\": {\"spec\": 0, "
            "\"code\": 0, \"inference\": 0, \"external\": 0}}, "
            "\"minimal_patch\": \"\", \"summary\": \"...\"}.\n\n"
            f"Disputed findings:\n```json\n{json.dumps(findings, indent=2)}\n```"
        )
        stdout, stderr, code = providers.run_cmd(
            arbiter_cmd, stdin_text=prompt, role="judge",
        )
        if code != 0:
            return {
                "phase": "arbiter",
                "exit_code": 1,
                "error": f"ARBITER exited {code}: {(stderr or '')[:200]}",
                "stdout": stdout,
            }
        parsed = _parse(stdout)
        return {
            "phase": "arbiter",
            "exit_code": 0,
            **parsed,
            "stdout": stdout,
        }
    except Exception as exc:
        return {"phase": "arbiter", "exit_code": 1, "error": str(exc)}
