import unittest

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
