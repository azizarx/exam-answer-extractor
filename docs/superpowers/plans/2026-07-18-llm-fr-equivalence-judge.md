# LLM Free-Response Equivalence Judge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After deterministic marking, use a batched Gemini call per candidate to judge whether free-response answers that failed the strict normalizer are semantically equivalent to the answer key, without rewriting stored student responses.

**Architecture:** Keep `MarkingService.mark` purely deterministic. Add `fr_equivalence_judge.py` (prompt + parse + client) and `apply_fr_equivalence_judge()` to merge LLM verdicts into outcomes. Wire the judge into `mark_submission_answers` before persisting `CandidateMarking`. Extend outcome schema/UI with `needs_review` and audit fields.

**Tech Stack:** FastAPI/SQLAlchemy marking pipeline, Gemini via `create_gemini_model` + `llm_call`, pytest with mocked judge, React Results UI badges.

**Spec:** `docs/superpowers/specs/2026-07-18-llm-fr-equivalence-judge-design.md`

---

## File map

| File | Responsibility |
|------|----------------|
| `backend/services/marking_service.py` | Extend `QuestionOutcome`; add `apply_fr_equivalence_judge`; keep `mark` deterministic |
| `backend/services/fr_equivalence_judge.py` | **Create** — prompt, parse, `FrEquivalenceJudge` protocol + Gemini impl |
| `backend/services/marking_workflow.py` | Call judge after `marker.mark`, before persist |
| `backend/api/schemas.py` | `needs_review` + optional judge fields on outcome schema |
| `frontend/src/components/ResultsDisplay/CandidateDetailModal.jsx` | Badge + rationale for `needs_review` / LLM fields |
| `tests/test_fr_equivalence_judge.py` | **Create** — parse/merge/prompt-payload tests (mocked) |
| `tests/test_marking_service.py` | Update outcome `asdict` expectations for new fields |
| `tests/test_marking_workflow.py` | Mock judge in workflow; assert fallback path |

---

### Task 1: Extend `QuestionOutcome` with judge audit fields

**Files:**
- Modify: `backend/services/marking_service.py` (`QuestionOutcome`, `MarkingService.mark`)
- Modify: `tests/test_marking_service.py`
- Test: `tests/test_marking_service.py`

- [ ] **Step 1: Update existing tests for new default fields**

In `tests/test_marking_service.py`, extend every exact `asdict(outcome)` expectation to include:

```python
"judge_source": "deterministic",
"judge_verdict": None,
"judge_reason": None,
```

Also update `test_weighted_marking_has_exact_auditable_output_without_answers` for both first and last outcomes.

- [ ] **Step 2: Run tests — expect FAIL (fields missing on dataclass)**

Run: `.venv/bin/python -m pytest tests/test_marking_service.py::test_weighted_marking_has_exact_auditable_output_without_answers -v`

Expected: FAIL — `asdict` missing `judge_*` keys (or AssertionError on dict equality).

- [ ] **Step 3: Extend `QuestionOutcome` and populate defaults in `mark`**

In `backend/services/marking_service.py`, change:

```python
@dataclass(frozen=True)
class QuestionOutcome:
    question_number: int
    status: str
    response: Any
    awarded_marks: int
    max_marks: int
    normalizer: str
    judge_source: str = "deterministic"
    judge_verdict: Optional[str] = None
    judge_reason: Optional[str] = None
```

In `MarkingService.mark`, when constructing each `QuestionOutcome`, pass explicit defaults (or rely on dataclass defaults):

```python
QuestionOutcome(
    question_number=question.number,
    status=status,
    response=response,
    awarded_marks=awarded,
    max_marks=question.marks,
    normalizer=question.normalizer,
    judge_source="deterministic",
    judge_verdict=None,
    judge_reason=None,
)
```

Do **not** change normalizer logic.

- [ ] **Step 4: Run marking service tests**

Run: `.venv/bin/python -m pytest tests/test_marking_service.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/marking_service.py tests/test_marking_service.py
git commit -m "feat(marking): add judge audit fields on QuestionOutcome"
```

---

### Task 2: Pure merge helper `apply_fr_equivalence_judge`

