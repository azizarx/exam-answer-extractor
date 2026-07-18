import copy
import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

import backend.db.database as database_module
from backend.db.database import Base, _apply_lightweight_migrations
from backend.db.models import AnswerKey, CandidateMarking, MarkingRun
from backend.services.marking_service import ManifestRegistry
from backend.services.marking_service import (
    AnswerKeySynchronizationError,
    ManifestValidationError,
    MarkingService,
    normalize_integer,
    normalize_time_12_24,
    synchronize_answer_keys,
)
from backend.services.template_service import TemplateRegistry


ROOT = Path(__file__).resolve().parent.parent


def test_registry_loads_all_manifests_with_weighted_totals():
    registry = ManifestRegistry(
        ROOT / "answer_keys" / "structured",
        ROOT / "answer_keys",
        TemplateRegistry(ROOT / "backend" / "templates"),
    )

    manifests = registry.load_all()

    assert {manifest.template_id: manifest.total_marks for manifest in manifests} == {
        "seamo_2025_a": 100,
        "seamo_2025_b": 100,
        "seamo_2025_c": 100,
        "seamo_2025_d": 100,
        "seamo_2025_e": 100,
        "seamo_2025_f": 100,
        "seamo_2025_k": 50,
    }


def _registry() -> ManifestRegistry:
    return ManifestRegistry(
        ROOT / "answer_keys" / "structured",
        ROOT / "answer_keys",
        TemplateRegistry(ROOT / "backend" / "templates"),
    )


def test_weighted_marking_has_exact_auditable_output_without_answers():
    manifest = _registry().load(ROOT / "answer_keys/structured/seamo_2025_a.json")
    candidate_answers = {
        str(question.number): question.accepted_answers[0]
        for question in manifest.questions
    }

    result = MarkingService(manifest).mark(candidate_answers)

    assert (result.awarded_marks, result.max_marks, result.percentage) == (100, 100, 100.0)
    assert asdict(result.outcomes[0]) == {
        "question_number": 1,
        "status": "correct",
        "response": "C",
        "awarded_marks": 3,
        "max_marks": 3,
        "normalizer": "uppercase",
        "judge_source": "deterministic",
        "judge_verdict": None,
        "judge_reason": None,
    }
    assert asdict(result.outcomes[-1]) == {
        "question_number": 25,
        "status": "correct",
        "response": "19",
        "awarded_marks": 6,
        "max_marks": 6,
        "normalizer": "integer",
        "judge_source": "deterministic",
        "judge_verdict": None,
        "judge_reason": None,
    }
    assert "accepted_answers" not in asdict(result.outcomes[0])


def test_marking_classifies_wrong_blank_invalid_and_does_not_mutate_answers():
    manifest = _registry().load(ROOT / "answer_keys/structured/seamo_2025_a.json")
    answers = {"1": " d ", "2": "BL", "3": "", "21": "5.0", "22": "17:00", "23": "IN"}
    original = copy.deepcopy(answers)

    result = MarkingService(manifest).mark(answers)
    outcomes = {outcome.question_number: outcome for outcome in result.outcomes}

    assert answers == original
    assert outcomes[1].status == "incorrect"
    assert outcomes[1].response == " d "
    assert outcomes[2].status == "blank"
    assert outcomes[3].status == "blank"
    assert outcomes[21].status == "invalid"
    assert outcomes[22].status == "correct"
    assert outcomes[23].status == "invalid"
    assert (result.awarded_marks, result.percentage) == (6, 6.0)


@pytest.mark.parametrize(
    ("response", "status", "value"),
    [
        ("005", "valid", "5"),
        ("+005", "valid", "5"),
        ("-0", "valid", "0"),
        ("5.0", "invalid", None),
        ("1e2", "invalid", None),
        ("  ", "blank", None),
        ("9" * 5000, "invalid", None),
    ],
)
def test_integer_normalization_is_conservative(response, status, value):
    normalized = normalize_integer(response)
    assert (normalized.status, normalized.value) == (status, value)


