from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.agent.contracts import ToolResult


logger = logging.getLogger(__name__)

PreparationStatus = Literal["available", "unavailable", "failed", "bounded_out"]


class _PreparationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PreparationLookup(_PreparationModel):
    status: PreparationStatus
    summary: StrictStr
    data: dict[str, Any] | list[Any] | None = None
    warnings: list[StrictStr] = Field(default_factory=list)
    error_code: StrictStr | None = None


class PreparationHandoffContext(_PreparationModel):
    workflow_id: StrictStr
    dataset_id: StrictInt
    file_id: StrictInt
    source_fingerprint: StrictStr
    operation: StrictStr
    evidence_references: list[dict[str, Any]]
    previous_stage: dict[str, Any] | None = None


class SpecialistPreparationArtifact(_PreparationModel):
    contract_version: Literal[1]
    prepared_for: Literal["analytics_modelling"]
    workflow_id: StrictStr
    dataset_id: StrictInt
    file_id: StrictInt
    source_fingerprint: StrictStr
    inventory: dict[str, Any]
    dataset_inventory: PreparationLookup
    schema_metadata: dict[str, Any]
    inspection_handoff: PreparationLookup
    quality_results: PreparationLookup
    eda_results: PreparationLookup
    analytics_outputs: list[dict[str, Any]]
    provenance: list[dict[str, Any]]
    tool_capabilities: list[dict[str, Any]]
    handoff_context: PreparationHandoffContext
    llm_calls: Literal[0] = 0


class PreparationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SpecialistPreparationPlanner:
    """Prepares bounded, deterministic context from existing workflow evidence."""

    def __init__(self, *, max_workers: int = 2, max_in_flight: int = 4) -> None:
        if max_workers < 1 or max_in_flight < max_workers:
            raise ValueError("Preparation concurrency limits are invalid.")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="agent-preparation",
        )
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._closed = False

    def prepare_async(
        self,
        *,
        registry: Any,
        workflow_id: str,
        dataset_id: int,
        file_id: int,
        source_fingerprint: str,
        operation: str,
        inspection: dict[str, Any],
        evidence_references: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        analytics_outputs: list[dict[str, Any]] | None = None,
    ) -> Future[SpecialistPreparationArtifact]:
        if self._closed:
            raise PreparationError(
                "agent_preparation_closed",
                "The specialist preparation service is no longer available.",
            )
        if not self._slots.acquire(blocking=False):
            raise PreparationError(
                "agent_preparation_busy",
                "The bounded specialist preparation capacity is currently full.",
            )
        try:
            future = self._executor.submit(
                self.prepare,
                registry=registry,
                workflow_id=workflow_id,
                dataset_id=dataset_id,
                file_id=file_id,
                source_fingerprint=source_fingerprint,
                operation=operation,
                inspection=inspection,
                evidence_references=evidence_references,
                context=context,
                analytics_outputs=analytics_outputs,
            )
        except RuntimeError as error:
            self._slots.release()
            raise PreparationError(
                "agent_preparation_unavailable",
                "The specialist preparation task could not be scheduled.",
            ) from error
        future.add_done_callback(lambda _: self._slots.release())
        return future

    def prepare(
        self,
        *,
        registry: Any,
        workflow_id: str,
        dataset_id: int,
        file_id: int,
        source_fingerprint: str,
        operation: str,
        inspection: dict[str, Any],
        evidence_references: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        analytics_outputs: list[dict[str, Any]] | None = None,
    ) -> SpecialistPreparationArtifact:
        file_data = inspection.get("file")
        file_data = file_data if isinstance(file_data, dict) else {}
        profile = inspection.get("profile")
        profile = profile if isinstance(profile, dict) else {}
        tables = profile.get("tables")
        schema_metadata = {
            "tables": tables[:12] if isinstance(tables, list) else [],
            "profile_version": profile.get("version"),
        }
        inventory = {
            "dataset_id": dataset_id,
            "file_id": file_id,
            "filename": file_data.get("filename"),
            "status": file_data.get("status"),
            "tables": file_data.get("tables", []),
        }
        scoped_context = dict(context or {})
        scoped_context.update(
            {
                "dataset_id": dataset_id,
                "user_message": scoped_context.get("user_message", ""),
            }
        )
        dataset_inventory = self._read_existing(
            registry,
            "list_files",
            {"dataset_id": dataset_id},
            scoped_context,
        )
        handoff = self._read_existing(
            registry,
            "get_inspection_handoff",
            {"dataset_id": dataset_id, "file_id": file_id},
            scoped_context,
            max_bytes=12 * 1024,
        )
        quality = self._read_existing(
            registry,
            "get_quality_report",
            {"dataset_id": dataset_id, "file_id": file_id},
            scoped_context,
        )
        eda = self._read_existing(
            registry,
            "list_eda_results",
            {
                "dataset_id": dataset_id,
                "file_id": file_id,
                "limit": 5,
                "offset": 0,
            },
            scoped_context,
        )
        provenance = self._provenance(
            evidence_references,
            handoff.data,
            quality.data,
            eda.data,
        )
        previous_outputs = [
            item
            for item in (analytics_outputs or [])[:8]
            if isinstance(item, dict)
        ]
        capabilities = registry.analytics_capabilities()
        if not isinstance(capabilities, list):
            raise PreparationError(
                "agent_preparation_invalid",
                "The registry returned invalid deterministic tool capabilities.",
            )
        artifact = SpecialistPreparationArtifact(
            contract_version=1,
            prepared_for="analytics_modelling",
            workflow_id=workflow_id,
            dataset_id=dataset_id,
            file_id=file_id,
            source_fingerprint=source_fingerprint,
            inventory=inventory,
            dataset_inventory=dataset_inventory,
            schema_metadata=schema_metadata,
            inspection_handoff=handoff,
            quality_results=quality,
            eda_results=eda,
            analytics_outputs=previous_outputs,
            provenance=provenance,
            tool_capabilities=capabilities,
            handoff_context=PreparationHandoffContext(
                workflow_id=workflow_id,
                dataset_id=dataset_id,
                file_id=file_id,
                source_fingerprint=source_fingerprint,
                operation=operation,
                evidence_references=evidence_references[:32],
            ),
        )
        encoded = json.dumps(
            artifact.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 32 * 1024:
            raise PreparationError(
                "agent_preparation_too_large",
                "The prepared specialist context exceeded its safe size limit.",
            )
        return artifact

    @staticmethod
    def _read_existing(
        registry: Any,
        name: str,
        arguments: dict[str, int],
        context: dict[str, Any],
        *,
        max_bytes: int = 8 * 1024,
    ) -> PreparationLookup:
        try:
            _, result, _ = registry.execute(
                name,
                arguments,
                context=context,
            )
        except Exception as error:
            logger.warning(
                "Specialist preparation read %s failed (%s)",
                name,
                type(error).__name__,
            )
            return PreparationLookup(
                status="failed",
                summary="The existing result could not be retrieved.",
                error_code="preparation_read_failed",
            )
        if not isinstance(result, ToolResult):
            return PreparationLookup(
                status="failed",
                summary="The existing-result reader returned an invalid response.",
                error_code="preparation_result_invalid",
            )
        if result.status != "succeeded":
            return PreparationLookup(
                status="failed",
                summary=result.summary[:500],
                warnings=list(result.warnings[:16]),
                error_code=result.error_code or "preparation_read_failed",
            )
        data = result.data
        if not isinstance(data, (dict, list)):
            return PreparationLookup(
                status="unavailable",
                summary="No persisted result is available for this source.",
            )
        try:
            serialized = json.dumps(
                data,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            return PreparationLookup(
                status="failed",
                summary="The persisted result could not be safely encoded.",
                error_code="preparation_result_invalid",
            )
        if len(serialized) > max_bytes:
            return PreparationLookup(
                status="bounded_out",
                summary="The persisted result exceeded the preparation size limit.",
            )
        return PreparationLookup(
            status="available",
            summary=result.summary[:500],
            data=data,
            warnings=list(result.warnings[:16]),
        )

    @staticmethod
    def _provenance(
        evidence_references: list[dict[str, Any]],
        handoff_data: dict[str, Any] | list[Any] | None,
        quality_data: dict[str, Any] | list[Any] | None,
        eda_data: dict[str, Any] | list[Any] | None,
    ) -> list[dict[str, Any]]:
        references = [
            dict(item)
            for item in evidence_references[:32]
            if isinstance(item, dict)
        ]
        if isinstance(handoff_data, dict):
            handoff_references = handoff_data.get("evidence_references")
            if isinstance(handoff_references, list):
                references.extend(
                    item
                    for item in handoff_references[:32]
                    if isinstance(item, dict)
                )
        for result in (quality_data, eda_data):
            if isinstance(result, dict):
                provenance = result.get("provenance")
                if isinstance(provenance, dict):
                    references.append({"type": "provenance", **provenance})
                result_references = result.get("references")
                if isinstance(result_references, list):
                    references.extend(
                        item
                        for item in result_references[:8]
                        if isinstance(item, dict)
                    )
                items = result.get("items")
                if isinstance(items, list):
                    for item in items[:8]:
                        if isinstance(item, dict) and isinstance(
                            item.get("provenance"),
                            dict,
                        ):
                            references.append(
                                {"type": "provenance", **item["provenance"]}
                            )
        return references[:64]

    def shutdown(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)


SPECIALIST_PREPARATION = SpecialistPreparationPlanner()
