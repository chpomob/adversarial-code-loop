"""
ARBITER phase: resolve disputed findings after max-loops.

Runs the ARBITER model over the unresolved disputes and parses a final
APPROVE/REJECT verdict plus any conditions that must hold for approval. Prefers
a JSON response; falls back to scanning a ``VERDICT:`` line and bullet points.
"""
import json
import re
from typing import Any

__all__ = ["run_arbiter"]

_VALID_VERDICTS = {"APPROVE", "REJECT"}


def _parse(text: str):
    """Extract ``(verdict, conditions)`` from arbiter output.

    JSON wins if present (``{"verdict": "APPROVE|REJECT", "conditions": [...]}``);
    otherwise a ``VERDICT: <X>`` line and ``-``/``*`` bullets are scanned.
    Defaults to REJECT when nothing parses (safe — never auto-approve).
    """
    verdict = "REJECT"
    conditions: list = []
    if text:
        # JSON first
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            token = str(payload.get("verdict", "")).upper()
            if token in _VALID_VERDICTS:
                verdict = token
            conds = payload.get("conditions")
            if isinstance(conds, list):
                conditions = [str(c) for c in conds]
            return verdict, conditions
        # fallback scan
        match = re.search(r"VERDICT\s*[:=]\s*([A-Z_]+)", text.upper())
        if match:
            token = match.group(1)
            if token in ("APPROVED", "APPROVE"):
                verdict = "APPROVE"
            elif token in ("REJECT", "REJECTED"):
                verdict = "REJECT"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped[:1] in ("-", "*") and len(stripped) > 2:
                conditions.append(stripped.lstrip("-* ").strip())
    return verdict, conditions


def run_arbiter(
    findings: list,
    dev_cmd: str,
    review_cmd: str,
    arbiter_cmd: str,
    providers: Any,
) -> dict:
    """
    Run the ARBITER model with unresolved disputes.

    Returns ``{"phase": "arbiter", "verdict": "APPROVE|REJECT",
               "conditions": [...], "exit_code": 0}``.
    """
    try:
        prompt = (
            "You are the arbiter. Resolve the following disputed findings and "
            "issue a final APPROVE or REJECT verdict. If APPROVE, list any "
            "conditions that must hold. Prefer responding as JSON "
            "({\"verdict\": ..., \"conditions\": [...]}).\n\n"
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
        verdict, conditions = _parse(stdout)
        return {
            "phase": "arbiter",
            "exit_code": 0,
            "verdict": verdict,
            "conditions": conditions,
            "stdout": stdout,
        }
    except Exception as exc:
        return {"phase": "arbiter", "exit_code": 1, "error": str(exc)}
