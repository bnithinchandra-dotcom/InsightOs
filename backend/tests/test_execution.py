import unittest
from concurrent.futures import ThreadPoolExecutor

import pyarrow as pa

from app.execution import (
    ExecutionBudget,
    ExecutionBudgetExceeded,
    estimate_decoded_python_string_bytes,
)


class ExecutionBudgetTests(unittest.TestCase):
    def test_consumption_tracks_used_and_remaining_resources(self):
        budget = ExecutionBudget({"rows": 4, "bytes": 8})
        budget.consume("rows", 3)
        budget.consume("bytes", 5)
        self.assertEqual(budget.used("rows"), 3)
        self.assertEqual(budget.remaining("rows"), 1)
        self.assertEqual(budget.used("bytes"), 5)
        self.assertEqual(budget.remaining("bytes"), 3)

    def test_exceeding_a_limit_does_not_consume_the_failed_amount(self):
        budget = ExecutionBudget({"rows": 2})
        budget.consume("rows", 2)
        with self.assertRaises(ExecutionBudgetExceeded):
            budget.consume("rows")
        self.assertEqual(budget.used("rows"), 2)

    def test_invalid_limits_and_consumption_are_rejected(self):
        for limits in ({}, {"rows": True}, {"rows": -1}, {"": 1}):
            with self.subTest(limits=limits):
                with self.assertRaises(ValueError):
                    ExecutionBudget(limits)
        budget = ExecutionBudget({"rows": 1})
        for resource, amount in (("unknown", 1), ("rows", -1), ("rows", True)):
            with self.subTest(resource=resource, amount=amount):
                with self.assertRaises(ValueError):
                    budget.consume(resource, amount)

    def test_child_budget_charges_parent_once_and_keeps_child_scope(self):
        root = ExecutionBudget({"comparisons": 8})
        child = root.child({"comparisons": 3})
        child.consume("comparisons", 2)
        root.consume("comparisons", 2)

        self.assertEqual(root.used("comparisons"), 4)
        self.assertEqual(child.used("comparisons"), 2)
        self.assertEqual(child.remaining("comparisons"), 1)

        with self.assertRaises(ExecutionBudgetExceeded):
            child.consume("comparisons", 2)
        self.assertEqual(root.used("comparisons"), 4)
        self.assertEqual(child.used("comparisons"), 2)

    def test_child_cannot_exceed_or_invent_parent_resources(self):
        budget = ExecutionBudget({"rows": 5})
        for limits in ({"rows": 6}, {"bytes": 1}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                budget.child(limits)

    def test_reservations_are_visible_and_cancelled_or_committed_atomically(self):
        root = ExecutionBudget({"bytes": 10})
        child = root.child({"bytes": 8})
        reservation = child.reserve("bytes", 6)
        self.assertEqual(root.remaining("bytes"), 4)
        self.assertEqual(root.used("bytes"), 0)
        reservation.cancel()
        self.assertEqual(root.remaining("bytes"), 10)

        committed = child.reserve("bytes", 6)
        committed.commit(4)
        self.assertEqual(root.used("bytes"), 4)
        self.assertEqual(child.used("bytes"), 4)
        self.assertEqual(root.remaining("bytes"), 6)

        with self.assertRaises(ExecutionBudgetExceeded):
            child.reserve("bytes", 5)

    def test_concurrent_consumption_is_atomic_and_independent_budgets_are_isolated(self):
        shared = ExecutionBudget({"work": 50})
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _index: self._consume_one(shared),
                    range(100),
                )
            )
        self.assertEqual(sum(results), 50)
        self.assertEqual(shared.used("work"), 50)

        first = ExecutionBudget({"work": 1})
        second = ExecutionBudget({"work": 1})
        first.consume("work")
        second.consume("work")
        self.assertEqual(first.used("work"), 1)
        self.assertEqual(second.used("work"), 1)

    @staticmethod
    def _consume_one(budget):
        try:
            budget.consume("work")
        except ExecutionBudgetExceeded:
            return 0
        return 1

    def test_string_estimate_accounts_for_dictionary_payload_and_expanded_rows(self):
        values = pa.DictionaryArray.from_arrays(
            pa.array([0, 0, None, 1], type=pa.int8()),
            pa.array(["alpha", "beta"]),
        )
        self.assertEqual(
            estimate_decoded_python_string_bytes(values),
            14 + 4 * 8 + 3 * 72 + 64,
        )

    def test_string_estimate_accumulates_arrow_chunks(self):
        first = pa.array(["a", "a"])
        second = pa.array(["bc"])
        values = pa.chunked_array([first, second])
        expected = (2 + 2 * 8 + 2 * 72 + 64) + (2 + 8 + 72 + 64)
        self.assertEqual(estimate_decoded_python_string_bytes(values), expected)


if __name__ == "__main__":
    unittest.main()