@pytest.mark.parametrize(
    ("response", "status", "value"),
    [
        ("5:00 PM", "valid", "17:00"),
        ("17:00", "valid", "17:00"),
        ("12:00 AM", "valid", "00:00"),
        ("12:00 PM", "valid", "12:00"),
        ("24:00", "invalid", None),
        ("13:00 PM", "invalid", None),
    ],
)
def test_time_normalization_compares_12_and_24_hour_forms(response, status, value):
    normalized = normalize_time_12_24(response)
    assert (normalized.status, normalized.value) == (status, value)


def _write_mutated_manifest(tmp_path: Path, mutate) -> tuple[Path, Path]:
    source_dir = tmp_path / "answer_keys"
    manifests_dir = source_dir / "structured"
    manifests_dir.mkdir(parents=True)
    source = ROOT / "answer_keys/Paper A key.pdf"
    copied_source = source_dir / source.name
    copied_source.write_bytes(source.read_bytes())
    raw = json.loads((ROOT / "answer_keys/structured/seamo_2025_a.json").read_text())
    mutate(raw)
    path = manifests_dir / "key.json"
    path.write_text(json.dumps(raw))
    return path, source_dir


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw["questions"].__setitem__(1, raw["questions"][0]), "question numbers"),
        (lambda raw: raw.__setitem__("total_marks", 99), "do not sum"),
        (lambda raw: raw["questions"][0].__setitem__("normalizer", "integer"), "normalizer"),
        (lambda raw: raw["questions"][0].__setitem__("accepted_answers", ["Z"]), "domain"),
    ],
)
def test_malformed_manifests_are_rejected_before_activation(tmp_path, mutate, message):
    path, source_dir = _write_mutated_manifest(tmp_path, mutate)
    registry = ManifestRegistry(
        path.parent, source_dir, TemplateRegistry(ROOT / "backend/templates")
    )
    with pytest.raises(ManifestValidationError, match=message):
        registry.load(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.__setitem__("template_id", []),
        lambda raw: raw.__setitem__("version", {}),
        lambda raw: raw.__setitem__("total_marks", []),
        lambda raw: raw.__setitem__("source", []),
        lambda raw: raw["source"].__setitem__("filename", {}),
        lambda raw: raw["source"].__setitem__("sha256", []),
        lambda raw: raw.__setitem__("questions", {}),
        lambda raw: raw["questions"].__setitem__(0, []),
        lambda raw: raw["questions"][0].__setitem__("number", []),
        lambda raw: raw["questions"][0].__setitem__("type", []),
        lambda raw: raw["questions"][0].__setitem__("normalizer", {}),
        lambda raw: raw["questions"][0].__setitem__("marks", []),
        lambda raw: raw["questions"][0].__setitem__("accepted_answers", "C"),
        lambda raw: raw["questions"][0].__setitem__("accepted_answers", [[]]),
    ],
)
def test_all_malformed_scalar_and_container_shapes_raise_domain_error(tmp_path, mutate):
    path, source_dir = _write_mutated_manifest(tmp_path, mutate)
    registry = ManifestRegistry(
        path.parent, source_dir, TemplateRegistry(ROOT / "backend/templates")
    )

    with pytest.raises(ManifestValidationError):
        registry.load(path)


def test_source_hash_mismatch_is_rejected(tmp_path):
    path, source_dir = _write_mutated_manifest(
        tmp_path, lambda raw: raw["source"].__setitem__("sha256", "0" * 64)
    )
    registry = ManifestRegistry(
        path.parent, source_dir, TemplateRegistry(ROOT / "backend/templates")
    )
    with pytest.raises(ManifestValidationError, match="source hash mismatch"):
        registry.load(path)


def test_template_question_type_mismatch_is_rejected(tmp_path):
    path, source_dir = _write_mutated_manifest(
        tmp_path, lambda raw: raw["questions"][0].__setitem__("type", "numeric")
    )
    registry = ManifestRegistry(
        path.parent, source_dir, TemplateRegistry(ROOT / "backend/templates")
    )
    with pytest.raises(ManifestValidationError, match="must be mcq"):
        registry.load(path)


