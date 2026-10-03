import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
from run_eval import normalize, results_match


def test_ignores_column_and_row_order_and_int_float():
    assert normalize([("a", 1), ("b", 2)], ordered=False) == normalize([(2.0, "b"), (1, "a")], ordered=False)


def test_ordered_respects_row_order():
    assert normalize([("a", 1), ("b", 2)], ordered=True) != normalize([("b", 2), ("a", 1)], ordered=True)


def test_extra_columns_allowed():
    got = [(1, "Retail", 8075.02), (2, "Wholesale", 7807.01)]
    gold = [("Wholesale", 7807.01), ("Retail", 8075.02)]
    assert results_match(got, gold, ordered=False)


def test_wrong_values_still_fail():
    assert not results_match([("Retail", 1.0)], [("Retail", 2.0)], ordered=False)