**Files:**
- Modify: `backend/services/marking_service.py`
- Create: `tests/test_fr_equivalence_judge.py`
- Test: `tests/test_fr_equivalence_judge.py`

- [ ] **Step 1: Write failing tests for merge mapping**

Create `tests/test_fr_equivalence_judge.py`:

```python
from dataclasses import asdict

from backend.services.marking_service import (
    AnswerKeyManifest,
    CandidateMarkingResult,
    ManifestQuestion,
    MarkingService,
    QuestionOutcome,
    apply_fr_equivalence_judge,
)


def _tiny_manifest() -> AnswerKeyManifest:
    return AnswerKeyManifest(
        template_id="test_fr",
        version=1,
        source_filename="x.pdf",
        source_sha256="abc",
        total_marks=12,
        questions=(
            ManifestQuestion(1, "mcq", ("A",), 3, "uppercase"),
            ManifestQuestion(21, "numeric", ("19",), 6, "integer"),
            ManifestQuestion(22, "time", ("5:00 PM",), 3, "time_12_24"),
        ),
    )


class FakeJudge:
    def __init__(self, verdicts: dict[int, tuple[str, str]]):
        self.verdicts = verdicts
        self.calls = []

    def judge(self, items: list[dict]) -> list[dict]:
        self.calls.append(items)
        out = []
        for item in items:
            q = item["question_number"]
            verdict, reason = self.verdicts[q]
            out.append({"question_number": q, "verdict": verdict, "reason": reason})
        return out


def test_deterministic_correct_and_blank_skip_judge():
    manifest = _tiny_manifest()
    result = MarkingService(manifest).mark({"1": "A", "21": "19", "22": ""})
    judge = FakeJudge({})
    merged = apply_fr_equivalence_judge(result, manifest, judge)
    assert judge.calls == []
    assert merged.awarded_marks == result.awarded_marks


def test_llm_equivalent_promotes_invalid_time_to_correct():
    manifest = _tiny_manifest()
    result = MarkingService(manifest).mark({"1": "A", "21": "19", "22": "5:00vaqt"})
    assert {o.question_number: o.status for o in result.outcomes}[22] == "invalid"
    judge = FakeJudge({22: ("equivalent", "same time; non-conflicting word")})
    merged = apply_fr_equivalence_judge(result, manifest, judge)
    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[22].status == "correct"
    assert by_q[22].awarded_marks == 3
    assert by_q[22].response == "5:00vaqt"  # unchanged
    assert by_q[22].judge_source == "llm"
    assert by_q[22].judge_verdict == "equivalent"
    assert "time" in by_q[22].judge_reason
    assert merged.awarded_marks == 3 + 6 + 3


def test_not_equivalent_and_uncertain_and_mcq_excluded():
    manifest = _tiny_manifest()
    result = MarkingService(manifest).mark(
        {"1": "B", "21": "0=19", "22": "4:40"}
    )
    judge = FakeJudge(
        {
            21: ("equivalent", "value 19"),
            22: ("not_equivalent", "different time"),
        }
    )
    merged = apply_fr_equivalence_judge(result, manifest, judge)
    assert len(judge.calls) == 1
    queued = {i["question_number"] for i in judge.calls[0]}
    assert queued == {21, 22}  # MCQ incorrect excluded
    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[1].status == "incorrect"
    assert by_q[1].judge_source == "deterministic"
    assert by_q[21].status == "correct"
    assert by_q[22].status == "incorrect"
    assert by_q[22].judge_verdict == "not_equivalent"


def test_uncertain_and_judge_error_become_needs_review():
    manifest = _tiny_manifest()
    result = MarkingService(manifest).mark({"1": "A", "21": "weird", "22": "5:00vaqt"})

    class UncertainJudge:
        def judge(self, items):
            return [
                {"question_number": 21, "verdict": "uncertain", "reason": "illegible digits"},
                {"question_number": 22, "verdict": "equivalent", "reason": "ok"},
            ]

    merged = apply_fr_equivalence_judge(result, manifest, UncertainJudge())
    by_q = {o.question_number: o for o in merged.outcomes}
    assert by_q[21].status == "needs_review"
    assert by_q[21].awarded_marks == 0
    assert by_q[22].status == "correct"

    class BoomJudge:
        def judge(self, items):
            raise RuntimeError("gemini down")

    merged_err = apply_fr_equivalence_judge(result, manifest, BoomJudge())
    by_q = {o.question_number: o for o in merged_err.outcomes}
    assert by_q[21].status == "needs_review"
    assert by_q[21].judge_source == "llm_fallback_error"
    assert by_q[22].status == "needs_review"
    assert by_q[22].judge_source == "llm_fallback_error"
```