def test_synchronize_answer_keys_is_idempotent_and_preserves_provenance(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite'}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        first = synchronize_answer_keys(db, _registry())
        db.commit()
        second = synchronize_answer_keys(db, _registry())
        db.commit()

        rows = db.scalars(select(AnswerKey).order_by(AnswerKey.template_id)).all()
        assert len(first) == len(second) == len(rows) == 7
        assert all(row.version == 1 and row.is_active for row in rows)
        assert rows[0].source_filename == "Paper A key.pdf"
        assert rows[0].source_sha256 == hashlib.sha256(
            (ROOT / "answer_keys/Paper A key.pdf").read_bytes()
        ).hexdigest()
        assert rows[0].total_marks == 100
        assert len(rows[0].question_spec) == 25


def test_synchronization_rejects_immutable_version_drift_without_overwrite(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'drift.sqlite'}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        synchronize_answer_keys(db, _registry())
        db.commit()
        row = db.scalar(
            select(AnswerKey).where(AnswerKey.template_id == "seamo_2025_a")
        )
        row.source_filename = "different.pdf"
        db.commit()

        with pytest.raises(
            AnswerKeySynchronizationError, match="immutable answer-key drift"
        ):
            synchronize_answer_keys(db, _registry())

        db.expire_all()
        unchanged = db.get(AnswerKey, row.id)
        assert unchanged.source_filename == "different.pdf"
        assert unchanged.is_active is True


def test_database_enforces_one_active_answer_key_per_template(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'active.sqlite'}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        common = {
            "name": "test",
            "answers": {},
            "template_id": "template",
            "source_filename": "key.pdf",
            "source_sha256": "0" * 64,
            "total_marks": 1,
            "question_spec": [],
            "is_active": True,
        }
        db.add_all(
            [
                AnswerKey(version=1, **common),
                AnswerKey(version=2, **common),
            ]
        )
        with pytest.raises(IntegrityError):
            db.flush()


def test_synchronization_retries_transient_integrity_race(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'race.sqlite'}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        original_flush = db.flush
        calls = 0

        def race_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise IntegrityError("simulated concurrent insert", {}, Exception())
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(db, "flush", race_once)
        rows = synchronize_answer_keys(db, _registry())
        db.commit()

        assert calls >= 2
        assert len(rows) == 7
        assert db.scalar(select(AnswerKey).where(AnswerKey.is_active.is_(True))) is not None


def test_synchronization_retries_real_sqlite_writer_lock(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'locked.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 0.01},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=10")

    Base.metadata.create_all(engine)
    lock_acquired = threading.Event()
    lock_observed = threading.Event()
    blocker_errors = []

    @event.listens_for(engine, "handle_error")
    def observe_lock(context):
        if (
            isinstance(context.original_exception, sqlite3.OperationalError)
            and "locked" in str(context.original_exception).lower()
        ):
            lock_observed.set()

    def hold_writer_lock():
        try:
            with Session(engine) as blocker:
                blocker.add(
                    AnswerKey(
                        name="lock holder",
                        answers={},
                        template_id="lock_holder",
                        version=1,
                        is_active=False,
                    )
                )
                blocker.flush()
                lock_acquired.set()
                if not lock_observed.wait(timeout=2):
                    raise AssertionError("synchronizer never encountered the writer lock")
                blocker.commit()
        except BaseException as exc:
            blocker_errors.append(exc)
            lock_acquired.set()

    blocker = threading.Thread(target=hold_writer_lock)
    blocker.start()
    assert lock_acquired.wait(timeout=2)

    with Session(engine) as db:
        rows = synchronize_answer_keys(db, _registry())
        db.commit()
        persisted = db.scalar(
            select(func.count()).select_from(AnswerKey).where(
                AnswerKey.template_id.like("seamo_2025_%")
            )
        )

    blocker.join(timeout=2)
    assert not blocker.is_alive()
    assert blocker_errors == []
    assert lock_observed.is_set()
    assert len(rows) == persisted == 7


def test_synchronization_does_not_retry_unrelated_operational_error(
    tmp_path, monkeypatch
):
    engine = create_engine(f"sqlite:///{tmp_path / 'operational.sqlite'}")
    Base.metadata.create_all(engine)
    unrelated = OperationalError(
        "SELECT broken", {}, sqlite3.OperationalError("no such table: broken")
    )

    with Session(engine) as db:
        calls = 0

        def fail_flush(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise unrelated

        monkeypatch.setattr(db, "flush", fail_flush)
        with pytest.raises(OperationalError) as raised:
            synchronize_answer_keys(db, _registry())

    assert raised.value is unrelated
    assert calls == 1


def test_synchronization_preserves_versions_and_only_activates_latest(tmp_path):
    source_dir = tmp_path / "keys"
    manifests_dir = source_dir / "structured"
    manifests_dir.mkdir(parents=True)
    source = ROOT / "answer_keys/Paper A key.pdf"
    (source_dir / source.name).write_bytes(source.read_bytes())
    raw = json.loads((ROOT / "answer_keys/structured/seamo_2025_a.json").read_text())
    (manifests_dir / "v1.json").write_text(json.dumps(raw))
    raw["version"] = 2
    (manifests_dir / "v2.json").write_text(json.dumps(raw))
    registry = ManifestRegistry(
        manifests_dir, source_dir, TemplateRegistry(ROOT / "backend/templates")
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'versions.sqlite'}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        synchronize_answer_keys(db, registry)
        db.commit()
        rows = db.scalars(select(AnswerKey).order_by(AnswerKey.version)).all()

        assert [(row.version, row.is_active) for row in rows] == [(1, False), (2, True)]
        assert registry.get_or_raise("seamo_2025_a").version == 2


def test_lightweight_answer_key_migration_is_idempotent_on_legacy_sqlite(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE answer_keys ("
                "id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, answers JSON NOT NULL)"
            )
        )

    _apply_lightweight_migrations(engine)
    _apply_lightweight_migrations(engine)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("answer_keys")}
    indexes = {index["name"] for index in inspector.get_indexes("answer_keys")}
    assert {
        "template_id",
        "version",
        "source_filename",
        "source_sha256",
        "total_marks",
        "question_spec",
        "is_active",
    } <= columns
    assert {
        "ix_answer_keys_template_id",
        "ix_answer_keys_template_active",
        "uq_answer_keys_template_version",
        "uq_answer_keys_one_active_template",
    } <= indexes


def test_active_key_migration_reconciles_legacy_duplicates(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicate-active.sqlite'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE answer_keys ("
                "id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, answers JSON NOT NULL, "
                "template_id VARCHAR(100), version INTEGER, is_active BOOLEAN)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO answer_keys "
                "(id, name, answers, template_id, version, is_active) VALUES "
                "(1, 'v1', '{}', 'template', 1, TRUE), "
                "(2, 'v2', '{}', 'template', 2, TRUE)"
            )
        )

    _apply_lightweight_migrations(engine)
    _apply_lightweight_migrations(engine)

    with engine.connect() as connection:
        active_versions = connection.execute(
            text(
                "SELECT version FROM answer_keys "
                "WHERE template_id = 'template' AND is_active = TRUE"
            )
        ).scalars().all()
    assert active_versions == [2]
    assert "uq_answer_keys_one_active_template" in {
        index["name"] for index in inspect(engine).get_indexes("answer_keys")
    }


def test_processing_run_migration_reconciles_duplicates_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicate-processing.sqlite'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE marking_runs ("
                "id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL, "
                "status VARCHAR(20) NOT NULL, completed_at DATETIME, error_message TEXT)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO marking_runs (id, submission_id, status) VALUES "
                "(1, 10, 'processing'), (2, 10, 'processing'), "
                "(3, 11, 'processing')"
            )
        )

    _apply_lightweight_migrations(engine)
    _apply_lightweight_migrations(engine)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, status, completed_at, error_message "
                "FROM marking_runs ORDER BY id"
            )
        ).mappings().all()
    assert rows[0]["status"] == "failed"
    assert rows[0]["completed_at"] is not None
    assert rows[0]["error_message"] == "Marking interrupted; retry is available"
    assert [(row["id"], row["status"]) for row in rows[1:]] == [
        (2, "processing"),
        (3, "processing"),
    ]
    indexes = {item["name"] for item in inspect(engine).get_indexes("marking_runs")}
    assert "uq_marking_runs_one_processing_submission" in indexes


