"""Pure-logic self-checks for --plan support (parse / validate / topo-sort).

No live model, no git writes. Run: ``python3 scripts/test_plan.py`` or pytest.
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_ROOT = os.path.dirname(_HERE)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

from scripts.phases import phase_plan as P  # noqa: E402

_SAMPLE = """\
---
spec: "feature-name"
version: "1.0"
findings-input: false
---

## Steps

### P1: Protocol variants
- **Files:** [protocol/src/lib.rs, shared/types.rs]
- **Description:** Add PrivateMessage to ClientMessage and ServerMessage enums
- **Dependencies:** []
- **Tests:** validate_client_message handles PrivateMessage
- **Risks:** None

### P2: Server routing
- **Files:** [server/src/main.rs]
- **Description:** find_peer_by_login(), process PrivateMessage
- **Dependencies:** [P1]
- **Tests:** Integration test
- **Risks:** Deadlock if peer_map held across lookup
"""


def _write_plan(text):
    fd, path = tempfile.mkstemp(suffix=".md", prefix="acl-plan-")
    os.close(fd)
    with open(path, "w") as fh:
        fh.write(text)
    return path


def test_parse_plan():
    steps = P.parse_plan(_write_plan(_SAMPLE))
    assert [s["id"] for s in steps] == ["P1", "P2"], steps
    p1 = steps[0]
    assert p1["title"] == "Protocol variants"
    assert p1["files"] == ["protocol/src/lib.rs", "shared/types.rs"], p1["files"]
    assert p1["dependencies"] == []
    assert "PrivateMessage" in p1["description"]
    assert steps[1]["dependencies"] == ["P1"], steps[1]


def test_validate_steps_ok_and_errors():
    steps = P.parse_plan(_write_plan(_SAMPLE))
    P.validate_steps(steps)  # no raise

    # unknown dependency
    bad = [{"id": "A", "description": "d", "dependencies": ["Z"]}]
    try:
        P.validate_steps(bad)
    except ValueError as exc:
        assert "Z" in str(exc)
    else:
        raise AssertionError("unknown dep not rejected")

    # missing description
    try:
        P.validate_steps([{"id": "A", "description": "", "dependencies": []}])
    except ValueError as exc:
        assert "description" in str(exc)
    else:
        raise AssertionError("missing description not rejected")

    # cycle A -> B -> A
    cyc = [{"id": "A", "description": "d", "dependencies": ["B"]},
           {"id": "B", "description": "d", "dependencies": ["A"]}]
    try:
        P.validate_steps(cyc)
    except ValueError as exc:
        assert "circular" in str(exc)
    else:
        raise AssertionError("cycle not detected")


def test_topo_sort_orders_dependencies_first():
    # Input deliberately out of order; P3 depends on P2 depends on P1.
    raw = [
        {"id": "P3", "description": "d", "dependencies": ["P2"]},
        {"id": "P1", "description": "d", "dependencies": []},
        {"id": "P2", "description": "d", "dependencies": ["P1"]},
    ]
    order = [s["id"] for s in P.topo_sort(raw)]
    assert order.index("P1") < order.index("P2") < order.index("P3"), order


def test_step_spec_text_carries_fields():
    step = {"id": "P1", "title": "T", "files": ["a.rs"], "description": "Do X",
            "dependencies": ["P0"], "tests": "t", "risks": "r"}
    txt = P._step_spec_text(step)
    for needle in ("P1", "T", "a.rs", "Do X", "t", "r", "P0"):
        assert needle in txt, (needle, txt)


def test_parse_plan_rejects_multiline_bullet_lists():
    bad = """\
### P1: Step
- **Files:**
  - /path/one
  - /path/two
- **Description:** D
- **Dependencies:** []
"""
    try:
        P.parse_plan(_write_plan(bad))
    except ValueError as exc:
        assert "multi-line bullet list" in str(exc), exc
        assert "P1" in str(exc), exc
    else:
        raise AssertionError("multi-line Files: list should raise ValueError")
    # Explicit `[]` stays an intentional empty list, even with prose bullets after.
    ok = """\
### P1: Step
- **Files:** /path/one
- **Description:** D
- **Dependencies:** []
- **Risks:** none
"""
    steps = P.parse_plan(_write_plan(ok))
    assert steps[0]["files"] == ["/path/one"], steps


def main():
    test_parse_plan()
    test_validate_steps_ok_and_errors()
    test_topo_sort_orders_dependencies_first()
    test_step_spec_text_carries_fields()
    test_parse_plan_rejects_multiline_bullet_lists()
    print("OK: plan parse / validate / topo-sort self-checks pass")


if __name__ == "__main__":
    main()
