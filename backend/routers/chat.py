"""Chat router - SSE streaming endpoint wrapping the agent loop."""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field, field_validator

import core.agent as agent_module
from core.llm import chat_configured

from .. import session_store
from ..agent_stream import ABORTED_ANSWER, clear_abort_flag, set_abort_flag, stream_agent
from ..redaction import public_exception_message, redact_text
from ..schemas import ChatRequest
from .params import query_int, query_optional_int


router = APIRouter(prefix="/api", tags=["chat"])
STOPPED_MARKER = "已停止生成"
MAX_PARTIAL_ANSWER_CHARS = 20000
SessionIdPath = Annotated[str, ApiPath(min_length=1, max_length=120)]
SessionIdField = Annotated[str, Field(min_length=1, max_length=120)]


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("title cannot be empty")
        return normalized


class BatchDeleteSessionsRequest(BaseModel):
    session_ids: list[SessionIdField] = Field(..., min_length=1, max_length=200)

    @field_validator("session_ids", mode="before")
    @classmethod
    def normalize_session_ids(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        if not all(isinstance(session_id, str) for session_id in value):
            return value
        normalized = [" ".join(session_id.strip().split()) for session_id in value]
        if any(not session_id for session_id in normalized):
            raise ValueError("session_ids cannot contain empty values")
        return normalized


class AbortSessionRequest(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=8000)
    partial_answer: str | None = Field(default=None, max_length=MAX_PARTIAL_ANSWER_CHARS)

    @field_validator("question", mode="before")
    @classmethod
    def normalize_question(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("question cannot be empty")
        return normalized

    @field_validator("partial_answer", mode="before")
    @classmethod
    def normalize_partial_answer(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        return value.strip()[:MAX_PARTIAL_ANSWER_CHARS]


def _stopped_answer(partial_answer: str | None) -> str:
    answer = (partial_answer or "").strip()
    if answer:
        return f"{answer}\n\n*（{STOPPED_MARKER}）*"
    return f"*（{STOPPED_MARKER}）*"


def _to_history(rows: list[dict[str, Any]]) -> list[BaseMessage]:
    history: list[BaseMessage] = []
    for row in rows:
        if row["role"] == "user":
            history.append(HumanMessage(content=row["content"]))
        elif row["role"] == "assistant":
            history.append(AIMessage(content=row["content"]))
    while history and not isinstance(history[0], HumanMessage):
        history.pop(0)
    return history


def _parse_sse_event(payload: str) -> tuple[str | None, dict[str, Any] | None]:
    event_name: str | None = None
    data_lines: list[str] = []
    for line in payload.splitlines():
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())

    if not data_lines:
        return event_name, None

    try:
        return event_name, json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return event_name, None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_and_persist(
    question: str,
    history: list[BaseMessage],
    session_id: str,
) -> Generator[str, None, None]:
    final_answer: str | None = None
    error_detail: str | None = None
    saw_done = False
    persisted = False

    def stopped_exchange_saved() -> bool:
        return session_store.last_exchange_matches(
            session_id,
            question,
            answer_contains=STOPPED_MARKER,
        )

    def persist_done_answer(answer: str) -> None:
        nonlocal persisted
        session = session_store.get_session(session_id)
        already_saved_abort = (
            stopped_exchange_saved()
            and (ABORTED_ANSWER in answer or bool(session and session["status"] == "aborting"))
        )
        if already_saved_abort:
            session_store.finish(session_id, "idle")
        else:
            session_store.append_exchange(session_id, question, answer)
        persisted = True

    try:
        yield (
            "event: session\n"
            f"data: {json.dumps({'session_id': session_id, 'status': 'running'}, ensure_ascii=False)}\n\n"
        )
        for payload in stream_agent(question, history, session_id):
            event_name, data = _parse_sse_event(payload)
            if event_name == "done" and data is not None:
                final_answer = str(data.get("answer", ""))
                saw_done = True
                persist_done_answer(final_answer)
            elif event_name == "error":
                session = session_store.get_session(session_id)
                if stopped_exchange_saved() and session and session["status"] == "aborting":
                    session_store.finish(session_id, "idle")
                    saw_done = True
                    persisted = True
                    continue
                raw_detail = data.get("detail", "Agent 执行失败") if data is not None else "Agent 执行失败"
                error_detail = redact_text(raw_detail)
                payload = _sse("error", {"detail": error_detail})
            yield payload

        if saw_done:
            if not persisted:
                persist_done_answer(final_answer or "")
        elif error_detail:
            session_store.finish(session_id, "error", error_detail)
        else:
            error_detail = "stream ended before completion"
            session_store.finish(session_id, "error", error_detail)
            yield (
                "event: error\n"
                f"data: {json.dumps({'detail': error_detail}, ensure_ascii=False)}\n\n"
            )
    except GeneratorExit:
        if persisted or saw_done:
            session_store.finish(session_id, "idle")
        elif error_detail:
            session_store.finish(session_id, "error", error_detail)
        else:
            session = session_store.get_session(session_id)
            user_requested_stop = bool(session and session["status"] == "aborting") or stopped_exchange_saved()
            session_store.finish(session_id, "idle", None if user_requested_stop else "client disconnected")
        raise
    except Exception as exc:
        session = session_store.get_session(session_id)
        if stopped_exchange_saved() and session and session["status"] == "aborting":
            session_store.finish(session_id, "idle")
            return
        session_store.finish(session_id, "error", public_exception_message("Chat stream failed", exc))
        raise


@router.post("/chat", summary="Chat with the Agent via SSE")
def chat(req: ChatRequest) -> StreamingResponse:
    if not chat_configured():
        raise HTTPException(
            status_code=503,
            detail="对话模型尚未配置，请设置 CHAT_BASE_URL、CHAT_API_KEY 和 CHAT_MODEL。",
        )

    requested_session_id = req.session_id.strip() if req.session_id else None
    if requested_session_id and session_store.get_session(requested_session_id) is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{requested_session_id}")

    session = session_store.get_or_create_session(requested_session_id)
    session_id = session["session_id"]
    if not session_store.try_begin(session_id):
        raise HTTPException(status_code=409, detail="该会话正在生成回复，请先等待或停止生成。")
    clear_abort_flag(session_id)

    rows = session_store.get_messages(session_id, limit=agent_module.MAX_HISTORY_MESSAGES)
    history = _to_history(rows)

    return StreamingResponse(
        _stream_and_persist(req.question, history, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/sessions", summary="List chat sessions")
def list_sessions(
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    safe_limit = query_int(limit, 100, minimum=1, maximum=200)
    safe_offset = query_int(offset, 0, minimum=0)
    items = session_store.list_sessions(limit=safe_limit, offset=safe_offset)
    return {
        "total_count": session_store.count_sessions(),
        "returned": len(items),
        "offset": safe_offset,
        "items": items,
    }


@router.delete("/chat/sessions", summary="Delete multiple chat sessions")
def batch_delete_sessions(req: BatchDeleteSessionsRequest) -> dict[str, Any]:
    return session_store.delete_sessions(req.session_ids)


@router.post("/chat/sessions/delete", summary="Delete multiple chat sessions")
def batch_delete_sessions_post(req: BatchDeleteSessionsRequest) -> dict[str, Any]:
    return session_store.delete_sessions(req.session_ids)


@router.delete("/chat/{session_id}", summary="Delete a chat session")
def delete_session(session_id: SessionIdPath) -> dict[str, str]:
    result = session_store.delete_session_result(session_id)
    if result == "active":
        raise HTTPException(status_code=409, detail="请先停止生成，再删除该会话。")
    if result == "missing":
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return {"message": f"会话 {session_id} 已删除。"}


@router.patch("/chat/{session_id}", summary="Rename a chat session")
def rename_session(session_id: SessionIdPath, req: RenameSessionRequest) -> dict[str, Any]:
    try:
        session = session_store.rename_session(session_id, req.title)
    except session_store.ActiveSessionError as exc:
        raise HTTPException(status_code=409, detail="请先停止生成，再重命名该会话。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return session


@router.get("/chat/{session_id}/messages", summary="Get chat session messages")
def get_session_messages(
    session_id: SessionIdPath,
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if session_store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    safe_limit = query_optional_int(limit, None, minimum=1, maximum=1000)
    safe_offset = query_int(offset, 0, minimum=0)
    items = session_store.get_messages(session_id, limit=safe_limit, offset=safe_offset)
    return {
        "total_count": session_store.count_messages(session_id),
        "returned": len(items),
        "offset": safe_offset,
        "items": items,
    }


@router.get("/chat/{session_id}/status", summary="Get chat session status")
def get_session_status(session_id: SessionIdPath) -> dict[str, Any]:
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return session


@router.post("/chat/{session_id}/abort", summary="Abort session generation")
def abort_session(session_id: SessionIdPath, req: AbortSessionRequest | None = None) -> dict[str, str]:
    abort_state = session_store.request_abort(session_id)
    if abort_state == "missing":
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    if abort_state == "idle":
        session = session_store.get_session(session_id)
        if req and req.question and session and session.get("last_error") == "client disconnected":
            question = req.question.strip()
            if not session_store.last_exchange_matches(
                session_id,
                question,
                answer_contains=STOPPED_MARKER,
            ):
                session_store.append_exchange(session_id, question, _stopped_answer(req.partial_answer))
        return {"message": "No active generation for this session.", "status": "idle"}

    set_abort_flag(session_id)
    if req and req.question:
        question = req.question.strip()
        if abort_state != "aborting" or not session_store.last_exchange_matches(
            session_id,
            question,
            answer_contains=STOPPED_MARKER,
        ):
            session_store.append_exchange(session_id, question, _stopped_answer(req.partial_answer), final_status="aborting")
    return {"message": "Abort requested.", "status": "aborting"}
