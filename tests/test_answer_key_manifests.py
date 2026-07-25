from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
ANSWER_KEYS = ROOT / "answer_keys"
STRUCTURED_KEYS = ANSWER_KEYS / "structured"
PAPERS = ("a", "b", "c", "d", "e", "f", "k")
INTEGER_PATTERN = re.compile(r"-?(?:0|[1-9]\d*)")
EXPECTED_ANSWERS = {
    "a": (
        "C B B B C C E E A C E C C A E E E D D D 5".split()
        + ["5:00 PM"]
        + "121 9 19".split()
    ),
    "b": "A C A E E D A D B C C B D E D C B C E A 11 7 160 5101 24".split(),
    "c": "E E B C D C A E C A E B B C A E D C E B 6 10 30 16 23".split(),
    "d": "B C B E D E C C E C D C A B D B C B C E 7 509 540 4675 56".split(),
    "e": "E C A D B B A E A C B C C E B D E B A D 18 10 2024 30 2".split(),
    "f": "E D A D B E D E C C B C A C A E A B B D 75 15 2 221 36".split(),
    "k": "B C B B A C A C B C C A A B A".split(),
}


def _load_manifest(paper: str) -> dict:
    path = STRUCTURED_KEYS / f"seamo_2025_{paper}.json"
    with path.open(encoding="utf-8") as manifest_file:
        return json.load(manifest_file)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_manifest_rejects_scalar_accepted_answers():
    manifest = _load_manifest("a")
    manifest["questions"][0]["accepted_answers"] = "C"

    with pytest.raises(AssertionError, match="accepted_answers must be a list"):
        _assert_valid_manifest("a", manifest)


def test_manifest_rejects_valid_looking_answer_substitution():
    manifest = _load_manifest("b")
    manifest["questions"][0]["accepted_answers"] = ["B"]

    with pytest.raises(AssertionError):
        _assert_valid_manifest("b", manifest)


def _assert_valid_manifest(paper: str, manifest: dict) -> None:
    paper_label = paper.upper()
    expected_count = 15 if paper == "k" else 25
    expected_total = 50 if paper == "k" else 100
    source_pdf = ANSWER_KEYS / f"Paper {paper_label} key.pdf"

    assert manifest["template_id"] == f"seamo_2025_{paper}"
    assert isinstance(manifest["version"], int) and manifest["version"] >= 1
    assert manifest["source"] == {
        "filename": source_pdf.name,
        "sha256": _sha256(source_pdf),
    }
    assert manifest["total_marks"] == expected_total

    questions = manifest["questions"]
    assert [question["number"] for question in questions] == list(
        range(1, expected_count + 1)
    )
    assert sum(question["marks"] for question in questions) == expected_total

    for question in questions:
        number = question["number"]
        expected_marks = 3 if number <= 10 else 4 if number <= 20 else 6
        assert question["marks"] == expected_marks
        accepted_answers = question["accepted_answers"]
        assert isinstance(
            accepted_answers, list
        ), "accepted_answers must be a list"
        assert accepted_answers
        assert all(isinstance(answer, str) for answer in accepted_answers)

        if paper == "a" and number == 22:
            assert question["type"] == "time"
            assert question["normalizer"] == "time_12_24"
            assert accepted_answers == ["5:00 PM"]
        elif number <= 20:
            allowed_answers = set("ABC") if paper == "k" else set("ABCDE")
            assert question["type"] == "mcq"
            assert question["normalizer"] == "uppercase"
            assert set(accepted_answers) <= allowed_answers
        else:
            assert question["type"] == "numeric"
            assert question["normalizer"] == "integer"
            assert all(INTEGER_PATTERN.fullmatch(answer) for answer in accepted_answers)

    assert [question["accepted_answers"] for question in questions] == [
        [answer] for answer in EXPECTED_ANSWERS[paper]
    ]


@pytest.mark.parametrize("paper", PAPERS)
def test_structured_answer_key_manifest_matches_source_and_scoring(paper: str):
    _assert_valid_manifest(paper, _load_manifest(paper))
