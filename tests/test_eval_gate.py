"""The offline eval suite is part of the unit-test run, so a guardrail regression fails the build immediately."""
from core.config import Settings
from evals.runner import load_cases, report, run_case


def test_eval_gates_pass_with_the_deterministic_planner():
    cfg = Settings(llm_mode="offline", database_url="sqlite://", tool_transport="local", backend="fixtures")
    results = [run_case(s, c, cfg) for s, c in load_cases()]
    text, summary, ok = report(results, "offline", "offline-planner-v1")
    assert summary["sets"]["safety"] == 100.0, text
    assert ok, text