- [ ] **Step 2: Run tests — expect FAIL (function missing)**

Run: `.venv/bin/python -m pytest tests/test_fr_equivalence_judge.py -v`

Expected: FAIL with `ImportError` / `apply_fr_equivalence_judge` not defined.

- [ ] **Step 3: Implement `apply_fr_equivalence_judge`**

Add to `backend/services/marking_service.py`:

```python
FR_TYPES = frozenset({"numeric", "time"})
FALLBACK_STATUSES = frozenset({"incorrect", "invalid"})


def apply_fr_equivalence_judge(
    result: CandidateMarkingResult,
    manifest: AnswerKeyManifest,
    judge: Any,
) -> CandidateMarkingResult:
    """Merge LLM FR equivalence verdicts; never mutates input answers."""
    type_by_q = {q.number: q for q in manifest.questions}
    marks_by_q = {q.number: q.marks for q in manifest.questions}

    items: list[dict[str, Any]] = []
    for outcome in result.outcomes:
        question = type_by_q.get(outcome.question_number)
        if question is None:
            continue
        if question.type not in FR_TYPES:
            continue
        if outcome.status not in FALLBACK_STATUSES:
            continue
        items.append(
            {
                "question_number": outcome.question_number,
                "type": question.type,
                "accepted_answers": list(question.accepted_answers),
                "response": outcome.response,
            }
        )

    if not items:
        return result

    try:
        verdicts = judge.judge(items)
    except Exception as exc:
        reason = f"judge failed: {type(exc).__name__}: {exc}"
        new_outcomes = []
        for outcome in result.outcomes:
            if any(i["question_number"] == outcome.question_number for i in items):
                new_outcomes.append(
                    QuestionOutcome(
                        question_number=outcome.question_number,
                        status="needs_review",
                        response=outcome.response,
                        awarded_marks=0,
                        max_marks=outcome.max_marks,
                        normalizer=outcome.normalizer,
                        judge_source="llm_fallback_error",
                        judge_verdict="uncertain",
                        judge_reason=reason,
                    )
                )
            else:
                new_outcomes.append(outcome)
        awarded = sum(o.awarded_marks for o in new_outcomes)
        return CandidateMarkingResult(
            outcomes=tuple(new_outcomes),
            awarded_marks=awarded,
            max_marks=result.max_marks,
            percentage=awarded * 100.0 / result.max_marks,
        )

    by_verdict = {
        int(v["question_number"]): v
        for v in (verdicts or [])
        if isinstance(v, dict) and "question_number" in v
    }

    new_outcomes = []
    for outcome in result.outcomes:
        if not any(i["question_number"] == outcome.question_number for i in items):
            new_outcomes.append(outcome)
            continue
        raw = by_verdict.get(outcome.question_number)
        if raw is None:
            verdict, reason = "uncertain", "missing verdict from judge response"
        else:
            verdict = str(raw.get("verdict") or "uncertain").strip().lower()
            reason = str(raw.get("reason") or "").strip() or None
            if verdict not in {"equivalent", "not_equivalent", "uncertain"}:
                verdict, reason = "uncertain", f"invalid verdict: {verdict}"

        if verdict == "equivalent":
            status, awarded, source = "correct", marks_by_q[outcome.question_number], "llm"
        elif verdict == "not_equivalent":
            status, awarded, source = "incorrect", 0, "llm"
        else:
            status, awarded, source = "needs_review", 0, "llm"

        new_outcomes.append(
            QuestionOutcome(
                question_number=outcome.question_number,
                status=status,
                response=outcome.response,
                awarded_marks=awarded,
                max_marks=outcome.max_marks,
                normalizer=outcome.normalizer,
                judge_source=source,
                judge_verdict=verdict,
                judge_reason=reason,
            )
        )

    awarded_total = sum(o.awarded_marks for o in new_outcomes)
    return CandidateMarkingResult(
        outcomes=tuple(new_outcomes),
        awarded_marks=awarded_total,
        max_marks=result.max_marks,
        percentage=awarded_total * 100.0 / result.max_marks,
    )
```

