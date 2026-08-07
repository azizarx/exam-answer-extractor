from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
KEY_DIR = ROOT / "answer_keys" / "seamo_x_2026"
PAPERS = ("a", "b", "c", "d", "e", "f", "k")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("paper", PAPERS)
def test_seamo_x_manifest_matches_pdf_and_weighting(paper: str):
    path = KEY_DIR / f"seamo_x_2026_{paper}.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    source = ROOT / "answer_keys" / manifest["source"]["filename"]
    expected_count = 15 if paper != "a" else 20
    expected_total = 50 if paper == "k" else 100

    assert manifest["template_id"] == f"seamo_x_2026_{paper}"
    assert source.is_file()
    assert manifest["source"]["sha256"] == _sha256(source)
    assert manifest["total_marks"] == expected_total
    assert [q["number"] for q in manifest["questions"]] == list(
        range(1, expected_count + 1)
    )
    assert sum(q["marks"] for q in manifest["questions"]) == expected_total
    assert all(q["accepted_answers"] for q in manifest["questions"])


def test_seamo_x_reconciles_conflicting_c_and_d_pdfs_explicitly():
    c = json.loads((KEY_DIR / "seamo_x_2026_c.json").read_text())
    d = json.loads((KEY_DIR / "seamo_x_2026_d.json").read_text())
    c_questions = {q["number"]: q for q in c["questions"]}
    d_questions = {q["number"]: q for q in d["questions"]}

    assert c_questions[1]["accepted_answers"] == ["4"]
    assert set(c_questions[5]["accepted_answers"]) == {"7/2", "3 1/2", "3.5"}
    assert d_questions[1]["accepted_answers"] == ["6048"]
    assert set(d_questions[2]["accepted_answers"]) == {
        "E",
        "(E)",
        "circled E",
        "2025/2026",
        "(E) 2025/2026",
        "2025/2026; circled E",
    }
    assert "(a+b)(b+c)(c-a)" in d_questions[7]["accepted_answers"]
    assert "additional_source" in c["reconciliation"]
    assert "additional_source" in d["reconciliation"]


def test_seamo_x_diagram_questions_are_not_encoded_as_blanks():
    a = json.loads((KEY_DIR / "seamo_x_2026_a.json").read_text())
    b = json.loads((KEY_DIR / "seamo_x_2026_b.json").read_text())
    a_questions = {q["number"]: q for q in a["questions"]}
    b_questions = {q["number"]: q for q in b["questions"]}

    assert {q for q, spec in a_questions.items() if spec["type"] == "diagram"} == {
        4,
        6,
        9,
    }
    assert b_questions[5]["type"] == "diagram"
    assert all(a_questions[q]["accepted_answers"][0] for q in (4, 6, 9))
    assert b_questions[5]["accepted_answers"][0]
    assert a["version"] == b["version"] == 2
    assert "12 equal sectors" in a_questions[9]["accepted_answers"][0]
    assert "8 equal sectors" not in a_questions[9]["accepted_answers"][0]
    assert "preprinted triangular network" in b_questions[5]["accepted_answers"][0]
    assert "edges" not in b_questions[5]["accepted_answers"][0]
