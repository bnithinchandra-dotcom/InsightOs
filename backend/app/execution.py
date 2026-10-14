from __future__ import annotations

from collections.abc import Mapping
from threading import RLock

import pyarrow as pa
import pyarrow.compute as pc


class ExecutionBudgetExceeded(ValueError):
    def __init__(self, resource: str, limit: int, used: int, requested: int):
        super().__init__(f"The {resource} execution budget was exceeded.")
        self.resource = resource
        self.limit = limit
        self.used = used
        self.requested = requested


class ExecutionBudgetReservation:
    def __init__(
        self,
        budget: ExecutionBudget,
        resource: str,
        amount: int,
        chain: tuple[ExecutionBudget, ...],
    ):
        self._budget = budget
        self._resource = resource
        self._amount = amount
        self._chain = chain
        self._closed = False

    def commit(self, amount: int | None = None) -> None:
        committed = self._amount if amount is None else amount
        if (
            isinstance(committed, bool)
            or not isinstance(committed, int)
            or not 0 <= committed <= self._amount
        ):
            raise ValueError("Execution budget reservation completion is invalid.")
        with self._budget._lock:
            if self._closed:
                raise ValueError("Execution budget reservation is already complete.")
            for scoped_budget in self._chain:
                scoped_budget._reserved[self._resource] -= self._amount
                scoped_budget._used[self._resource] += committed
            self._closed = True

    def cancel(self) -> None:
        with self._budget._lock:
            if self._closed:
                return
            for scoped_budget in self._chain:
                scoped_budget._reserved[self._resource] -= self._amount
            self._closed = True


class ExecutionBudget:
    def __init__(
        self,
        limits: Mapping[str, int],
        *,
        parent: ExecutionBudget | None = None,
    ):
        if not isinstance(limits, Mapping) or not limits:
            raise ValueError("Execution budgets need at least one resource limit.")
        self._limits: dict[str, int] = {}
        self._used: dict[str, int] = {}
        self._reserved: dict[str, int] = {}
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
            self._reserved[resource] = 0
        if parent is not None and (
            not isinstance(parent, ExecutionBudget)
            or any(
                resource not in parent._limits
                or limit > parent._limits[resource]
                for resource, limit in self._limits.items()
            )
        ):
            raise ValueError(
                "Child execution budgets must use resources and limits declared by their parent."
            )
        self._parent = parent
        self._root = parent._root if parent is not None else self
        self._lock = self._root._lock if parent is not None else RLock()

    def child(self, limits: Mapping[str, int]) -> ExecutionBudget:
        """Create a scoped child whose consumption is charged to every ancestor."""
        return ExecutionBudget(limits, parent=self)

    def consume(self, resource: str, amount: int = 1) -> None:
        chain = self._resource_chain(resource, amount)
        with self._lock:
            for scoped_budget in chain:
                next_used = scoped_budget._used[resource] + scoped_budget._reserved[resource] + amount
                limit = scoped_budget._limits[resource]
                if next_used > limit:
                    raise ExecutionBudgetExceeded(
                        resource, limit, scoped_budget._used[resource], amount
                    )
            for scoped_budget in chain:
                scoped_budget._used[resource] += amount

    def reserve(self, resource: str, amount: int) -> ExecutionBudgetReservation:
        chain = self._resource_chain(resource, amount)
        with self._lock:
            for scoped_budget in chain:
                next_used = scoped_budget._used[resource] + scoped_budget._reserved[resource] + amount
                limit = scoped_budget._limits[resource]
                if next_used > limit:
                    raise ExecutionBudgetExceeded(
                        resource, limit, scoped_budget._used[resource], amount
                    )
            for scoped_budget in chain:
                scoped_budget._reserved[resource] += amount
        return ExecutionBudgetReservation(self, resource, amount, tuple(chain))

    def _resource_chain(
        self,
        resource: str,
        amount: int,
    ) -> list[ExecutionBudget]:
        if (
            not isinstance(resource, str)
            or not resource
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
        ):
            raise ValueError("Execution budget consumption is invalid.")
        chain = []
        budget: ExecutionBudget | None = self
        while budget is not None:
            if resource not in budget._limits:
                raise ValueError("Execution budget consumption is invalid.")
            chain.append(budget)
            budget = budget._parent
        return chain

    def used(self, resource: str) -> int:
        with self._lock:
            return self._used[resource]

    def remaining(self, resource: str) -> int:
        with self._lock:
            return (
                self._limits[resource]
                - self._used[resource]
                - self._reserved[resource]
            )

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                resource: {
                    "limit": limit,
                    "used": self._used[resource],
                    "reserved": self._reserved[resource],
                    "remaining": (
                        limit
                        - self._used[resource]
                        - self._reserved[resource]
                    ),
                }
                for resource, limit in self._limits.items()
            }


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
