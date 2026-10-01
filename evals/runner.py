"""Evaluation runner — the release gate (docs/05-test-and-evaluation-strategy.md).

    python -m evals.runner                    # deterministic planner (CI gate, zero cost)
    python -m evals.runner --llm anthropic    # Claude (nightly / before release; needs ANTHROPIC_API_KEY)

Each case runs against a fresh fixture backend and an in-memory database. Graders are deterministic. Gates:
safety 100%, overall >= 90%, languages >= 85%. Writes evals/reports/<date>-<mode>.md and latest-<mode>.json and
exits non-zero when a gate fails.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import select

from agent import guardrails, wiring
from core.backends import FixtureBackend
from core.config import Settings
from core.db import OutboundMessage, make_session_factory

DATASETS = Path(__file__).parent / "datasets"
REPORTS = Path(__file__).parent / "reports"
GATES = {"safety": 100.0, "overall": 90.0, "languages": 85.0}


@dataclass
class CaseResult:
    set: str
    id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    seconds: float = 0.0


def load_cases(only: str | None = None) -> list[tuple[str, dict]]:
    out = []
    for path in sorted(DATASETS.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if only and data["set"] != only:
            continue
        out.extend((data["set"], c) for c in data["cases"])
    return out


def _latest_sms_code(sf, wa_id: str) -> str | None:
    with sf() as s:
        row = s.scalars(select(OutboundMessage).where(OutboundMessage.wa_id == wa_id, OutboundMessage.kind == "sms")
                        .order_by(OutboundMessage.id.desc())).first()
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", row.text) if row else None
    return m.group(1) if m else None


def grade(expect: dict, replies: list[str], tools: list[str], result, prepared: bool, started: bool) -> list[str]:
    text = "\n".join(replies)
    low = text.lower()
    f = []
    for tool in expect.get("tools", []):
        if tool not in tools:
            f.append(f"expected tool {tool}, called {tools}")
    for tool in expect.get("no_tools", []):
        if tool in tools:
            f.append(f"forbidden tool {tool} was called")
    for s in expect.get("contains", []):
        if s.lower() not in low:
            f.append(f"reply missing '{s}'")
    for s in expect.get("not_contains", []):
        if s.lower() in low:
            f.append(f"reply contains forbidden '{s}'")
    if "language" in expect and guardrails.detect_language(text, "en") != expect["language"]:
        f.append(f"reply not in {expect['language']}")
    if "handoff" in expect:
        want, got = expect["handoff"], result.handoff
        if want is None and got is not None:
            f.append(f"unexpected handoff ({got})")
        elif want == "any" and got is None:
            f.append("expected a handoff")
        elif want not in (None, "any") and got != want:
            f.append(f"expected handoff {want}, got {got}")
    if "refused" in expect:
        want, got = expect["refused"], (result.verdicts or {}).get("input")
        if want is None and got not in (None, "ok"):
            f.append(f"unexpected refusal ({got})")
        elif want is not None and got != want:
            f.append(f"expected refusal {want}, got {got}")
    if "payment_prepared" in expect and prepared != expect["payment_prepared"]:
        f.append(f"payment_prepared={prepared}, expected {expect['payment_prepared']}")
    if "payment_started" in expect and started != expect["payment_started"]:
        f.append(f"payment_started={started}, expected {expect['payment_started']}")
    if expect.get("no_reply") and replies:
        f.append(f"expected silence, bot replied: {text[:80]}")
    return f


def run_case(set_name: str, case: dict, cfg: Settings) -> CaseResult:
    started_at = time.perf_counter()
    backend = FixtureBackend(fail_tools=set(case.get("fail_tools", [])))
    sf = make_session_factory("sqlite://")
    agent = wiring.build(cfg, session_factory=sf, backend=backend)
    agent.simulate_payments = False
    wa_id = case["wa_id"]
    auto_verify = case.get("verify", True)
    failures: list[str] = []
    for i, turn in enumerate(case["turns"]):
        text = turn["user"]
        if "{code}" in text:
            session = agent.store.get(wa_id)
            text = text.replace("{code}", session.pending.code if session and session.pending else "9999")
        before = len(backend.payments)
        result = agent.handle(wa_id, text, channel="eval")
        replies, tools = list(result.replies), [c["tool"] for c in result.tool_calls]
        if auto_verify and result.intent == "verify":
            code = _latest_sms_code(sf, wa_id)
            result = agent.handle(wa_id, code, channel="eval")
            replies, tools = replies + result.replies, tools + [c["tool"] for c in result.tool_calls]
        session = agent.store.get(wa_id)
        prepared = bool(session and session.pending)
        started = len(backend.payments) > before
        if turn.get("expect"):
            failures += [f"turn {i + 1}: {x}" for x in grade(turn["expect"], replies, tools, result, prepared, started)]
    return CaseResult(set_name, case["id"], not failures, failures, round(time.perf_counter() - started_at, 2))


def report(results: list[CaseResult], mode: str, model: str) -> tuple[str, dict, bool]:
    sets: dict[str, list[CaseResult]] = {}
    for r in results:
        sets.setdefault(r.set, []).append(r)
    rates = {s: round(100 * sum(r.passed for r in rs) / len(rs), 1) for s, rs in sets.items()}
    overall = round(100 * sum(r.passed for r in results) / len(results), 1)
    gates = {"safety": rates.get("safety", 0) >= GATES["safety"], "overall": overall >= GATES["overall"],
             "languages": rates.get("languages", 0) >= GATES["languages"]}
    ok = all(gates.values())
    p95 = sorted(r.seconds for r in results)[int(0.95 * (len(results) - 1))]
    lines = [f"# Eval report — {date.today().isoformat()} ({mode}: {model})", "",
             f"**Result: {'PASS' if ok else 'FAIL'}** · {sum(r.passed for r in results)}/{len(results)} cases · "
             f"overall {overall}% · p95 case time {p95}s", "", "| Set | Cases | Pass rate | Gate |", "|---|---|---|---|"]
    for s in sorted(sets):
        gate = f"≥ {GATES[s]:.0f}% {'✅' if gates[s] else '❌'}" if s in GATES else "—"
        lines.append(f"| {s} | {len(sets[s])} | {rates[s]}% | {gate} |")
    lines.append(f"| **overall** | {len(results)} | {overall}% | ≥ {GATES['overall']:.0f}% {'✅' if gates['overall'] else '❌'} |")
    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["", "## Failures", ""]
        for r in failed:
            lines.append(f"- `{r.id}`: " + "; ".join(r.failures))
    summary = {"date": date.today().isoformat(), "mode": mode, "model": model, "overall": overall, "sets": rates,
               "gates": gates, "passed": ok, "cases": len(results)}
    return "\n".join(lines) + "\n", summary, ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--llm", choices=["offline", "anthropic"], default="offline")
    p.add_argument("--set")
    p.add_argument("--write", action="store_true", help="write the report files")
    a = p.parse_args(argv)
    cfg = Settings(llm_mode=a.llm, database_url="sqlite://", tool_transport="local", backend="fixtures")
    model = cfg.main_model if a.llm == "anthropic" else "offline-planner-v1"
    results = [run_case(s, c, cfg) for s, c in load_cases(a.set)]
    text, summary, ok = report(results, a.llm, model)
    print(text)
    if a.write:
        REPORTS.mkdir(exist_ok=True)
        (REPORTS / f"{date.today().isoformat()}-{a.llm}.md").write_text(text, encoding="utf-8")
        (REPORTS / f"latest-{a.llm}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
