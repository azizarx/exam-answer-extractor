"""Validated answer-key loading, deterministic marking, and DB synchronization."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.services.template_service import ExamTemplate, TemplateRegistry


ALLOWED_TYPES = {"mcq", "numeric", "time"}
TYPE_NORMALIZERS = {
    "mcq": "uppercase",
    "numeric": "integer",
    "time": "time_12_24",
}
INTEGER_RE = re.compile(r"^[+-]?\d+$")
MAX_INTEGER_DIGITS = 1000
TIME_RE = re.compile(
    r"^(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>[AP]\.?M\.?)?$",
    re.IGNORECASE,
)


class ManifestValidationError(ValueError):
    """Raised when an answer-key manifest is unsafe to activate."""


class AnswerKeySynchronizationError(ValueError):
    """Raised when persisted answer-key history cannot be synchronized safely."""


@dataclass(frozen=True)
class ManifestQuestion:
    number: int
    type: str
    accepted_answers: tuple[str, ...]
    marks: int
    normalizer: str


@dataclass(frozen=True)
class AnswerKeyManifest:
    template_id: str
    version: int
    source_filename: str
    source_sha256: str
    total_marks: int
    questions: tuple[ManifestQuestion, ...]


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


@dataclass(frozen=True)
class CandidateMarkingResult:
    outcomes: tuple[QuestionOutcome, ...]
    awarded_marks: int
    max_marks: int
    percentage: float


@dataclass(frozen=True)
class NormalizedResponse:
    status: str
    value: Optional[str]


class ResponseNormalizer(Protocol):
    def __call__(self, response: Any) -> NormalizedResponse: ...


def _sentinel(response: Any) -> Optional[NormalizedResponse]:
    if response is None:
        return NormalizedResponse("blank", None)
    if not isinstance(response, str):
        return NormalizedResponse("invalid", None)
    value = response.strip()
    if not value or value.upper() == "BL":
        return NormalizedResponse("blank", None)
    if value.upper() == "IN":
        return NormalizedResponse("invalid", None)
    return None


def normalize_uppercase(response: Any) -> NormalizedResponse:
    sentinel = _sentinel(response)
    if sentinel:
        return sentinel
    return NormalizedResponse("valid", response.strip().upper())


def normalize_integer(response: Any) -> NormalizedResponse:
    sentinel = _sentinel(response)
    if sentinel:
        return sentinel
    value = response.strip()
    if not INTEGER_RE.fullmatch(value):
        return NormalizedResponse("invalid", None)
    unsigned = value.lstrip("+-")
    if len(unsigned) > MAX_INTEGER_DIGITS:
        return NormalizedResponse("invalid", None)
    try:
        canonical = str(int(value))
    except ValueError:
        return NormalizedResponse("invalid", None)
    return NormalizedResponse("valid", canonical)


def normalize_time_12_24(response: Any) -> NormalizedResponse:
    sentinel = _sentinel(response)
    if sentinel:
        return sentinel
    match = TIME_RE.fullmatch(response.strip())
    if not match:
        return NormalizedResponse("invalid", None)
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or "0")
    period = match.group("period")
    if minute > 59:
        return NormalizedResponse("invalid", None)
    if period:
        if not 1 <= hour <= 12:
            return NormalizedResponse("invalid", None)
        normalized_period = period.replace(".", "").upper()
        hour = hour % 12 + (12 if normalized_period == "PM" else 0)
    elif not 0 <= hour <= 23:
        return NormalizedResponse("invalid", None)
    return NormalizedResponse("valid", f"{hour:02d}:{minute:02d}")


NORMALIZERS: dict[str, ResponseNormalizer] = {
    "uppercase": normalize_uppercase,
    "integer": normalize_integer,
    "time_12_24": normalize_time_12_24,
}


class ManifestRegistry:
    """Load and validate manifests before exposing them to marking."""

    def __init__(
        self,
        manifests_dir: Path,
        source_dir: Path,
        template_registry: TemplateRegistry,
    ):
        self.manifests_dir = Path(manifests_dir)
        self.source_dir = Path(source_dir)
        self.template_registry = template_registry
        self._manifests: dict[str, AnswerKeyManifest] = {}

    def load_all(self) -> list[AnswerKeyManifest]:
        loaded: list[AnswerKeyManifest] = []
        versions: set[tuple[str, int]] = set()
        active: dict[str, AnswerKeyManifest] = {}
        for path in sorted(self.manifests_dir.glob("*.json")):
            manifest = self.load(path)
            identity = (manifest.template_id, manifest.version)
            if identity in versions:
                raise ManifestValidationError(
                    f"duplicate manifest {manifest.template_id} version {manifest.version}"
                )
            versions.add(identity)
            loaded.append(manifest)
            previous = active.get(manifest.template_id)
            if previous is None or manifest.version > previous.version:
                active[manifest.template_id] = manifest
        self._manifests = active
        return sorted(loaded, key=lambda item: (item.template_id, item.version))

    def load(self, path: Path) -> AnswerKeyManifest:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestValidationError(f"cannot load manifest {path}: {exc}") from exc
        manifest = self._parse(raw, Path(path))
        self._validate(manifest)
        return manifest

    def get(self, template_id: str) -> Optional[AnswerKeyManifest]:
        return self._manifests.get(template_id)

    def get_or_raise(self, template_id: str) -> AnswerKeyManifest:
        try:
            return self._manifests[template_id]
        except KeyError as exc:
            raise KeyError(f"No active answer key for template {template_id}") from exc

    def _parse(self, raw: Any, path: Path) -> AnswerKeyManifest:
        try:
            if not isinstance(raw, dict):
                raise TypeError("manifest must be an object")
            source = raw["source"]
            if not isinstance(source, dict):
                raise TypeError("source must be an object")
            if not isinstance(raw["questions"], list):
                raise TypeError("questions must be a list")
            for question in raw["questions"]:
                if not isinstance(question, dict):
                    raise TypeError("each question must be an object")
                if not isinstance(question.get("accepted_answers"), list):
                    raise TypeError("accepted_answers must be a list")
            questions = tuple(
                ManifestQuestion(
                    number=q["number"],
                    type=q["type"],
                    accepted_answers=tuple(q["accepted_answers"]),
                    marks=q["marks"],
                    normalizer=q["normalizer"],
                )
                for q in raw["questions"]
            )
            return AnswerKeyManifest(
                template_id=raw["template_id"],
                version=raw["version"],
                source_filename=source["filename"],
                source_sha256=source["sha256"],
                total_marks=raw["total_marks"],
                questions=questions,
            )
        except (KeyError, TypeError) as exc:
            raise ManifestValidationError(f"invalid schema in {path.name}: {exc}") from exc

    def _validate(self, manifest: AnswerKeyManifest) -> None:
        if not isinstance(manifest.template_id, str) or not manifest.template_id:
            raise ManifestValidationError("template_id must be a non-empty string")
        if type(manifest.version) is not int or manifest.version < 1:
            raise ManifestValidationError("version must be a positive integer")
        if type(manifest.total_marks) is not int or manifest.total_marks <= 0:
            raise ManifestValidationError("total_marks must be a positive integer")
        if not isinstance(manifest.source_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", manifest.source_sha256
        ):
            raise ManifestValidationError("source sha256 must be lowercase hexadecimal")
        if (
            not isinstance(manifest.source_filename, str)
            or not manifest.source_filename
            or Path(manifest.source_filename).name != manifest.source_filename
            or Path(manifest.source_filename).suffix.lower() != ".pdf"
        ):
            raise ManifestValidationError("source filename must name a PDF in source directory")

        template = self.template_registry.get(manifest.template_id)
        if template is None:
            raise ManifestValidationError(f"unknown template {manifest.template_id}")
        expected_numbers = _template_question_types(template)
        if not manifest.questions:
            raise ManifestValidationError("questions must be a non-empty list")
        if any(type(q.number) is not int or q.number < 1 for q in manifest.questions):
            raise ManifestValidationError("question number must be a positive integer")
        if any(type(q.marks) is not int or q.marks <= 0 for q in manifest.questions):
            raise ManifestValidationError("marks must be a positive integer")
        numbers = [q.number for q in manifest.questions]
        if numbers != sorted(expected_numbers):
            raise ManifestValidationError("question numbers must be unique, ordered, and match template")
        if sum(q.marks for q in manifest.questions) != manifest.total_marks:
            raise ManifestValidationError("question marks do not sum to total_marks")

        for question in manifest.questions:
            self._validate_question(question, expected_numbers[question.number], template)

        source_path = self.source_dir / manifest.source_filename
        if not source_path.is_file():
            raise ManifestValidationError(f"source PDF not found: {manifest.source_filename}")
        if _sha256(source_path) != manifest.source_sha256:
            raise ManifestValidationError(
                f"source hash mismatch for {manifest.source_filename}"
            )

    def _validate_question(
        self, question: ManifestQuestion, template_type: str, template: ExamTemplate
    ) -> None:
        if type(question.number) is not int or question.number < 1:
            raise ManifestValidationError("question number must be a positive integer")
        if not isinstance(question.type, str):
            raise ManifestValidationError("question type must be a string")
        if question.type not in ALLOWED_TYPES:
            raise ManifestValidationError(f"unsupported question type {question.type}")
        if template_type == "mcq" and question.type != "mcq":
            raise ManifestValidationError(f"question {question.number} must be mcq")
        if template_type == "numeric" and question.type not in {"numeric", "time"}:
            raise ManifestValidationError(f"question {question.number} must be numeric or time")
        if not isinstance(question.normalizer, str):
            raise ManifestValidationError("question normalizer must be a string")
        if question.normalizer != TYPE_NORMALIZERS[question.type]:
            raise ManifestValidationError(
                f"normalizer {question.normalizer} is invalid for {question.type}"
            )
        if type(question.marks) is not int or question.marks <= 0:
            raise ManifestValidationError("marks must be a positive integer")
        if not question.accepted_answers or not all(
            isinstance(answer, str) for answer in question.accepted_answers
        ):
            raise ManifestValidationError("accepted_answers must be a non-empty string list")

        normalized_answers: set[str] = set()
        normalizer = NORMALIZERS[question.normalizer]
        for answer in question.accepted_answers:
            normalized = normalizer(answer)
            if normalized.status != "valid" or normalized.value is None:
                raise ManifestValidationError(
                    f"invalid accepted answer for question {question.number}"
                )
            normalized_answers.add(normalized.value)
        if len(normalized_answers) != len(question.accepted_answers):
            raise ManifestValidationError(
                f"duplicate accepted answers for question {question.number}"
            )
        if question.type == "mcq":
            options = _template_options(template, question.number)
            if not normalized_answers <= options:
                raise ManifestValidationError(
                    f"answer outside template domain for question {question.number}"
                )


class MarkingService:
    """Apply one immutable manifest to candidate answers."""

    def __init__(self, manifest: AnswerKeyManifest):
        self.manifest = manifest
        self._accepted = {
            q.number: {
                NORMALIZERS[q.normalizer](answer).value
                for answer in q.accepted_answers
            }
            for q in manifest.questions
        }

    def mark(self, answers: Mapping[Any, Any]) -> CandidateMarkingResult:
        outcomes: list[QuestionOutcome] = []
        awarded_total = 0
        for question in self.manifest.questions:
            response = answers.get(str(question.number), answers.get(question.number))
            normalized = NORMALIZERS[question.normalizer](response)
            if normalized.status == "blank":
                status = "blank"
            elif normalized.status == "invalid":
                status = "invalid"
            elif normalized.value in self._accepted[question.number]:
                status = "correct"
            else:
                status = "incorrect"
            awarded = question.marks if status == "correct" else 0
            awarded_total += awarded
            outcomes.append(
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
            )
        return CandidateMarkingResult(
            outcomes=tuple(outcomes),
            awarded_marks=awarded_total,
            max_marks=self.manifest.total_marks,
            percentage=awarded_total * 100.0 / self.manifest.total_marks,
        )


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


def synchronize_answer_keys(
    db: Session, registry: ManifestRegistry
) -> list["AnswerKey"]:
    """Persist immutable versions and atomically activate each latest version.

    A savepoint contains the entire operation, so a uniqueness race cannot
    leave keys deactivated. Bounded retries handle concurrent inserts and
    SQLite writer locks by re-reading persisted state.
    """
    from backend.db.models import AnswerKey

    manifests = registry.load_all()
    last_error: Optional[Exception] = None
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            with db.begin_nested():
                synchronized = _synchronize_answer_keys_once(db, manifests, AnswerKey)
                db.flush()
            return synchronized
        except IntegrityError as exc:
            last_error = exc
            db.expire_all()
        except OperationalError as exc:
            if not _is_retryable_sqlite_lock(db, exc):
                raise
            last_error = exc
            # End SQLite's outer read snapshot before retrying. Rolling back
            # only the savepoint can otherwise produce SQLITE_BUSY_SNAPSHOT
            # indefinitely after the competing writer commits.
            db.rollback()
            if attempt < max_attempts - 1:
                time.sleep(0.005 * (attempt + 1))
    raise AnswerKeySynchronizationError(
        "answer-key synchronization conflicted with a concurrent writer"
    ) from last_error


def _is_retryable_sqlite_lock(db: Session, exc: OperationalError) -> bool:
    if db.get_bind().dialect.name != "sqlite":
        return False
    original = exc.orig
    if not isinstance(original, sqlite3.OperationalError):
        return False
    error_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(error_code, int) and error_code & 0xFF in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return True
    message = str(original).lower()
    return "database is locked" in message or "database table is locked" in message


def _synchronize_answer_keys_once(
    db: Session, manifests: Sequence[AnswerKeyManifest], answer_key_model: Any
) -> list[Any]:
    synchronized: list[AnswerKey] = []
    latest_versions: dict[str, int] = {}
    for manifest in manifests:
        latest_versions[manifest.template_id] = max(
            latest_versions.get(manifest.template_id, 0), manifest.version
        )
    for manifest in manifests:
        row = (
            db.query(answer_key_model)
            .filter(
                answer_key_model.template_id == manifest.template_id,
                answer_key_model.version == manifest.version,
            )
            .one_or_none()
        )
        values = _manifest_db_values(
            manifest, is_active=False
        )
        if row is None:
            row = answer_key_model(**values)
            db.add(row)
        else:
            _validate_immutable_answer_key(row, manifest)
        synchronized.append(row)

    db.flush()

    for template_id, latest_version in latest_versions.items():
        target = next(
            row
            for row in synchronized
            if row.template_id == template_id and row.version == latest_version
        )
        db.query(answer_key_model).filter(
            answer_key_model.template_id == template_id,
            answer_key_model.is_active.is_(True),
            answer_key_model.id != target.id,
        ).update({"is_active": False}, synchronize_session="fetch")
        target.is_active = True

    return synchronized


def _validate_immutable_answer_key(
    row: Any, manifest: AnswerKeyManifest
) -> None:
    expected_spec = [asdict(question) for question in manifest.questions]
    drifted = []
    for field, expected in (
        ("source_filename", manifest.source_filename),
        ("source_sha256", manifest.source_sha256),
        ("total_marks", manifest.total_marks),
        ("question_spec", expected_spec),
    ):
        actual = getattr(row, field)
        if field == "question_spec":
            matches = _canonical_json(actual) == _canonical_json(expected)
        else:
            matches = actual == expected
        if not matches:
            drifted.append(field)
    if drifted:
        identity = f"{manifest.template_id} version {manifest.version}"
        raise AnswerKeySynchronizationError(
            f"immutable answer-key drift for {identity}: {', '.join(drifted)}"
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _manifest_db_values(
    manifest: AnswerKeyManifest, *, is_active: bool
) -> dict[str, Any]:
    return {
        "name": manifest.template_id,
        "paper_type": manifest.template_id.rsplit("_", 1)[-1].upper(),
        "answers": {
            str(question.number): question.accepted_answers[0]
            for question in manifest.questions
        },
        "total_questions": len(manifest.questions),
        "template_id": manifest.template_id,
        "version": manifest.version,
        "source_filename": manifest.source_filename,
        "source_sha256": manifest.source_sha256,
        "total_marks": manifest.total_marks,
        "question_spec": [asdict(question) for question in manifest.questions],
        "is_active": is_active,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _template_question_types(template: ExamTemplate) -> dict[int, str]:
    resolved: dict[int, str] = {}
    for section in template.sections:
        for number in range(section.question_start, section.question_end + 1):
            section_type = section.question_overrides.get(number)
            raw_type = section_type.type if section_type else section.type
            if raw_type == "mcq_grid":
                resolved[number] = "mcq"
            elif raw_type == "numeric_grid":
                resolved[number] = "numeric"
            else:
                raise ManifestValidationError(
                    f"template question {number} has unsupported type {raw_type}"
                )
    return resolved


def _template_options(template: ExamTemplate, number: int) -> set[str]:
    for section in template.sections:
        if section.question_start <= number <= section.question_end:
            return set(section.grid.options if section.grid else ())
    return set()
