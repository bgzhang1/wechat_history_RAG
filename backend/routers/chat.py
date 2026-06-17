"""Chat router - SSE streaming endpoint wrapping the agent loop."""

from __future__ import annotations

import json
from collections.abc import Generator
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel, Field

import core.agent as agent_module
from core.llm import chat_configured

from .. import session_store
from ..agent_stream import clear_abort_flag, set_abort_flag, stream_agent
from ..schemas import ChatRequest


router = APIRouter(prefix="/api", tags=["chat"])


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class BatchDeleteSessionsRequest(BaseModel):
    session_ids: list[str] = Field(..., min_length=1, max_length=200)


def _to_history(rows: list[dict[str, Any]]) -> list[BaseMessage]:
    history: list[BaseMessage] = []
    for row in rows:
        if row["role"] == "user":
            history.append(HumanMessage(content=row["content"]))
        elif row["role"] == "assistant":
            history.append(AIMessage(content=row["content"]))
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


def _stream_and_persist(
    question: str,
    history: list[BaseMessage],
    session_id: str,
) -> Generator[str, None, None]:
    final_answer: str | None = None
    error_detail: str | None = None
    saw_done = False
    persisted = False

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
                session_store.append_exchange(session_id, question, final_answer)
                persisted = True
            elif event_name == "error" and data is not None:
                error_detail = str(data.get("detail", "Agent execution failed"))
            yield payload

        if saw_done and not persisted:
            session_store.append_exchange(session_id, question, final_answer or "")
        elif error_detail:
            session_store.finish(session_id, "error", error_detail)
        else:
            session_store.finish(session_id, "idle", "stream ended before completion")
    except GeneratorExit:
        session_store.finish(session_id, "idle", "client disconnected")
        raise
    except Exception as exc:
        session_store.finish(session_id, "error", f"{type(exc).__name__}: {exc}")
        raise


@router.post("/chat", summary="Chat with the Agent via SSE")
def chat(req: ChatRequest) -> StreamingResponse:
    if not chat_configured():
        raise HTTPException(
            status_code=503,
            detail="Chat model is not configured. Set CHAT_BASE_URL, CHAT_API_KEY, and CHAT_MODEL.",
        )

    session = session_store.get_or_create_session(req.session_id)
    session_id = session["session_id"]
    if not session_store.try_begin(session_id):
        raise HTTPException(status_code=409, detail="This session is already generating a response.")
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
) -> list[dict[str, Any]]:
    return session_store.list_sessions(limit=limit, offset=offset)


@router.delete("/chat/sessions", summary="Delete multiple chat sessions")
def batch_delete_sessions(req: BatchDeleteSessionsRequest) -> dict[str, Any]:
    return session_store.delete_sessions(req.session_ids)


@router.post("/chat/sessions/delete", summary="Delete multiple chat sessions")
def batch_delete_sessions_post(req: BatchDeleteSessionsRequest) -> dict[str, Any]:
    return session_store.delete_sessions(req.session_ids)


@router.delete("/chat/{session_id}", summary="Delete a chat session")
def delete_session(session_id: str) -> dict[str, str]:
    if not session_store.delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} does not exist.")
    return {"message": f"Session {session_id} deleted."}


@router.patch("/chat/{session_id}", summary="Rename a chat session")
def rename_session(session_id: str, req: RenameSessionRequest) -> dict[str, Any]:
    try:
        session = session_store.rename_session(session_id, req.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} does not exist.")
    return session


@router.get("/chat/{session_id}/messages", summary="Get chat session messages")
def get_session_messages(session_id: str) -> list[dict[str, Any]]:
    if session_store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} does not exist.")
    return session_store.get_messages(session_id)


@router.get("/chat/{session_id}/status", summary="Get chat session status")
def get_session_status(session_id: str) -> dict[str, Any]:
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} does not exist.")
    return session


@router.post("/chat/{session_id}/abort", summary="Abort session generation")
def abort_session(session_id: str) -> dict[str, str]:
    abort_state = session_store.request_abort(session_id)
    if abort_state == "missing":
        raise HTTPException(status_code=404, detail=f"Session {session_id} does not exist.")
    if abort_state == "idle":
        return {"message": "No active generation for this session.", "status": "idle"}

    set_abort_flag(session_id)
    return {"message": "Abort requested.", "status": "aborting"}