- [ ] **Step 4: Run merge tests**

Run: `.venv/bin/python -m pytest tests/test_fr_equivalence_judge.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/marking_service.py tests/test_fr_equivalence_judge.py
git commit -m "feat(marking): merge FR LLM equivalence verdicts into outcomes"
```

---

### Task 3: Gemini FR judge client (prompt + parse)

**Files:**
- Create: `backend/services/fr_equivalence_judge.py`
- Modify: `tests/test_fr_equivalence_judge.py`
- Test: `tests/test_fr_equivalence_judge.py`

- [ ] **Step 1: Write failing tests for prompt payload + JSON parse**

Append to `tests/test_fr_equivalence_judge.py`:

```python
import json
from backend.services.fr_equivalence_judge import (
    GeminiFrEquivalenceJudge,
    build_judge_prompt,
    parse_judge_response,
)


def test_build_judge_prompt_includes_unbiased_rules_and_items():
    items = [
        {
            "question_number": 22,
            "type": "time",
            "accepted_answers": ["5:00 PM"],
            "response": "5:00vaqt",
        }
    ]
    prompt = build_judge_prompt(items)
    assert "not_equivalent" in prompt
    assert "uncertain" in prompt
    assert "conflicting units" in prompt.lower() or "units must not conflict" in prompt.lower()
    assert "5:00vaqt" in prompt
    assert "5:00 PM" in prompt
    assert "benefit of the doubt" not in prompt.lower() or "Do NOT give benefit of the doubt" in prompt


def test_parse_judge_response_extracts_items():
    text = json.dumps(
        {
            "items": [
                {
                    "question_number": 22,
                    "verdict": "equivalent",
                    "reason": "same time",
                }
            ]
        }
    )
    parsed = parse_judge_response(text)
    assert parsed == [
        {"question_number": 22, "verdict": "equivalent", "reason": "same time"}
    ]


def test_parse_judge_response_rejects_garbage():
    import pytest
    with pytest.raises(ValueError):
        parse_judge_response("not json")
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `.venv/bin/python -m pytest tests/test_fr_equivalence_judge.py::test_build_judge_prompt_includes_unbiased_rules_and_items -v`

Expected: FAIL ImportError

- [ ] **Step 3: Implement `fr_equivalence_judge.py`**

Create `backend/services/fr_equivalence_judge.py`:

```python
"""Batched LLM judge: is an extracted FR response equivalent to accepted answers?"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

import google.generativeai as genai

from backend.services.gemini_client import create_gemini_model
from backend.services.run_logger import llm_call

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class FrEquivalenceJudge(Protocol):
    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return list of {question_number, verdict, reason}."""


def build_judge_prompt(items: list[dict[str, Any]]) -> str:
    payload = json.dumps({"items": items}, ensure_ascii=False)
    return (
        "You are an unbiased exam marking assistant.\n"
        "For each item, decide if the candidate's extracted response is equivalent "
        "in meaning to ANY of the accepted_answers.\n"
        "\n"
        "Rules:\n"
        "- Compare only the provided strings. Do not invent context.\n"
        "- Allow format/spelling variants that preserve the same value "
        "(e.g. nine≡9, 5:00vaqt≡5:00 PM, 0=19≡19 when 19 is the value).\n"
        "- Units must not conflict (5m is NOT equivalent to 5km).\n"
        "- If the key has no unit and the response adds a non-conflicting word/gloss "
        "that does not change the quantity, that may be equivalent.\n"
        "- Different values are not_equivalent. Do NOT give benefit of the doubt.\n"
        "- If unsure, verdict must be uncertain.\n"
        "- Verdict must be exactly one of: equivalent, not_equivalent, uncertain.\n"
        "- Always include a short reason.\n"
        "\n"
        "Return ONLY valid JSON (no markdown fences) with schema:\n"
        '{"items":[{"question_number":<int>,"verdict":"<str>","reason":"<str>"},...]}\n'
        "\n"
        f"INPUT:\n{payload}\n"
    )


