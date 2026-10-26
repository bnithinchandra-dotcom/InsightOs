from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Annotated, Literal
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.agent.config import (
    MAX_HISTORY_MESSAGES,
    MAX_USER_MESSAGE_CHARS,
    get_agent_settings,
)
from app.agent.contracts import (
    AgentOutcome,
    CapabilityStatus,
    ProviderError,
    ToolResult,
    UsageMetadata,
)
from app.agent.providers import create_provider
from app.agent.runtime import execute_agent_request, public_outcome_metadata
from app.agent.tools import ToolRegistry
from app.database import SessionLocal
from app.models import (
    Conversation,
    ConversationMessage,
    Dataset,
    DatasetFile,
    Project,
    User,
    WorkflowExecution,
    WorkflowTask,
    WorkflowTaskDependency,
)
from app.workflow_execution import (
    MAX_WORKFLOW_TASKS,
    WorkflowPlanError,
    persist_workflow_task,
    recover_persisted_workflow,
    run_persisted_workflow,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
PROCESSING_LEASE_SECONDS = 2_460
MAX_MESSAGES_PER_CONVERSATION = 25
MAX_PERSISTED_MESSAGE_BYTES = 256 * 1024
MAX_RECOVERY_AGE_SECONDS = PROCESSING_LEASE_SECONDS


def _mark_workflow_failure(
    workflow_id: str,
    owner_user_id: int,
    error_code: str,
) -> None:
    try:
        with SessionLocal() as session:
            workflow = (
                session.query(WorkflowExecution)
                .filter(
                    WorkflowExecution.id == workflow_id,
                    WorkflowExecution.owner_user_id == owner_user_id,
                )
                .one_or_none()
            )
            if workflow is not None:
                workflow.status = "failed"
                workflow.error_code = error_code
                workflow.completed_at = _utcnow()
                session.commit()
    except SQLAlchemyError as error:
        logger.error(
            "Workflow failure state could not be saved for %s (%s)",
            workflow_id,
            type(error).__name__,
        )


def _dataset_state_identity(
    session: SessionLocal,
    dataset_id: int,
    owner_user_id: int,
) -> dict[str, Any] | None:
    dataset = (
        session.query(Dataset.id, Dataset.updated_at)
        .join(Project, Dataset.project_id == Project.id)
        .filter(
            Dataset.id == dataset_id,
            Project.user_id == owner_user_id,
        )
        .one_or_none()
    )
    if dataset is None:
        return None
    files = (
        session.query(
            DatasetFile.id,
            DatasetFile.checksum,
            DatasetFile.status,
            DatasetFile.normalization_status,
            DatasetFile.quality_analysis_key,
            DatasetFile.updated_at,
        )
        .filter(DatasetFile.dataset_id == dataset_id)
        .order_by(DatasetFile.id)
        .all()
    )
    return {
        "dataset_updated_at": (
            dataset.updated_at.isoformat() if dataset.updated_at is not None else None
        ),
        "files": [
            {
                "id": row.id,
                "checksum": row.checksum,
                "status": row.status,
                "normalization_status": row.normalization_status,
                "quality_analysis_key": row.quality_analysis_key,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in files
        ],
    }


def _tool_source_state_identity(
    session: SessionLocal,
    arguments: dict[str, Any],
    fallback_dataset_id: Any,
    owner_user_id: int,
) -> dict[str, Any] | None:
    sources = arguments.get("sources")
    if isinstance(sources, list):
        source_dataset_ids = [
            source.get("dataset_id")
            for source in sources
            if isinstance(source, dict)
            and isinstance(source.get("dataset_id"), int)
            and not isinstance(source.get("dataset_id"), bool)
            and source["dataset_id"] > 0
        ]
        if len(source_dataset_ids) != len(sources) or not source_dataset_ids:
            return None
        dataset_ids = set(source_dataset_ids)
        states = {}
        for dataset_id in sorted(dataset_ids):
            state = _dataset_state_identity(session, dataset_id, owner_user_id)
            if state is None:
                return None
            states[str(dataset_id)] = state
        return {"datasets": states}
    if (
        isinstance(fallback_dataset_id, int)
        and not isinstance(fallback_dataset_id, bool)
        and fallback_dataset_id > 0
    ):
        return _dataset_state_identity(session, fallback_dataset_id, owner_user_id)
    return None


def _workflow_task_input_is_current(
    session: SessionLocal,
    workflow: WorkflowExecution,
    task: WorkflowTask,
) -> bool:
    if not task.idempotent or task.dataset_id is None or not task.source_fingerprint:
        return False
    references = task.input_references
    if not isinstance(references, list):
        return False
    argument_refs = [
        item
        for item in references
        if isinstance(item, dict) and item.get("type") == "tool_arguments"
    ]
    identity_refs = [
        item
        for item in references
        if isinstance(item, dict) and item.get("type") == "input_identity"
    ]
    context_refs = [
        item
        for item in references
        if isinstance(item, dict) and item.get("type") == "input_context"
    ]
    if len(argument_refs) != 1 or len(identity_refs) != 1 or len(context_refs) != 1:
        return False
    argument_ref = argument_refs[0]
    context_ref = context_refs[0]
    arguments = argument_ref.get("arguments")
    pinned_context = context_ref.get("context")
    workflow_scope = (
        workflow.source_scope if isinstance(workflow.source_scope, dict) else {}
    )
    if (
        argument_ref.get("tool") != task.task_type
        or not isinstance(arguments, dict)
        or not isinstance(pinned_context, dict)
        or workflow_scope.get("project_id") is not None
        and pinned_context.get("project_id") != workflow_scope.get("project_id")
        or workflow_scope.get("dataset_id") is not None
        and pinned_context.get("dataset_id") != workflow_scope.get("dataset_id")
    ):
        return False
    source_identity = _tool_source_state_identity(
        session,
        arguments,
        task.dataset_id,
        workflow.owner_user_id,
    )
    if source_identity is None:
        return False
    encoded_source = json.dumps(
        source_identity,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    source_fingerprint = hashlib.sha256(encoded_source.encode("utf-8")).hexdigest()
    if source_fingerprint != task.source_fingerprint:
        return False
    identity = json.dumps(
        {
            "tool": task.task_type,
            "arguments": arguments,
            "project_id": pinned_context.get("project_id"),
            "dataset_id": pinned_context.get("dataset_id"),
            "source_identity": source_identity,
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest() == identity_refs[0].get(
        "sha256"
    )


def _workflow_tool_scheduler(
    workflow_id: str,
    owner_user_id: int,
    context: dict[str, Any],
) -> Callable[..., tuple[str, ToolResult, str, str | None]]:
    def schedule(
        tool_name: str,
        arguments: dict[str, Any],
        execute: Callable[[], tuple[str, ToolResult, str]],
        *,
        idempotent: bool,
        max_retries: int = 0,
        depends_on: list[str] | None = None,
    ) -> tuple[str, ToolResult, str, str | None]:
        try:
            arguments_identity = json.dumps(
                arguments,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, OverflowError):
            return (
                "",
                ToolResult(
                    status="failed",
                    summary="The tool inputs could not be safely identified.",
                    error_code="workflow_input_invalid",
                ),
                tool_name,
                None,
            )
        if len(arguments_identity.encode("utf-8")) > 4096:
            return (
                "",
                ToolResult(
                    status="failed",
                    summary="The tool inputs exceed the safe recovery storage limit.",
                    error_code="workflow_input_too_large",
                ),
                tool_name,
                None,
            )
        dataset_id = arguments.get("dataset_id", context.get("dataset_id"))
        if dataset_id is None and isinstance(arguments.get("sources"), list):
            first_source = next(
                (
                    source
                    for source in arguments["sources"]
                    if isinstance(source, dict)
                ),
                None,
            )
            if first_source is not None:
                dataset_id = first_source.get("dataset_id")
        file_id = arguments.get("file_id")
        try:
            with SessionLocal() as session:
                workflow = (
                    session.query(WorkflowExecution)
                    .filter(
                        WorkflowExecution.id == workflow_id,
                        WorkflowExecution.owner_user_id == owner_user_id,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if workflow is None:
                    raise LookupError("Workflow not found.")
                source_identity = _tool_source_state_identity(
                    session,
                    arguments,
                    dataset_id,
                    owner_user_id,
                )
                identity = json.dumps(
                    {
                        "tool": tool_name,
                        "arguments": json.loads(arguments_identity),
                        "project_id": context.get("project_id"),
                        "dataset_id": context.get("dataset_id"),
                        "source_identity": source_identity,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                input_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                source_digest = (
                    hashlib.sha256(
                        json.dumps(
                            source_identity,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    if source_identity is not None
                    else None
                )
                task_key = (
                    f"tool:{tool_name}:{input_digest[:24]}"
                    if idempotent
                    else f"tool:{tool_name}:{uuid4().hex[:24]}"
                )
                dependencies = depends_on or []
                plan_item = {
                    "task_id": task_key,
                    "agent": "registered_tool",
                    "operation": tool_name,
                    "depends_on": dependencies,
                    "status": "pending",
                    "dataset_id": dataset_id,
                    "file_id": file_id,
                    "source_fingerprint": source_digest,
                    "input_references": [
                        {"type": "input_identity", "sha256": input_digest},
                        {
                            "type": "tool_arguments",
                            "tool": tool_name,
                            "arguments": json.loads(arguments_identity),
                        },
                        {
                            "type": "input_context",
                            "context": {
                                "project_id": context.get("project_id"),
                                "dataset_id": context.get("dataset_id"),
                            },
                        },
                    ],
                    "output_references": [],
                    "idempotent": idempotent,
                    "max_retries": max_retries if idempotent else 0,
                }
                task = persist_workflow_task(session, workflow, plan_item)
                session.commit()
                task_id = task.id
        except (SQLAlchemyError, WorkflowPlanError, LookupError) as error:
            logger.error(
                "Workflow task could not be persisted for %s (%s)",
                workflow_id,
                type(error).__name__,
            )
            _mark_workflow_failure(
                workflow_id,
                owner_user_id,
                "workflow_task_persistence_failed",
            )
            return (
                "",
                ToolResult(
                    status="failed",
                    summary="The tool operation could not be safely scheduled.",
                    error_code="workflow_task_persistence_failed",
                ),
                tool_name,
                None,
            )

        last_execution: list[tuple[str, ToolResult, str]] = []

        def handler(_task: WorkflowTask) -> dict[str, Any]:
            raw_output, result, label = execute()
            last_execution[:] = [(raw_output, result, label)]
            if result.status != "succeeded":
                raise RuntimeError("Registered tool returned a failed result.")
            return {
                "status": result.status,
                "summary": result.summary,
                "data": result.data,
                "warnings": list(result.warnings),
                "references": list(result.references),
                "limitations": list(result.limitations),
                "error_code": result.error_code,
                "label": label,
            }

        try:
            with SessionLocal() as session:
                run_persisted_workflow(
                    session,
                    workflow_id,
                    {tool_name: handler},
                )
            with SessionLocal() as session:
                task = session.get(WorkflowTask, task_id)
                if (
                    task is None
                    or task.workflow_id != workflow_id
                ):
                    return (
                        "",
                        ToolResult(
                            status="failed",
                            summary="The scheduled tool operation did not complete.",
                            error_code=task.error_code if task else "workflow_task_missing",
                        ),
                        tool_name,
                        None,
                    )
                if task.status != "completed":
                    if last_execution and last_execution[-1][1].status == "failed":
                        raw_output, result, label = last_execution[-1]
                        return raw_output, result, label, task.task_key
                    return (
                        "",
                        ToolResult(
                            status="failed",
                            summary="The scheduled tool operation did not complete.",
                            error_code=task.error_code or "workflow_task_failed",
                        ),
                        tool_name,
                        task.task_key,
                    )
                if last_execution:
                    raw_output, result, label = last_execution[-1]
                    return raw_output, result, label, task.task_key
                if not isinstance(task.result_summary, dict):
                    return (
                        "",
                        ToolResult(
                            status="failed",
                            summary="The completed tool output could not be recovered.",
                            error_code="workflow_result_unavailable",
                        ),
                        tool_name,
                        task.task_key,
                    )
                stored = task.result_summary
                result = ToolResult(
                    status="succeeded",
                    summary=stored["summary"],
                    data=stored.get("data"),
                    warnings=tuple(stored.get("warnings", [])),
                    references=tuple(stored.get("references", [])),
                    limitations=tuple(stored.get("limitations", [])),
                    error_code=stored.get("error_code"),
                )
                raw_output = json.dumps(
                    {
                        "status": result.status,
                        "summary": result.summary,
                        "data": result.data,
                        "warnings": list(result.warnings),
                        "references": list(result.references),
                        "limitations": list(result.limitations),
                        "error_code": result.error_code,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                return raw_output, result, stored.get("label", tool_name), task.task_key
        except (SQLAlchemyError, WorkflowPlanError, LookupError) as error:
            logger.error(
                "Workflow task execution failed for %s (%s)",
                workflow_id,
                type(error).__name__,
            )
            _mark_workflow_failure(
                workflow_id,
                owner_user_id,
                "workflow_task_execution_failed",
            )
            return (
                "",
                ToolResult(
                    status="failed",
                    summary="The scheduled tool operation could not be completed.",
                    error_code="workflow_task_execution_failed",
                ),
                tool_name,
                None,
            )

    return schedule


def _require_workspace_owner(session: SessionLocal) -> User:
    """Resolve the single trusted local workspace user for protected operations."""
    users = session.query(User).order_by(User.id.asc()).limit(2).all()
    if not users:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workspace_profile_required",
                "message": "Create a local workspace profile before using protected workspace data.",
            },
        )
    if len(users) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "authentication_required",
                "message": "Authentication is required to choose a project owner.",
            },
        )
    return users[0]


def _require_project_access(session, project_id: int, owner: User) -> Project | None:
    project = session.get(Project, project_id)
    if project is None or project.user_id != owner.id:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _require_dataset_access(session, dataset_id: int, owner: User) -> Dataset | None:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    project = session.get(Project, dataset.project_id)
    if project is None or project.user_id != owner.id:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return dataset


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConversationCreateRequest(StrictRequest):
    title: StrictStr | None = Field(default=None, max_length=160)
    project_id: StrictInt | None = Field(default=None, gt=0)
    dataset_id: StrictInt | None = Field(default=None, gt=0)


class UserMessageRequest(StrictRequest):
    content: StrictStr = Field(min_length=1, max_length=MAX_USER_MESSAGE_CHARS)


class ConversationSummaryResponse(BaseModel):
    id: int
    title: str
    status: str
    project_id: int | None
    dataset_id: int | None
    created_at: str
    updated_at: str


class ConversationMessageResponse(BaseModel):
    id: int
    sequence: int
    role: str
    content: str
    metadata: dict[str, Any] | None
    created_at: str


class ConversationDetailResponse(ConversationSummaryResponse):
    total_messages: int
    limit: int
    offset: int
    messages: list[ConversationMessageResponse]


class ChatTurnResponse(BaseModel):
    conversation: ConversationSummaryResponse
    user_message: ConversationMessageResponse
    assistant_message: ConversationMessageResponse
    execution_status: str
    workflow_id: str
    tool_activities: list[dict[str, Any]]
    structured_results: list[dict[str, Any]]
    warnings: list[str]
    error_code: str | None


class ProviderStatusResponse(BaseModel):
    provider: str
    status: Literal[
        "not_configured",
        "invalid_configuration",
        "unavailable",
        "failed",
        "healthy",
    ]
    model: str | None
    message: str
    capabilities: dict[str, CapabilityStatus]
    provider_capabilities: dict[str, CapabilityStatus]
    model_identity: dict[str, str | None] | None = None
    limits: dict[str, int | None]
    runtime_version: str | None = None


def _conversation_summary(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "status": conversation.status,
        "project_id": conversation.project_id,
        "dataset_id": conversation.dataset_id,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


def _message_response(message: ConversationMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "sequence": message.sequence,
        "role": message.role,
        "content": message.content,
        "metadata": message.metadata_json,
        "created_at": message.created_at.isoformat(),
    }


def _safe_title(value: str | None) -> str:
    if value is None or not value.strip():
        return "New conversation"
    return value.strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _provider_status() -> dict[str, Any]:
    capabilities: dict[str, CapabilityStatus] = {
        "text_generation": "unsupported",
        "structured_output": "unsupported",
        "tool_calling": "unsupported",
        "streaming": "unsupported",
        "local_inference": "unsupported",
    }
    try:
        settings = get_agent_settings()
    except ProviderError:
        return {
            "provider": "invalid",
            "status": "invalid_configuration",
            "model": None,
            "message": "The model provider configuration is invalid.",
            "capabilities": capabilities,
            "provider_capabilities": capabilities,
            "model_identity": None,
            "limits": {"max_output_tokens": None, "context_window_tokens": None},
            "runtime_version": None,
        }
    if settings.provider == "none":
        return {
            "provider": "none",
            "status": "not_configured",
            "model": None,
            "message": "Configure Ollama or an OpenAI-compatible model to use the assistant. Deterministic data APIs remain available.",
            "capabilities": capabilities,
            "provider_capabilities": capabilities,
            "model_identity": None,
            "limits": {
                "max_output_tokens": settings.max_output_tokens,
                "context_window_tokens": None,
            },
            "runtime_version": None,
        }
    missing = not settings.model or (
        settings.provider == "openai_compatible" and not settings.api_key
    )
    if missing:
        return {
            "provider": settings.provider,
            "status": "not_configured",
            "model": settings.model,
            "message": "The selected model provider needs additional server-side configuration.",
            "capabilities": capabilities,
            "provider_capabilities": capabilities,
            "model_identity": None,
            "limits": {
                "max_output_tokens": settings.max_output_tokens,
                "context_window_tokens": None,
            },
            "runtime_version": None,
        }
    capabilities = {
        "text_generation": "unverified",
        "structured_output": "unverified",
        "tool_calling": "unverified",
        "streaming": "unsupported",
        "local_inference": "unverified",
    }
    limits = {
        "max_output_tokens": settings.max_output_tokens,
        "context_window_tokens": None,
    }
    try:
        health = create_provider(settings).check_health()
    except ProviderError as error:
        unavailable_codes = {
            "provider_timeout",
            "provider_unavailable",
            "provider_rate_limited",
        }
        is_unavailable = error.code in unavailable_codes
        return {
            "provider": settings.provider,
            "status": "unavailable" if is_unavailable else "failed",
            "model": settings.model,
            "message": (
                "The configured model runtime is unavailable."
                if is_unavailable
                else "The configured model runtime could not verify its status."
            ),
            "capabilities": capabilities,
            "provider_capabilities": capabilities,
            "model_identity": None,
            "limits": limits,
            "runtime_version": None,
        }
    status_value = health.status
    return {
        "provider": settings.provider,
        "status": status_value,
        "model": settings.model,
        "message": (
            "The provider runtime and configured model are reachable. "
            "Generation behavior remains unverified until model evaluation."
            if status_value == "healthy"
            else "The provider is reachable, but the configured model is not available."
        ),
        "capabilities": capabilities,
        "provider_capabilities": (
            health.capabilities.as_dict()
            if health.capabilities is not None
            else {
                "text_generation": "unverified",
                "structured_output": "unverified",
                "tool_calling": "unverified",
                "streaming": "unsupported",
                "local_inference": "unverified",
            }
        ),
        "model_identity": (
            {
                "provider": health.model_identity.provider,
                "model": health.model_identity.model,
                "digest": health.model_identity.digest,
            }
            if health.model_identity is not None
            else None
        ),
        "limits": limits,
        "runtime_version": health.runtime_version,
    }


@router.get("/provider-status", response_model=ProviderStatusResponse)
def get_chat_provider_status():
    return _provider_status()


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=ConversationSummaryResponse,
)
def create_conversation(payload: ConversationCreateRequest):
    with SessionLocal() as session:
        try:
            owner = _require_workspace_owner(session)
            project = (
                _require_project_access(session, payload.project_id, owner)
                if payload.project_id is not None
                else None
            )

            dataset = (
                _require_dataset_access(session, payload.dataset_id, owner)
                if payload.dataset_id is not None
                else None
            )
            project_id = payload.project_id
            if dataset is not None:
                if project_id is not None and dataset.project_id != project_id:
                    raise HTTPException(
                        status_code=422,
                        detail="Dataset does not belong to the selected project.",
                    )
                project_id = dataset.project_id

            conversation = Conversation(
                title=_safe_title(payload.title),
                project_id=project_id,
                dataset_id=payload.dataset_id,
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return _conversation_summary(conversation)
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            session.rollback()
            logger.error("Conversation creation failed (%s)", type(error).__name__)
            raise HTTPException(
                status_code=500,
                detail="Conversation could not be created.",
            ) from None


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
def list_conversations(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    with SessionLocal() as session:
        try:
            _require_workspace_owner(session)
            conversations = (
                session.query(Conversation)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [_conversation_summary(item) for item in conversations]
        except SQLAlchemyError as error:
            logger.error("Conversation listing failed (%s)", type(error).__name__)
            raise HTTPException(
                status_code=500,
                detail="Conversations could not be retrieved.",
            ) from None


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
)
def get_conversation(
    conversation_id: Annotated[int, Path(gt=0)],
    limit: int = Query(default=MAX_MESSAGES_PER_CONVERSATION, ge=1, le=MAX_MESSAGES_PER_CONVERSATION),
    offset: int = Query(default=0, ge=0),
):
    with SessionLocal() as session:
        try:
            owner = _require_workspace_owner(session)
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            if conversation.project_id is not None:
                _require_project_access(session, conversation.project_id, owner)
            if conversation.dataset_id is not None:
                _require_dataset_access(session, conversation.dataset_id, owner)
            query = session.query(ConversationMessage).filter(
                ConversationMessage.conversation_id == conversation_id
            )
            total = query.count()
            messages = (
                query.order_by(ConversationMessage.sequence.asc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return {
                **_conversation_summary(conversation),
                "total_messages": total,
                "limit": limit,
                "offset": offset,
                "messages": [_message_response(message) for message in messages],
            }
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Conversation retrieval failed for %s (%s)",
                conversation_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Conversation could not be retrieved.",
            ) from None


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatTurnResponse,
)
def send_conversation_message(
    conversation_id: Annotated[int, Path(gt=0)],
    payload: UserMessageRequest,
):
    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=422,
            detail="Message content cannot be blank.",
        )

    user_message: ConversationMessage
    history: list[dict[str, Any]]
    context: dict[str, Any]
    processing_token = str(uuid4())
    with SessionLocal() as session:
        try:
            owner = _require_workspace_owner(session)
            conversation = (
                session.query(Conversation)
                .filter(Conversation.id == conversation_id)
                .with_for_update()
                .one_or_none()
            )
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            if conversation.project_id is not None:
                _require_project_access(session, conversation.project_id, owner)
            if conversation.dataset_id is not None:
                _require_dataset_access(session, conversation.dataset_id, owner)
            now = _utcnow()
            if (
                conversation.status == "Processing"
                and conversation.processing_started_at is not None
                and conversation.processing_started_at
                > now - timedelta(seconds=PROCESSING_LEASE_SECONDS)
            ):
                raise HTTPException(
                    status_code=409,
                    detail="A response is already being generated for this conversation.",
                )

            query = session.query(ConversationMessage).filter(
                ConversationMessage.conversation_id == conversation_id
            )
            history_rows = (
                query.order_by(ConversationMessage.sequence.desc())
                .limit(MAX_HISTORY_MESSAGES)
                .all()
            )
            history_rows.reverse()
            history = [
                {
                    "role": item.role,
                    "content": item.content,
                    "metadata": item.metadata_json,
                }
                for item in history_rows
            ]
            next_sequence = (
                session.query(func.max(ConversationMessage.sequence))
                .filter(ConversationMessage.conversation_id == conversation_id)
                .scalar()
                or 0
            ) + 1
            user_message = ConversationMessage(
                conversation_id=conversation_id,
                sequence=next_sequence,
                role="user",
                content=content,
            )
            session.add(user_message)
            session.flush()
            workflow_id = uuid4().hex
            workflow = WorkflowExecution(
                id=workflow_id,
                owner_user_id=owner.id,
                conversation_id=conversation.id,
                dataset_id=conversation.dataset_id,
                idempotency_key=processing_token,
                objective=content,
                status="running",
                current_step=0,
                source_scope={
                    "project_id": conversation.project_id,
                    "dataset_id": conversation.dataset_id,
                },
                started_at=now,
            )
            session.add(workflow)
            session.flush()
            if conversation.title == "New conversation":
                conversation.title = content[:157].rstrip() + ("..." if len(content) > 157 else "")
            conversation.status = "Processing"
            conversation.processing_started_at = now
            conversation.processing_token = processing_token
            conversation.updated_at = now
            context = {
                "project_id": conversation.project_id,
                "dataset_id": conversation.dataset_id,
                "conversation_id": conversation.id,
                "workflow_id": workflow_id,
                "owner_user_id": owner.id,
            }
            session.commit()
            session.refresh(user_message)
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Conversation message could not be recorded for %s (%s)",
                conversation_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Message could not be saved.",
            ) from None

    try:
        outcome = execute_agent_request(
            user_message=content,
            history=history,
            context=context,
            workflow_scheduler=_workflow_tool_scheduler(
                context["workflow_id"],
                context["owner_user_id"],
                context,
            ),
        )
        metadata = public_outcome_metadata(outcome)
    except Exception as error:
        logger.error(
            "Agent execution failed for conversation %s (%s)",
            conversation_id,
            type(error).__name__,
        )
        outcome = AgentOutcome(
            status="failed",
            assistant_text="The assistant could not complete this request. Please retry.",
            activities=(),
            structured_results=(),
            warnings=("The assistant execution failed before producing a result.",),
            usage=UsageMetadata(),
            error_code="agent_execution_failed",
        )
        metadata = public_outcome_metadata(outcome)
    try:
        assistant_size = len(outcome.assistant_text.encode("utf-8"))
        metadata_size = len(
            json.dumps(
                metadata,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (UnicodeError, TypeError, ValueError):
        assistant_size = MAX_PERSISTED_MESSAGE_BYTES + 1
        metadata_size = MAX_PERSISTED_MESSAGE_BYTES + 1
    if assistant_size + metadata_size > MAX_PERSISTED_MESSAGE_BYTES:
        outcome_text = (
            "The request produced results that exceeded the safe conversation storage limit. "
            "Please retry with a narrower request."
        )
        metadata = {
            "execution_status": "partial",
            "tool_activities": [],
            "structured_results": [],
            "warnings": ["The conversation response exceeded its storage limit."],
            "error_code": "response_too_large",
            "usage": {
                "input_tokens": outcome.usage.input_tokens,
                "output_tokens": outcome.usage.output_tokens,
            },
        }
    else:
        outcome_text = outcome.assistant_text

    with SessionLocal() as session:
        try:
            conversation = (
                session.query(Conversation)
                .filter(Conversation.id == conversation_id)
                .with_for_update()
                .one_or_none()
            )
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
            if conversation.processing_token != processing_token:
                interrupted = session.get(WorkflowExecution, context["workflow_id"])
                if interrupted is not None and interrupted.status == "running":
                    interrupted.status = "cancelled"
                    interrupted.error_code = "response_superseded"
                    interrupted.completed_at = _utcnow()
                    session.commit()
                raise HTTPException(
                    status_code=409,
                    detail="This response was superseded by a newer request.",
                )
            sequence = (
                session.query(func.max(ConversationMessage.sequence))
                .filter(ConversationMessage.conversation_id == conversation_id)
                .scalar()
                or 0
            ) + 1
            assistant_message = ConversationMessage(
                conversation_id=conversation_id,
                sequence=sequence,
                role="assistant",
                content=outcome_text,
                metadata_json=metadata,
            )
            session.add(assistant_message)
            workflow = session.get(WorkflowExecution, context["workflow_id"])
            if (
                workflow is None
                or workflow.owner_user_id != context["owner_user_id"]
                or workflow.conversation_id != conversation_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="The workflow could not be associated with this conversation.",
                )
            trace = next(
                (
                    item
                    for item in outcome.structured_results
                    if isinstance(item, dict)
                    and item.get("tool") == "orchestration_trace"
                    and isinstance(item.get("data"), dict)
                ),
                None,
            )
            if trace is not None:
                source_identity = trace["data"].get("source_identity")
                if isinstance(source_identity, dict):
                    fingerprint = source_identity.get("source_fingerprint")
                    workflow.source_scope = {
                        "project_id": context["project_id"],
                        "dataset_id": source_identity.get(
                            "dataset_id",
                            context["dataset_id"],
                        ),
                        "file_id": source_identity.get("file_id"),
                        "source_fingerprint": (
                            fingerprint
                            if isinstance(fingerprint, str) and len(fingerprint) <= 128
                            else None
                        ),
                    }
            workflow_status = {
                "completed": "completed",
                "partial": "partial",
                "failed": "failed",
                "provider_unavailable": "failed",
                "unsupported": "blocked",
                "blocked": "blocked",
            }.get(outcome.status, "failed")
            if workflow.error_code in {
                "workflow_task_persistence_failed",
                "workflow_task_execution_failed",
            }:
                workflow_status = "failed"
            task_statuses = [
                task.status
                for task in session.query(WorkflowTask.status)
                .filter(WorkflowTask.workflow_id == workflow.id)
                .all()
            ]
            if task_statuses and not all(
                task_status == "completed" for task_status in task_statuses
            ):
                if "completed" in task_statuses:
                    workflow_status = "partial"
                elif set(task_statuses).issubset({"blocked", "cancelled"}):
                    workflow_status = "blocked"
                else:
                    workflow_status = "failed"
            if task_statuses or workflow.error_code in {
                "workflow_task_persistence_failed",
                "workflow_task_execution_failed",
            }:
                metadata["execution_status"] = workflow_status
                if workflow.error_code and metadata.get("error_code") is None:
                    metadata["error_code"] = workflow.error_code
                assistant_message.metadata_json = metadata
            workflow.status = workflow_status
            workflow.completed_at = _utcnow()
            workflow.final_response = outcome_text[:MAX_USER_MESSAGE_CHARS * 4]
            workflow.final_artifact_references = (
                trace.get("references", [])[:32]
                if trace is not None
                and isinstance(trace.get("references"), list)
                else []
            )
            conversation.status = "Active"
            conversation.processing_started_at = None
            conversation.processing_token = None
            conversation.updated_at = _utcnow()
            session.commit()
            session.refresh(assistant_message)
            session.refresh(conversation)
            return {
                "conversation": _conversation_summary(conversation),
                "user_message": _message_response(user_message),
                "assistant_message": _message_response(assistant_message),
                "execution_status": metadata["execution_status"],
                "workflow_id": workflow.id,
                "tool_activities": metadata["tool_activities"],
                "structured_results": metadata["structured_results"],
                "warnings": metadata["warnings"],
                "error_code": metadata["error_code"],
            }
        except HTTPException:
            session.rollback()
            raise
        except SQLAlchemyError as error:
            session.rollback()
            logger.error(
                "Assistant response persistence failed for conversation %s (%s)",
                conversation_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "The assistant response could not be saved. The user message remains stored; "
                    "retry after the conversation becomes available."
                ),
            ) from None


@router.get("/workflows/{workflow_id}")
def get_chat_workflow(workflow_id: str):
    if len(workflow_id) != 32 or any(
        character not in "0123456789abcdef" for character in workflow_id
    ):
        raise HTTPException(status_code=404, detail="Workflow not found.")
    with SessionLocal() as session:
        try:
            owner = _require_workspace_owner(session)
            workflow = (
                session.query(WorkflowExecution)
                .filter(
                    WorkflowExecution.id == workflow_id,
                    WorkflowExecution.owner_user_id == owner.id,
                )
                .one_or_none()
            )
            if workflow is None:
                raise HTTPException(status_code=404, detail="Workflow not found.")
            if workflow.conversation_id is not None:
                conversation = session.get(Conversation, workflow.conversation_id)
                if conversation is None:
                    raise HTTPException(status_code=404, detail="Workflow not found.")
                if conversation.project_id is not None:
                    _require_project_access(session, conversation.project_id, owner)
                if conversation.dataset_id is not None:
                    _require_dataset_access(session, conversation.dataset_id, owner)
            if workflow.dataset_id is not None:
                _require_dataset_access(session, workflow.dataset_id, owner)
            tasks = (
                session.query(WorkflowTask)
                .filter(WorkflowTask.workflow_id == workflow.id)
                .order_by(WorkflowTask.sequence)
                .all()
            )
            task_ids = {task.id: task.task_key for task in tasks}
            dependencies = session.query(
                WorkflowTaskDependency.task_id,
                WorkflowTaskDependency.depends_on_task_id,
            ).filter(
                WorkflowTaskDependency.task_id.in_(task_ids)
            ).all() if task_ids else []
            dependency_map: dict[str, list[str]] = {}
            for task_id, depends_on_id in dependencies:
                dependency_map.setdefault(task_id, []).append(
                    task_ids.get(depends_on_id, "")
                )
            return {
                "workflow_id": workflow.id,
                "conversation_id": workflow.conversation_id,
                "objective": workflow.objective,
                "status": workflow.status,
                "current_step": workflow.current_step,
                "plan": workflow.plan,
                "source_scope": workflow.source_scope,
                "final_response": workflow.final_response,
                "final_artifact_references": workflow.final_artifact_references,
                "error_code": workflow.error_code,
                "started_at": workflow.started_at.isoformat() if workflow.started_at else None,
                "completed_at": workflow.completed_at.isoformat() if workflow.completed_at else None,
                "tasks": [
                    {
                        "task_id": task.task_key,
                        "agent": task.agent,
                        "task_type": task.task_type,
                        "status": task.status,
                        "depends_on": dependency_map.get(task.id, []),
                        "dataset_id": task.dataset_id,
                        "file_id": task.file_id,
                        "source_fingerprint": task.source_fingerprint,
                        "input_references": task.input_references,
                        "output_references": task.output_references,
                        "result_summary": (
                            task.result_summary.get("summary")
                            if isinstance(task.result_summary, dict)
                            else None
                        ),
                        "retry_count": task.retry_count,
                        "max_retries": task.max_retries,
                        "timeout_seconds": task.timeout_seconds,
                        "started_at": task.started_at.isoformat() if task.started_at else None,
                        "completed_at": (
                            task.completed_at.isoformat()
                            if task.completed_at
                            else None
                        ),
                        "error_code": task.error_code,
                    }
                    for task in tasks
                ],
            }
        except HTTPException:
            raise
        except SQLAlchemyError as error:
            logger.error(
                "Workflow retrieval failed (%s)",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Workflow could not be retrieved.",
            ) from None


@router.post("/workflows/{workflow_id}/recover")
def recover_chat_workflow(workflow_id: str):
    if len(workflow_id) != 32 or any(
        character not in "0123456789abcdef" for character in workflow_id
    ):
        raise HTTPException(status_code=404, detail="Workflow not found.")

    with SessionLocal() as session:
        try:
            owner = _require_workspace_owner(session)
            workflow = (
                session.query(WorkflowExecution)
                .filter(
                    WorkflowExecution.id == workflow_id,
                    WorkflowExecution.owner_user_id == owner.id,
                )
                .with_for_update()
                .one_or_none()
            )
            if workflow is None:
                raise HTTPException(status_code=404, detail="Workflow not found.")
            if workflow.conversation_id is not None:
                conversation = session.get(Conversation, workflow.conversation_id)
                if conversation is None:
                    raise HTTPException(status_code=404, detail="Workflow not found.")
                if conversation.project_id is not None:
                    _require_project_access(session, conversation.project_id, owner)
                if conversation.dataset_id is not None:
                    _require_dataset_access(session, conversation.dataset_id, owner)
            if workflow.dataset_id is not None:
                _require_dataset_access(session, workflow.dataset_id, owner)

            if workflow.status in {"completed", "cancelled"}:
                return {
                    "workflow_id": workflow.id,
                    "status": workflow.status,
                    "recovery": {
                        "recovered": [],
                        "reused": [],
                        "invalidated": [],
                        "not_replayed": [],
                    },
                }

            now = _utcnow()
            stale_before = now - timedelta(seconds=MAX_RECOVERY_AGE_SECONDS)
            if workflow.updated_at > stale_before:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "workflow_not_stale",
                        "message": "The workflow is still within its active execution lease.",
                    },
                )

            recovery_context = {
                "project_id": (
                    workflow.source_scope.get("project_id")
                    if isinstance(workflow.source_scope, dict)
                    else None
                ),
                "dataset_id": (
                    workflow.source_scope.get("dataset_id")
                    if isinstance(workflow.source_scope, dict)
                    else None
                ),
                "conversation_id": workflow.conversation_id,
                "workflow_id": workflow.id,
                "owner_user_id": owner.id,
                "user_message": workflow.objective,
            }
            registry = ToolRegistry()
            registered_tools = {
                item.get("function", {}).get("name")
                for item in registry.provider_schemas(context=recovery_context)
                if isinstance(item, dict)
                and isinstance(item.get("function"), dict)
            }
            handlers: dict[str, Callable[[WorkflowTask], dict[str, Any]]] = {}
            for task in workflow.tasks:
                if (
                    not task.idempotent
                    or task.task_type not in registered_tools
                    or registry.is_engine_operation(task.task_type)
                ):
                    continue

                def execute_recovered_tool(
                    persisted_task: WorkflowTask,
                    *,
                    selected_registry: ToolRegistry = registry,
                    selected_context: dict[str, Any] = recovery_context,
                ) -> dict[str, Any]:
                    argument_refs = [
                        item
                        for item in persisted_task.input_references
                        if isinstance(item, dict)
                        and item.get("type") == "tool_arguments"
                        and item.get("tool") == persisted_task.task_type
                    ]
                    if len(argument_refs) != 1 or not isinstance(
                        argument_refs[0].get("arguments"), dict
                    ):
                        raise WorkflowPlanError(
                            "Stored recovery arguments are missing or ambiguous."
                        )
                    _raw, result, _label = selected_registry.execute(
                        persisted_task.task_type,
                        argument_refs[0]["arguments"],
                        context=selected_context,
                    )
                    if result.status != "succeeded":
                        raise RuntimeError("Recovered tool execution failed.")
                    return {
                        "status": result.status,
                        "summary": result.summary,
                        "data": result.data,
                        "warnings": list(result.warnings),
                        "references": list(result.references),
                        "limitations": list(result.limitations),
                        "error_code": result.error_code,
                    }

                handlers[task.task_type] = execute_recovered_tool

            workflow, recovery = recover_persisted_workflow(
                session,
                workflow.id,
                handlers,
                _workflow_task_input_is_current,
                stale_before=stale_before,
                max_steps=MAX_WORKFLOW_TASKS,
            )
            session.refresh(workflow)
            return {
                "workflow_id": workflow.id,
                "status": workflow.status,
                "error_code": workflow.error_code,
                "recovery": recovery,
            }
        except HTTPException:
            raise
        except (SQLAlchemyError, WorkflowPlanError, LookupError) as error:
            session.rollback()
            logger.error(
                "Workflow recovery failed for %s (%s)",
                workflow_id,
                type(error).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Workflow recovery could not be completed.",
            ) from None
