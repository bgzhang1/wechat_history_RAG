"""Chat router — SSE streaming endpoint wrapping the agent loop."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from wechat_rag_agent.llm import chat_configured

from ..agent_stream import stream_agent
from ..schemas import ChatRequest, ChatResponse


router = APIRouter(prefix="/api", tags=["chat"])

# In-memory session store: { session_id: list[BaseMessage] }
# For production, replace with Redis or a persistent store.
_sessions: dict[str, list[BaseMessage]] = {}

MAX_SESSIONS = 200


def _gc_sessions() -> None:
    """Evict oldest sessions when the pool is full."""
    while len(_sessions) > MAX_SESSIONS:
        oldest = next(iter(_sessions))
        del _sessions[oldest]


def _get_or_create_session(session_id: str | None) -> tuple[str, list[BaseMessage]]:
    if session_id and session_id in _sessions:
        return session_id, _sessions[session_id]
    new_id = session_id or str(uuid.uuid4())
    _sessions[new_id] = []
    _gc_sessions()
    return new_id, _sessions[new_id]


@router.post("/chat", summary="与 Agent 对话（SSE 流式）")
def chat(req: ChatRequest) -> StreamingResponse:
    """
    发送问题给 Agent，以 SSE 流式返回工具调用过程和最终回答。
    """
    if not chat_configured():
        raise HTTPException(
            status_code=503,
            detail="主模型未配置。请在 .env 中设置 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL。",
        )

    session_id, history = _get_or_create_session(req.session_id)

    return StreamingResponse(
        stream_agent(req.question, history, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/chat/{session_id}", summary="清除会话历史")
def delete_session(session_id: str) -> dict[str, str]:
    """删除指定会话的聊天历史。"""
    removed = _sessions.pop(session_id, None)
    if removed is None:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    return {"message": f"会话 {session_id} 已删除"}


@router.get("/chat/{session_id}/messages", summary="获取会话历史消息")
def get_session_messages(session_id: str) -> list[dict[str, str]]:
    """获取指定会话的历史消息记录。"""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    
    msgs = []
    for m in _sessions[session_id]:
        if isinstance(m, HumanMessage):
            msgs.append({"role": "user", "content": str(m.content)})
        elif isinstance(m, AIMessage):
            msgs.append({"role": "assistant", "content": str(m.content)})
    return msgs


@router.post("/chat/{session_id}/abort", summary="终止会话生成")
def abort_session(session_id: str) -> dict[str, str]:
    """强制终止指定会话正在进行的生成任务。"""
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    
    from ..agent_stream import set_abort_flag
    set_abort_flag(session_id)
    return {"message": "已发送终止指令"}


@router.get("/chat/sessions", summary="列出活跃会话")
def list_sessions() -> list[dict[str, Any]]:
    """列出所有活跃会话及其消息数。"""
    return [
        {
            "session_id": sid,
            "message_count": len(msgs),
            "last_question": next(
                (m.content for m in reversed(msgs) if isinstance(m, HumanMessage)),
                None,
            ),
        }
        for sid, msgs in _sessions.items()
    ]
