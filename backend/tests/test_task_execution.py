import threading
import time
import unittest
from unittest.mock import patch

from app.agent.tools import StrictArguments, ToolDefinition, ToolRegistry
from app.task_execution import (
    CapabilityRegistry,
    ExecutionTarget,
    TaskInput,
    TaskExecutionFailure,
    TaskRequirements,
    backend_agent_registry,
    run_task,
    select_execution_target,
)


def target(
    target_id,
    kind,
    *,
    capabilities=("read",),
    classifications=("public", "internal", "sensitive"),
    available=True,
    memory_bytes=None,
    supports_cancellation=False,
    supports_timeout=False,
):
    return ExecutionTarget(
        target_id=target_id,
        kind=kind,
        capabilities=frozenset(capabilities),
        data_classifications=frozenset(classifications),
        available=available,
        memory_bytes=memory_bytes,
        supports_cancellation=supports_cancellation,
        supports_timeout=supports_timeout,
    )


class TaskExecutionTests(unittest.TestCase):
    def test_registry_discovers_targets_and_rejects_duplicate_ids(self):
        browser = target("browser", "browser")
        registry = CapabilityRegistry((browser,))
        self.assertEqual(registry.targets(), (browser,))
        with self.assertRaises(ValueError):
            registry.register(browser)

    def test_placement_prefers_local_when_capable_and_permitted(self):
        requirements = TaskRequirements(("read",), data_classification="public")
        decision = select_execution_target(
            requirements,
            (
                target("backend", "backend"),
                target("local", "browser"),
            ),
        )
        self.assertEqual(decision.target.target_id, "local")
        self.assertIn("supports the required capabilities", decision.rationale)

    def test_backend_locality_wins_over_local_preference(self):
        requirements = TaskRequirements(
            ("read",),
            data_locality="backend",
            data_classification="internal",
        )
        decision = select_execution_target(
            requirements,
            (target("browser", "browser"), target("backend", "backend")),
        )
        self.assertEqual(decision.target.target_id, "backend")
        self.assertIn("matches data locality", decision.rationale)

    def test_local_only_data_never_falls_back_to_backend(self):
        requirements = TaskRequirements(
            ("read",),
            data_locality="browser",
            data_classification="local_only",
        )
        decision = select_execution_target(
            requirements,
            (target("backend", "backend", classifications=("local_only",)),),
        )
        self.assertIsNone(decision.target)
        self.assertEqual(decision.error_code, "no_execution_target")

    def test_data_transfer_requires_explicit_permission(self):
        targets = (
            target("backend", "backend"),
        )
        requirements = TaskRequirements(
            ("read",),
            data_locality="browser",
            data_classification="internal",
        )
        self.assertIsNone(select_execution_target(requirements, targets).target)
        permitted = TaskRequirements(
            ("read",),
            data_locality="browser",
            data_classification="internal",
            allow_data_transfer=True,
        )
        self.assertEqual(
            select_execution_target(permitted, targets).target.target_id,
            "backend",
        )

        backend_data = TaskRequirements(
            ("read",),
            data_locality="backend",
            data_classification="internal",
        )
        local_runtime = target(
            "local-runtime",
            "local_runtime",
            classifications=("internal",),
        )
        self.assertIsNone(
            select_execution_target(backend_data, (local_runtime,)).target
        )

    def test_unsupported_unavailable_memory_and_timeout_requirements_fail_closed(self):
        requirements = TaskRequirements(
            ("read", "gpu"),
            data_classification="public",
        )
        decision = select_execution_target(requirements, (target("worker", "browser"),))
        self.assertIsNone(decision.target)

        requirements = TaskRequirements(
            ("read",),
            data_classification="public",
            minimum_memory_bytes=1024,
        )
        decision = select_execution_target(
            requirements,
            (target("unknown-memory", "browser"),),
        )
        self.assertIsNone(decision.target)

        requirements = TaskRequirements(
            ("read",),
            data_classification="public",
            timeout_seconds=1,
            require_cancellation=True,
        )
        decision = select_execution_target(
            requirements,
            (
                target(
                    "backend",
                    "backend",
                    supports_timeout=True,
                    supports_cancellation=False,
                ),
            ),
        )
        self.assertIsNone(decision.target)

    def test_task_executes_and_validates_bounded_results(self):
        result = run_task(
            task=TaskInput("small", {"request": "summary"}),
            requirements=TaskRequirements(
                ("readonly_agent_tool",),
                data_locality="backend",
                data_classification="internal",
                allowed_targets=("backend",),
                prefer_local=False,
                max_result_bytes=100,
            ),
            registry=backend_agent_registry(),
            operation=lambda context, payload: {"rows": 2, "request": payload["request"]},
            result_validator=lambda value: isinstance(value, dict),
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.target_id, "backend")
        self.assertEqual(result.progress, 100)
        self.assertEqual(result.result, {"rows": 2, "request": "summary"})

        oversized = run_task(
            task=TaskInput("oversized", {}),
            requirements=TaskRequirements(
                ("readonly_agent_tool",),
                data_locality="backend",
                data_classification="internal",
                allowed_targets=("backend",),
                prefer_local=False,
                max_result_bytes=4,
            ),
            registry=backend_agent_registry(),
            operation=lambda context, _payload: {"rows": 200},
            result_validator=lambda value: isinstance(value, dict),
        )
        self.assertEqual(oversized.error_code, "result_too_large")

    def test_cancellation_timeout_invalid_result_and_failure_are_explicit(self):
        cancellation = threading.Event()
        cancellation.set()
        cancelled = run_task(
            task=TaskInput("cancelled", {}),
            requirements=TaskRequirements(("read",), data_classification="public"),
            registry=CapabilityRegistry((target(
                "browser",
                "browser",
                supports_cancellation=True,
            ),)),
            operation=lambda context, _payload: {"ok": True},
            result_validator=lambda value: True,
            cancellation=cancellation,
        )
        self.assertEqual(cancelled.status, "cancelled")

        timed_out = run_task(
            task=TaskInput("timeout", {}),
            requirements=TaskRequirements(
                ("read",),
                data_classification="public",
                timeout_seconds=0.01,
            ),
            registry=CapabilityRegistry((target(
                "browser",
                "browser",
                supports_timeout=True,
            ),)),
            operation=lambda context, _payload: (
                time.sleep(0.02),
                context.checkpoint(),
                {},
            )[-1],
            result_validator=lambda value: True,
        )
        self.assertEqual(timed_out.status, "timed_out")

        invalid = run_task(
            task=TaskInput("invalid", {}),
            requirements=TaskRequirements(("read",), data_classification="public"),
            registry=CapabilityRegistry((target("browser", "browser"),)),
            operation=lambda context, _payload: {"ok": False},
            result_validator=lambda value: value.get("ok") is True,
        )
        self.assertEqual(invalid.error_code, "invalid_execution_result")

        failed = run_task(
            task=TaskInput("failure", {}),
            requirements=TaskRequirements(("read",), data_classification="public"),
            registry=CapabilityRegistry((target("browser", "browser"),)),
            operation=lambda context, _payload: (_ for _ in ()).throw(
                RuntimeError("secret")
            ),
            result_validator=lambda value: True,
        )
        self.assertEqual(failed.error_code, "task_execution_failed")
        self.assertNotIn("secret", failed.error_message)

    def test_typed_task_failure_preserves_domain_error(self):
        failed = run_task(
            task=TaskInput("bounded-analysis", {}),
            requirements=TaskRequirements(("read",), data_classification="public"),
            registry=CapabilityRegistry((target("browser", "browser"),)),
            operation=lambda _context, _payload: (_ for _ in ()).throw(
                TaskExecutionFailure("row_limit", "The analysis row limit was exceeded.")
            ),
            result_validator=lambda _value: True,
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "row_limit")
        self.assertEqual(
            failed.error_message,
            "The analysis row limit was exceeded.",
        )

    def test_existing_agent_tool_runs_through_backend_placement_boundary(self):
        with patch("app.main.list_projects", return_value=[{"id": 4, "name": "Orders"}]):
            output, result, _label = ToolRegistry().execute("list_projects", {})

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.data, [{"id": 4, "name": "Orders"}])
        self.assertEqual(result.references[-1]["target"], "backend")
        self.assertIn("matches data locality", result.references[-1]["rationale"])
        self.assertIn('"execution_placement"', output)

    def test_agent_operation_fails_explicitly_when_backend_target_is_unavailable(self):
        calls = []
        registry = CapabilityRegistry((target(
            "offline-backend",
            "backend",
            available=False,
            capabilities=("readonly_agent_tool",),
            classifications=("internal",),
        ),))
        definition = ToolDefinition(
            "list_projects",
            "Listing projects",
            "List projects.",
            StrictArguments,
            lambda _arguments: calls.append(True),
        )
        _output, result, _label = ToolRegistry(
            (definition,),
            execution_targets=registry,
        ).execute("list_projects", {})
        self.assertEqual(result.error_code, "no_execution_target")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
