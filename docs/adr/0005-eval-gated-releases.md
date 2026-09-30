# ADR-0005: Releases are gated by an evaluation suite, with deterministic graders first

- **Status:** Accepted · **Date:** 2026-09-30

## Context
Prompt, model or tool changes can silently change behaviour. Unit tests can't tell whether the assistant still answers correctly in Shona or still refuses third-party data requests.

## Options
1. Manual spot checks: cheap, but unreliable and not repeatable.
2. LLM-as-judge for everything: easy to write, but noisy and itself needs validating.
3. **Deterministic graders wherever possible** (tool trajectory, exact facts, safety patterns), with LLM-as-judge only for tone, calibrated against human labels.

## Decision
Option 3. Gates: safety 100%, overall ≥ 90%, languages ≥ 85%, no set regressing more than 3 points. Cassette replays run on every PR; live evals run nightly and before release under a token budget.

## Consequences
- ➕ "Continuous evaluation" is demonstrable, which is what 2026 AI hiring asks for.
- ➖ Token cost of nightly runs; capped by budget and small datasets.
- Every production failure or red-team finding becomes a new eval case.

## Sources
- [ODSC — in-demand AI jobs and skills 2026](https://opendatascience.com/12-most-in-demand-ai-jobs-in-2026-and-the-skills-employers-want/)
- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
