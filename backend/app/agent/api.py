from __future__ import annotations

import json
import logging
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
    UsageMetadata,
)
from app.agent.providers import create_provider
from app.agent.runtime import execute_agent_request, public_outcome_metadata
from app.database import SessionLocal
from app.models import Conversation, ConversationMessage, Dataset, Project


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
PROCESSING_LEASE_SECONDS = 2_460
MAX_MESSAGES_PER_CONVERSATION = 25
MAX_PERSISTED_MESSAGE_BYTES = 256 * 1024


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
            project = (
                session.get(Project, payload.project_id)
                if payload.project_id is not None
                else None
            )
            if payload.project_id is not None and project is None:
                raise HTTPException(status_code=404, detail="Project not found.")

            dataset = (
                session.get(Dataset, payload.dataset_id)
                if payload.dataset_id is not None
                else None
            )
            if payload.dataset_id is not None and dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found.")
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
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
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
            conversation = (
                session.query(Conversation)
                .filter(Conversation.id == conversation_id)
                .with_for_update()
                .one_or_none()
            )
            if conversation is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")
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
            if conversation.title == "New conversation":
                conversation.title = content[:157].rstrip() + ("..." if len(content) > 157 else "")
            conversation.status = "Processing"
            conversation.processing_started_at = now
            conversation.processing_token = processing_token
            conversation.updated_at = now
            context = {
                "project_id": conversation.project_id,
                "dataset_id": conversation.dataset_id,
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
