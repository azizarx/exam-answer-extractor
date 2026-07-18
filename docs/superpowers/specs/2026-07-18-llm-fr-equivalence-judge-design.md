# LLM Free-Response Equivalence Judge

**Date:** 2026-07-18  
**Status:** Approved (pending user review of this written spec)  
**Scope:** Marking only — does not change extraction or rewrite stored student answers

## Problem

Deterministic free-response (FR) normalizers reject many answers that are semantically equivalent to the key because candidates append words/units or use alternate spellings, e.g.:

- `5:00vaqt` vs accepted `5:00 PM`
- `0=19` vs accepted `19`
- `nine` / `eight` vs numeric keys

We also saw true extraction errors (e.g. `5:00PM` read as `3:00pm`). Those remain extraction issues; this design does **not** fix OCR. It only judges whether the *already extracted* string is equivalent to the accepted answer(s).

MCQ marking is already accurate enough via CV overlay and must stay deterministic.

## Goals

1. Keep raw extracted `response` values unchanged in marking outcomes and exports.
2. Keep MCQ fully deterministic.
3. Use an LLM only as a **fallback equivalence judge** for FR questions that the strict normalizer did not already mark `correct`.
4. Stay unbiased: same value / format variants only; no “benefit of the doubt” for wrong values; conflicting units are not equivalent.
5. When the LLM is unsure (or the call fails), surface `needs_review` rather than awarding or silently denying marks without visibility.

## Non-goals

- Rewriting or “cleaning” student answers via smarter normalizers before storage.
- Using the LLM to re-read the page image (that would be an extraction change).
- LLM judging of MCQ.
- Human override / re-mark UI workflows beyond displaying `needs_review` + rationale (override can be a follow-up).
- Changing answer-key JSON schema beyond optional future hints (not required for v1).

## Decisions (from design review)

| Topic | Choice |
|-------|--------|
| When to call LLM | Fallback only: skip if deterministic status is already `correct` or `blank` |
| Uncertainty | `needs_review` (0 marks until human handles it later) |
| Equivalence strictness | Same mathematical/time value + spelled-number variants; **units must not conflict** (`5m` ≠ `5km`) |
| Rationale | Always store a short reason on the outcome |
| Batching | One LLM call per candidate covering all FR fallback items |

## Design

### High-level flow

```
For each candidate:
  1. Deterministic MarkingService pass (unchanged normalizers)
  2. Collect FR outcomes where status ∈ {incorrect, invalid}
     (question.type ∈ {numeric, time}; never mcq)
  3. If none → persist deterministic result as today
  4. Else one Gemini call (batch) with items:
       {question_number, type, accepted_answers[], response}
  5. Parse structured verdicts; merge into outcomes
  6. Recompute awarded_marks / percentage
  7. Persist CandidateMarking with full outcomes (incl. LLM fields)
```

Blank FR answers never go to the LLM.

### Status mapping

| Deterministic | LLM verdict | Final status | Marks |
|---------------|-------------|--------------|-------|
| `correct` | (not called) | `correct` | full |
| `blank` | (not called) | `blank` | 0 |
| `incorrect` / `invalid` | `equivalent` | `correct` | full |
| `incorrect` / `invalid` | `not_equivalent` | `incorrect` | 0 |
| `incorrect` / `invalid` | `uncertain` | `needs_review` | 0 |
| any FR fallback | call/parse failure | `needs_review` | 0 |

Notes:

- Deterministic `invalid` that the LLM finds equivalent becomes `correct` (e.g. `5:00vaqt`).
- Deterministic `invalid` that is truly not equivalent becomes `incorrect` (clearer than leaving `invalid` after an explicit judge).
- `needs_review` is a new outcome status; awarded marks stay 0.

### Outcome schema extensions

Extend `QuestionOutcome` / API schema with optional LLM audit fields (raw `response` unchanged):

- `status`: add `needs_review` to the allowed set
- `judge_source`: `deterministic` | `llm` | `llm_fallback_error`
- `judge_verdict`: `equivalent` | `not_equivalent` | `uncertain` | null
- `judge_reason`: short string | null

Deterministic-only outcomes set `judge_source="deterministic"` and leave verdict/reason null (or omit nulls in JSON).

### LLM interface

**Module:** e.g. `backend/services/fr_equivalence_judge.py`  
**Model:** same Gemini client stack as extraction (`create_gemini_model` / configured Flash), temperature `0`.  
**Batching:** one request per candidate; response is JSON only.

**Input (conceptual):**

