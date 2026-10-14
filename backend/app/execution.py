from __future__ import annotations

from collections.abc import Mapping

import pyarrow as pa
import pyarrow.compute as pc


class ExecutionBudgetExceeded(ValueError):
    def __init__(self, resource: str, limit: int, used: int, requested: int):
        super().__init__(f"The {resource} execution budget was exceeded.")
        self.resource = resource
        self.limit = limit
        self.used = used
        self.requested = requested


class ExecutionBudget:
    def __init__(self, limits: Mapping[str, int]):
        if not isinstance(limits, Mapping) or not limits:
            raise ValueError("Execution budgets need at least one resource limit.")
        self._limits = {}
        self._used = {}
        for resource, limit in limits.items():
            if (
                not isinstance(resource, str)
                or not resource
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or limit < 0
            ):
                raise ValueError("Execution budget limits must be named non-negative integers.")
            self._limits[resource] = limit
            self._used[resource] = 0

    def consume(self, resource: str, amount: int = 1) -> None:
        if (
            not isinstance(resource, str)
            or resource not in self._limits
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
        ):
            raise ValueError("Execution budget consumption is invalid.")
        next_used = self._used[resource] + amount
        limit = self._limits[resource]
        if next_used > limit:
            raise ExecutionBudgetExceeded(resource, limit, self._used[resource], amount)
        self._used[resource] = next_used

    def used(self, resource: str) -> int:
        return self._used[resource]

    def remaining(self, resource: str) -> int:
        return self._limits[resource] - self._used[resource]


def estimate_decoded_python_string_bytes(
    values: pa.Array | pa.ChunkedArray,
) -> int:
    total_bytes = 0
    chunks = values.chunks if isinstance(values, pa.ChunkedArray) else (values,)
    for chunk in chunks:
        value_type = chunk.type
        if pa.types.is_dictionary(value_type):
            value_array = chunk.dictionary
            lengths = pc.binary_length(value_array)
            row_lengths = pc.take(lengths, chunk.indices)
            payload_bytes = pc.sum(row_lengths).as_py() or 0
        elif pa.types.is_string(value_type) or pa.types.is_large_string(value_type):
            payload_bytes = pc.sum(pc.binary_length(chunk)).as_py() or 0
        else:
            continue

        valid_values = len(chunk) - chunk.null_count
        total_bytes += payload_bytes + len(chunk) * 8 + valid_values * 72 + 64
    return total_bytes