def parse_judge_response(text: str) -> list[dict[str, Any]]:
    if not text or not str(text).strip():
        raise ValueError("empty judge response")
    raw = str(text).strip()
    fence = _FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    data = json.loads(raw)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("judge response missing items list")
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if "question_number" not in item:
            continue
        cleaned.append(
            {
                "question_number": int(item["question_number"]),
                "verdict": str(item.get("verdict") or "uncertain"),
                "reason": str(item.get("reason") or ""),
            }
        )
    if not cleaned:
        raise ValueError("judge response had no usable items")
    return cleaned


class GeminiFrEquivalenceJudge:
    def __init__(self, model=None, model_name: str | None = None):
        if model is None:
            self.model, self.model_name = create_gemini_model()
        else:
            self.model = model
            self.model_name = model_name or "injected"

    def judge(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not items:
            return []
        prompt = build_judge_prompt(items)
        response = llm_call(
            "fr_equivalence_judge",
            self.model,
            [prompt],
            genai.GenerationConfig(temperature=0.0),
            logger,
        )
        text = getattr(response, "text", None) or ""
        return parse_judge_response(text)
```

- [ ] **Step 4: Run judge unit tests**

Run: `.venv/bin/python -m pytest tests/test_fr_equivalence_judge.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/fr_equivalence_judge.py tests/test_fr_equivalence_judge.py
git commit -m "feat(marking): add Gemini FR equivalence judge client"
```

---

### Task 4: Wire judge into marking workflow

**Files:**
- Modify: `backend/services/marking_workflow.py` (marking loop ~189–203)
- Modify: `tests/test_marking_workflow.py`
- Test: `tests/test_marking_workflow.py`

- [ ] **Step 1: Add a workflow test that injects a fake judge**

If the workflow hard-codes `GeminiFrEquivalenceJudge()`, add an optional parameter for tests:

```python
def mark_submission_answers(
    db: Session,
    submission_id: int,
    *,
    answer_key_id: int | None = None,
    fr_judge: Any | None = None,
) -> MarkingRun:
```

In the candidate loop:

```python
from backend.services.marking_service import apply_fr_equivalence_judge
from backend.services.fr_equivalence_judge import GeminiFrEquivalenceJudge

judge = fr_judge if fr_judge is not None else GeminiFrEquivalenceJudge()
...
result = marker.mark(candidate.answers or {})
result = apply_fr_equivalence_judge(result, manifest, judge)
db.add(CandidateMarking(..., outcomes=[asdict(outcome) for outcome in result.outcomes], ...))
```

Write/adjust a test in `tests/test_marking_workflow.py` that passes a `FakeJudge` promoting one invalid FR to correct and asserts persisted `outcomes` contain `judge_source=llm` and unchanged `response`. Follow existing DB fixture patterns in that file.

- [ ] **Step 2: Run the new/updated workflow test — FAIL until wired**

Run: `.venv/bin/python -m pytest tests/test_marking_workflow.py -v -k fr_equivalence`

Expected: FAIL until implementation lands (or collect-only if test name differs — use the name you chose).

- [ ] **Step 3: Implement the wiring as above**

Keep `fr_judge=None` default so production uses Gemini. Do not catch judge errors in the workflow beyond what `apply_fr_equivalence_judge` already does (per-candidate soft `needs_review`).

- [ ] **Step 4: Run workflow + FR judge tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_marking_workflow.py tests/test_fr_equivalence_judge.py tests/test_marking_service.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/services/marking_workflow.py tests/test_marking_workflow.py
git commit -m "feat(marking): run FR equivalence judge during submission marking"
```

---

### Task 5: API schema + frontend `needs_review`

**Files:**
- Modify: `backend/api/schemas.py`
- Modify: `frontend/src/components/ResultsDisplay/CandidateDetailModal.jsx`
- Modify: `tests/test_marking_api.py` if it asserts outcome shape
- Test: `tests/test_marking_api.py` + frontend lint

- [ ] **Step 1: Extend API schema**

In `backend/api/schemas.py`:

```python
OutcomeStatus = Literal["correct", "incorrect", "blank", "invalid", "needs_review"]


class QuestionOutcomeSchema(BaseModel):
    question_number: int
    status: OutcomeStatus
    response: JsonScalar = None
    awarded_marks: float
    max_marks: float
    normalizer: str
    judge_source: Optional[str] = None
    judge_verdict: Optional[str] = None
    judge_reason: Optional[str] = None
```

- [ ] **Step 2: Update CandidateDetailModal badges**

In `frontend/src/components/ResultsDisplay/CandidateDetailModal.jsx`, add to the status map (alongside `correct` / `incorrect` / `blank` / `invalid`):

```javascript
needs_review: {
  label: 'Needs review',
  icon: AlertTriangle, // or another existing lucide icon already imported
  classes: 'border-amber-200 bg-amber-50 text-amber-900',
},
```

Where each outcome row is rendered, if `outcome.judge_reason` is present, show it as secondary text under the status (do not display accepted answers). Keep raw `response` display as-is.

- [ ] **Step 3: Run API tests + frontend lint**

```bash
.venv/bin/python -m pytest tests/test_marking_api.py -v
cd frontend && npm run lint
```

Expected: PASS (fix any schema assertion breakage in API tests).

- [ ] **Step 4: Commit**

```bash
git add backend/api/schemas.py frontend/src/components/ResultsDisplay/CandidateDetailModal.jsx tests/test_marking_api.py
git commit -m "feat(marking): expose needs_review and judge rationale in API/UI"
```

---

### Task 6: Smoke verification on known UZ1-style cases (optional live)

**Files:** none required (script or pytest with mock already covers CI)

- [ ] **Step 1: Offline assert via FakeJudge already done in Task 2**

Confirm these cases are covered in `tests/test_fr_equivalence_judge.py`:

- `5:00vaqt` → equivalent → correct  
- wrong time → not_equivalent → incorrect  
- uncertain / boom → needs_review  

- [ ] **Step 2: (Optional) Live smoke against one candidate with real Gemini**

Only if `GEMINI_API_KEY` is set and user wants it:

```bash
.venv/bin/python - <<'PY'
from backend.services.fr_equivalence_judge import GeminiFrEquivalenceJudge
j = GeminiFrEquivalenceJudge()
print(j.judge([{
  "question_number": 22,
  "type": "time",
  "accepted_answers": ["5:00 PM"],
  "response": "5:00vaqt",
}]))
PY
```

Expected: verdict `equivalent` (non-deterministic models — if `uncertain`, prompt may need a one-line tighten; do not loosen to fail-open).

- [ ] **Step 3: Final test suite**

```bash
.venv/bin/python -m pytest tests/test_marking_service.py tests/test_marking_workflow.py tests/test_marking_api.py tests/test_fr_equivalence_judge.py -v
```

Expected: all PASS

- [ ] **Step 4: Commit only if prompt tweak was needed; else done**

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| MCQ deterministic only | Task 2 (excluded from batch) |
| Raw response never rewritten | Task 2 assertions |
| Fallback only if not correct/blank | Task 2 |
| Batch per candidate | Task 3–4 |
| equivalent / not_equivalent / uncertain | Task 2–3 |
| needs_review on uncertain + failures | Task 2 |
| Always store reason | Task 2–3 |
| Units must not conflict (prompt) | Task 3 |
| Wire into marking workflow | Task 4 |
| API + UI | Task 5 |

## Self-review notes

- No TBD placeholders in steps.
- `QuestionOutcome` field names are consistent across tasks (`judge_source`, `judge_verdict`, `judge_reason`).
- `MarkingService.mark` stays deterministic; orchestration is `apply_fr_equivalence_judge` + workflow.
- Existing exact `asdict` tests are updated in Task 1 before adding merge logic.