def test_postgresql_migrations_only_emit_missing_compatible_ddl_twice(monkeypatch):
    class FakePostgresEngine:
        dialect = postgresql.dialect()

        def __init__(self):
            self.tables = {
                "exam_submissions": {"id", "template_id"},
                "answer_keys": {"id", "name", "answers", "template_id"},
                "marking_runs": {
                    "id",
                    "submission_id",
                    "status",
                    "completed_at",
                    "error_message",
                },
            }
            self.indexes = {
                "answer_keys": {"ix_answer_keys_template_id"},
                "marking_runs": set(),
            }
            self.executed = []

        def begin(self):
            engine = self

            class Transaction:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    return False

                def execute(self, statement):
                    sql = str(statement.compile(dialect=engine.dialect))
                    engine.executed.append(sql)
                    tokens = sql.split()
                    if tokens[:3] == ["ALTER", "TABLE", "answer_keys"]:
                        engine.tables["answer_keys"].add(tokens[5])
                    elif tokens[:4] == ["CREATE", "UNIQUE", "INDEX", "IF"]:
                        engine.indexes[tokens[8]].add(tokens[6])
                    elif tokens[:3] == ["CREATE", "INDEX", "IF"]:
                        engine.indexes["answer_keys"].add(tokens[5])

            return Transaction()

    class FakeInspector:
        def __init__(self, engine):
            self.engine = engine

        def get_table_names(self):
            return list(self.engine.tables)

        def get_columns(self, table):
            return [{"name": name} for name in self.engine.tables[table]]

        def get_indexes(self, table):
            return [
                {"name": name} for name in self.engine.indexes.get(table, set())
            ]

    engine = FakePostgresEngine()
    monkeypatch.setattr(database_module, "inspect", lambda bind: FakeInspector(bind))

    _apply_lightweight_migrations(engine)
    first_run = list(engine.executed)
    _apply_lightweight_migrations(engine)
    second_run = engine.executed[len(first_run) :]

    assert len(first_run) == 12
    assert second_run == []
    assert not any("ADD COLUMN template_id" in sql for sql in first_run)
    assert not any("ix_answer_keys_template_id" in sql for sql in first_run)
    assert all(
        str(text(sql).compile(dialect=postgresql.dialect())) == sql
        for sql in first_run
    )
    assert any("question_spec JSON" in sql for sql in first_run)
    assert any("is_active BOOLEAN DEFAULT FALSE" in sql for sql in first_run)
    assert any(
        "uq_answer_keys_one_active_template" in sql
        and "WHERE is_active IS TRUE" in sql
        for sql in first_run
    )
    assert sum(sql.startswith("UPDATE answer_keys") for sql in first_run) == 1
    assert sum(sql.startswith("UPDATE marking_runs") for sql in first_run) == 1
    assert any(
        "uq_marking_runs_one_processing_submission" in sql
        and "WHERE status = 'processing'" in sql
        for sql in first_run
    )


def test_audit_models_expose_run_provenance_and_weighted_candidate_results():
    assert MarkingRun.__table__.c.answer_key_id.nullable
    assert {"submission_id", "answer_key_id", "status", "key_provenance", "error_message"} <= {
        column.name for column in MarkingRun.__table__.columns
    }
    assert {
        "marking_run_id",
        "candidate_result_id",
        "awarded_marks",
        "max_marks",
        "percentage",
        "outcomes",
    } <= {column.name for column in CandidateMarking.__table__.columns}
    processing_index = next(
        index
        for index in MarkingRun.__table__.indexes
        if index.name == "uq_marking_runs_one_processing_submission"
    )
    assert processing_index.unique is True
    assert str(processing_index.dialect_options["sqlite"]["where"]) == (
        "status = 'processing'"
    )
    assert str(processing_index.dialect_options["postgresql"]["where"]) == (
        "status = 'processing'"
    )