```json
{
  "items": [
    {
      "question_number": 22,
      "type": "time",
      "accepted_answers": ["5:00 PM"],
      "response": "5:00vaqt"
    }
  ]
}
```

**Output (conceptual):**

```json
{
  "items": [
    {
      "question_number": 22,
      "verdict": "equivalent",
      "reason": "Same time 5:00; trailing word is non-conflicting language for time."
    }
  ]
}
```

Missing question numbers in the response → treat those items as `uncertain` / `needs_review`.

### Unbiased judging rules (prompt contract)

The system prompt must instruct the model to:

1. Compare **meaning** of `response` to any of `accepted_answers` — not handwriting quality, not student intent beyond the written string.
2. Allow format / spelling variants that preserve value (`nine` ≡ `9`, `5:00vaqt` ≡ `5:00 PM`, `0=19` ≡ `19` when `19` is the intended value).
3. Reject different values (`4:40` ≠ `5:00`).
4. Reject **conflicting units** (`5m` ≠ `5km`). If the key has no unit and the response adds a non-conflicting gloss/word that does not change the quantity, that may be equivalent.
5. If unsure → `uncertain` (never invent equivalence).
6. Do not award partial credit; verdicts are ternary only.
7. Do not mention or use any candidate identity; only the fields provided.

No page images are sent in v1 (keeps cost down and avoids mixing extraction with marking).

### Integration points

| Area | Change |
|------|--------|
| `marking_service.MarkingService.mark` | Keep pure deterministic; return outcomes usable as input to judge step **or** add a thin orchestrator that calls mark → judge → merge |
| `marking_workflow` | After deterministic mark, run FR judge before persisting `CandidateMarking` |
| `schemas.OutcomeStatus` | Add `needs_review` |
| Frontend outcome badges | Render `needs_review` + show `judge_reason` |
| Tests | Unit tests for merge/mapping with mocked LLM; prompt contract examples; no live Gemini in CI |

Preferred structure: keep `MarkingService.mark` deterministic; add `apply_fr_equivalence_judge(result, judge_client) -> CandidateMarkingResult` so tests can inject a fake judge.

### Failure and rate limits

- Use existing Gemini RPM / retry helpers where practical.
- On `ResourceExhausted` / timeout / empty / invalid JSON: every queued item for that candidate → `needs_review` with reason describing the failure; do not fail the whole marking run if other candidates succeeded (match existing per-candidate isolation if any; otherwise fail soft per candidate).
- Marking run status remains `completed` if persistence succeeded; optionally set a run-level warning flag/count of `needs_review` for UI banners (nice-to-have).

### UI

- Candidate detail: badge for `needs_review`; show rationale under the outcome.
- Do not leak accepted answers in exports beyond what marking already exposes today (current product rule: no correct-answer leakage to students — keep the same policy; rationale must not paste the full key if that would be a new leak — prefer reasons like “different numeric value” without echoing keys if the UI is student-facing; operator UI may show accepted answers as today for markers).

Clarification for implementers: operator-facing Results UI already shows marking outcomes for staff; showing `accepted_answers` is unchanged from current marking UX. Rationale text should still avoid coaching language.

### Out of scope follow-ups

- Manual resolve of `needs_review` → force correct/incorrect
- Image-aware second pass for FR extraction errors
- Per-paper unit metadata in answer keys

## Testing plan

1. Deterministic `correct` / `blank` never call the judge (assert mock not invoked).
2. `5:00vaqt` + key `5:00 PM` → after mocked `equivalent` → `correct` + marks + reason stored.
3. `4:40` + key `5:00 PM` → mocked `not_equivalent` → `incorrect`.
4. Mocked `uncertain` → `needs_review`, 0 marks.
5. Judge raises / bad JSON → `needs_review`, `judge_source=llm_fallback_error`.
6. MCQ outcomes never appear in judge batch payload.
7. Frontend renders new status without breaking existing badges.

## Risks

| Risk | Mitigation |
|------|------------|
| LLM bias / generosity | Strict prompt; temperature 0; uncertain path; audit reasons |
| Cost / RPM | Batch per candidate; fallback-only; Flash model |
| Extraction errors marked “correct” if string happens to match key semantically by luck | Acceptable; image re-read is separate work |
| Schema migrations for stored JSON outcomes | Additive fields; old rows simply lack judge_* keys |

## Success criteria

- UZ1-style cases like `5:00vaqt` / `0=19` / spelled numbers can become `correct` via LLM without mutating stored responses.
- Conflicting-unit and wrong-value cases stay non-correct.
- Uncertain/failures are visible as `needs_review` in the UI with a reason.
- MCQ path and deterministic exact matches unchanged.
