"""WebAgent — wraps the core Agent for WebSocket-based communication."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebAgent:
    """Wraps a core Agent, routing output through a WebSocket connection."""

    def __init__(self, agent: Any, websocket: WebSocket, session_store=None, session_id: str = "") -> None:
        self._agent = agent
        self._ws = websocket
        self._session_store = session_store
        self._session_id = session_id
        self._confirm_future: asyncio.Future[bool] | None = None
        self._plan_future: asyncio.Future[dict] | None = None
        self._literature_confirm_event: threading.Event | None = None
        self._literature_confirm_result: dict = {}
        self._loop = asyncio.get_event_loop()
        self._current_tool_id: str | None = None
        self._current_tool_name: str | None = None
        self._response_accumulator: str = ""
        self._lit_state: dict = {
            "litDimensions": [],
            "litSearchResults": {},
            "litReport": None,
        }

        self._orig_tool_call: Callable | None = None
        self._orig_tool_result: Callable | None = None
        self._pdf_injected: bool = False  # 每次 WebSocket 连接只注入一次 PDF 全文

        agent.set_emit_fn(self._on_emit_text)
        agent.confirm_fn = self._on_confirm
        agent._plan_approval_fn = self._on_plan_approval
        agent._session_id = session_id
        # Set progress callback so literature tools run in a thread executor
        # (the actual progress is handled via the global callback registered below)
        agent.set_progress_callback(lambda phase, data: None)

        self._patch_ui()

        # 注册 v2 LiteratureAgent 进度回调（将进度发送到前端 WebSocket）
        self._register_literature_v2_callback()

    def _register_literature_v2_callback(self) -> None:
        """Register v2 LiteratureAgent progress callback with global instance."""
        try:
            from tools.literature_agent import set_global_progress_callback
            set_global_progress_callback(self._on_literature_progress_v2)
            logger.info("[LitProgress] Callback registered successfully")
        except Exception as e:
            logger.warning("[LitProgress] Failed to register callback: %s", e)

        # Also register confirm callback for literature_confirm tool
        try:
            from tools.literature_agent import set_global_confirm_callback
            set_global_confirm_callback(self._sync_literature_confirm)
            logger.info("[LitConfirm] Confirm callback registered successfully")
        except Exception as e:
            logger.warning("[LitConfirm] Failed to register confirm callback: %s", e)

    def _patch_ui(self) -> None:
        """Replace print_tool_call/print_tool_result to also send via WebSocket."""
        import core.ui as ui

        self._orig_tool_call = ui.print_tool_call
        self._orig_tool_result = ui.print_tool_result

        web_agent = self

        def patched_tool_call(name: str, inp: dict) -> None:
            web_agent._orig_tool_call(name, inp)
            tool_id = f"tool-{asyncio.get_event_loop().time()}"
            web_agent._current_tool_id = tool_id
            web_agent._current_tool_name = name
            asyncio.ensure_future(web_agent.send_json({
                "type": "tool_call",
                "id": tool_id,
                "name": name,
                "input": inp,
            }))

        def patched_tool_result(name: str, result: str) -> None:
            web_agent._orig_tool_result(name, result)
            tool_id = web_agent._current_tool_id or f"tool-{asyncio.get_event_loop().time()}"
            asyncio.ensure_future(web_agent.send_json({
                "type": "tool_result",
                "id": tool_id,
                "name": name,
                "result": result,
            }))
            web_agent._current_tool_id = None
            web_agent._current_tool_name = None

        ui.print_tool_call = patched_tool_call
        ui.print_tool_result = patched_tool_result

    def _unpatch_ui(self) -> None:
        """Restore original ui functions."""
        import core.ui as ui
        if self._orig_tool_call:
            ui.print_tool_call = self._orig_tool_call
        if self._orig_tool_result:
            ui.print_tool_result = self._orig_tool_result

    async def send_json(self, data: dict) -> None:
        try:
            await self._ws.send_json(data)
        except Exception:
            logger.exception("WebSocket send error")

    def _on_emit_text(self, text: str) -> None:
        """Called synchronously by the agent for streaming text output."""
        self._response_accumulator += text
        asyncio.ensure_future(self.send_json({"type": "text_delta", "content": text}))

    async def _on_confirm(self, command: str) -> bool:
        """Called by the agent when a dangerous operation needs confirmation."""
        await self.send_json({
            "type": "confirm_request",
            "command": command,
        })
        loop = asyncio.get_event_loop()
        self._confirm_future = loop.create_future()
        try:
            result = await self._confirm_future
            return result
        finally:
            self._confirm_future = None

    async def _on_plan_approval(self, plan_content: str) -> dict:
        """Called by the agent when plan mode requests approval."""
        await self.send_json({
            "type": "plan_approval_request",
            "plan": plan_content,
        })
        loop = asyncio.get_event_loop()
        self._plan_future = loop.create_future()
        try:
            result = await self._plan_future
            return result
        finally:
            self._plan_future = None

    def _sync_literature_confirm(self, phase: str, message: str) -> dict:
        """Synchronous callback called from a worker thread — blocks until user responds.

        This is called by LiteratureConfirmTool.run() which runs in a thread executor.
        We send a WebSocket message to the frontend, then block on a threading.Event
        until the frontend responds.
        """
        # Send confirm request to frontend via the event loop
        future = asyncio.run_coroutine_threadsafe(
            self.send_json({
                "type": "literature_confirm_request",
                "phase": phase,
                "message": message,
            }),
            self._loop,
        )
        try:
            future.result(timeout=5)
        except Exception as e:
            logger.warning("[LitConfirm] Failed to send confirm request: %s", e)
            return {"confirmed": True, "feedback": ""}

        # Block until the frontend responds
        event = threading.Event()
        self._literature_confirm_event = event
        self._literature_confirm_result = {}
        event.wait()

        result = self._literature_confirm_result
        self._literature_confirm_event = None
        self._literature_confirm_result = {}
        return result

    # ─── Literature pipeline progress (v2 契约式设计) ───────

    async def _send_literature_step_v2(self, phase: str, message_text: str, data: dict | None) -> None:
        """Send a structured literature pipeline step to the client (v2 format)."""
        msg = {
            "type": "literature_step",
            "phase": phase,
            "summary": message_text,
            "data": data or {},
            "timestamp": asyncio.get_event_loop().time()
        }
        await self.send_json(msg)

    def _on_literature_progress_v2(self, update: object) -> None:
        """Callback for v2 LiteratureAgent ProgressUpdate.

        set_global_progress_callback() 会将此函数注册为
        LiteratureAgent 的 progress_callback。
        update 是 ProgressUpdate 对象（来自 tools.literature_agent）。
        """
        try:
            phase = getattr(update, 'phase', 'unknown')
            if hasattr(phase, 'value'):
                phase = phase.value
            else:
                phase = str(phase)
            message_text = str(getattr(update, 'message', ''))
            data = getattr(update, 'data', None)
            if hasattr(data, 'model_dump'):
                data = data.model_dump()
            data_keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            logger.info("[LitProgress] phase=%s data_keys=%s msg=%s", phase, data_keys, message_text[:60])
            # 保存会话主题名到 session store
            if phase == "planning" and isinstance(data, dict) and data.get("session_topic"):
                topic = data.pop("session_topic")  # 从 data 中移除，不发送给前端
                if self._session_store:
                    config = self._session_store.get_config(self._session_id) or {}
                    config["session_topic"] = topic
                    self._session_store.set_config(self._session_id, config)
            # 保存文献卡片状态（页面刷新后恢复用）
            if isinstance(data, dict) and self._session_store:
                if phase == "planning" and "dimensions" in data:
                    self._lit_state["litDimensions"] = data["dimensions"]
                elif phase == "searching" and data.get("dimension_id"):
                    self._lit_state["litSearchResults"][data["dimension_id"]] = {
                        "dimension_name": data.get("dimension_name", ""),
                        "papers": data.get("papers", []),
                        "paper_count": data.get("paper_count", 0),
                    }
                elif phase == "completed":
                    self._lit_state["litReport"] = {
                        "title": data.get("title"),
                        "summary": data.get("summary"),
                        "total_papers": data.get("total_papers"),
                        "has_html": data.get("has_html"),
                        "has_markdown": data.get("has_markdown"),
                        "html_path": data.get("html_path"),
                        "markdown_path": data.get("markdown_path"),
                    }
                self._session_store.set_lit_state(self._session_id, self._lit_state)
            asyncio.run_coroutine_threadsafe(
                self._send_literature_step_v2(phase, message_text, data), self._loop
            )
        except Exception as exc:
            logger.exception("Error in literature progress v2 callback: %s", exc)

    def handle_client_message(self, data: dict) -> None:
        """Process an incoming message from the WebSocket client."""
        msg_type = data.get("type", "")

        if msg_type == "confirm_response":
            if self._confirm_future and not self._confirm_future.done():
                self._confirm_future.set_result(data.get("allowed", False))

        elif msg_type == "plan_approval_response":
            if self._plan_future and not self._plan_future.done():
                self._plan_future.set_result({
                    "choice": data.get("choice", "manual-execute"),
                    "feedback": data.get("feedback"),
                })

        elif msg_type == "literature_confirm_response":
            if self._literature_confirm_event and not self._literature_confirm_event.is_set():
                self._literature_confirm_result = {
                    "confirmed": data.get("confirmed", False),
                    "feedback": data.get("feedback", ""),
                }
                self._literature_confirm_event.set()

        elif msg_type == "abort":
            self._agent.abort()

    async def run_chat(self, message: str) -> None:
        """Run the agent with the given user message."""
        # 自动生成会话标题（如尚未设置）
        if self._session_store:
            config = self._session_store.get_config(self._session_id) or {}
            existing = config.get("session_topic")
            logger.info("[Title] session=%s has_topic=%s", self._session_id, bool(existing))
            if not existing:
                topic = await self._agent.generate_title(message)
                logger.info("[Title] generated=%s", topic)
                if topic:
                    config["session_topic"] = topic
                    self._session_store.set_config(self._session_id, config)

        # If a literature_confirm is pending, resolve it with the user's message as feedback
        if self._literature_confirm_event and not self._literature_confirm_event.is_set():
            logger.info("[LitConfirm] User sent message while confirm pending — using as feedback")
            self._literature_confirm_result = {
                "confirmed": False,
                "feedback": message,
            }
            self._literature_confirm_event.set()
            # Don't start a new chat — the pending tool will return with the user's feedback
            return

        # 将上传的 PDF 全文注入到当前消息之前（每个 WebSocket 连接只注入一次）
        llm_message = message  # 传给 LLM 的消息（可能包含 PDF 内容）
        if self._session_store and not self._pdf_injected:
            config = self._session_store.get_config(self._session_id) or {}
            uploaded = config.get("uploaded_pdfs", [])
            pending = [p for p in uploaded if p.get("text_content")]
            if pending:
                pdf_prefix = "\n\n".join(
                    f"[用户上传了PDF文件: {p['filename']} ({p.get('page_count', '?')}页)]\n\n{p['text_content']}"
                    for p in pending
                )
                llm_message = pdf_prefix + "\n\n=== 以下是用户的问题 ===\n\n" + message
                self._pdf_injected = True
                logger.info("Injected %d PDF(s) into message context", len(pending))

        if self._session_store:
            self._session_store.append_message(self._session_id, "user", message)

        self._response_accumulator = ""

        try:
            await self._agent.chat(llm_message)
            if self._session_store and self._response_accumulator.strip():
                self._session_store.append_message(
                    self._session_id, "assistant", self._response_accumulator.strip()
                )
            await self.send_json({"type": "done"})
        except Exception as e:
            logger.exception("Agent chat error")
            if self._session_store and self._response_accumulator.strip():
                self._session_store.append_message(
                    self._session_id, "assistant", self._response_accumulator.strip() + "\n\n[Error: " + str(e) + "]"
                )
            await self.send_json({"type": "error", "message": str(e)})