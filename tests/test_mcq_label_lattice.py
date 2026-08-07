from backend.services.mcq_label_lattice import _select_regular_row_groups


def _component(x: float, y: float, area: int = 100):
    return (x, y, 20, 30, area, int(x - 10), int(y - 15))


def test_regular_row_selection_ignores_instruction_example_lattice():
    answer_rows = [
        [_component(100 + col * 110, 1000 + row * 80) for col in range(5)]
        for row in range(20)
    ]
    # A filled mark can merge one real label component, while an A-E example
    # in the instructions below the grid still has all five glyphs.
    answer_rows[7] = answer_rows[7][:3]
    instruction_example = [
        _component(100 + col * 110, 1000 + 24 * 80) for col in range(5)
    ]

    selected = _select_regular_row_groups(
        [*answer_rows, instruction_example],
        n_rows=20,
        expected_pitch=80.0,
    )

    centers = [sum(component[1] for component in row) / len(row) for row in selected]
    assert centers == [1000 + row * 80 for row in range(20)]
