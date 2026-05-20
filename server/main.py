"""FastAPI application with WebSocket chat endpoint."""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Ensure the project root is on sys.path so we can import core modules
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.agent import Agent
from core.session import load_session as load_core_session
from core.tools import get_tool_definitions
from server.session_store import SessionStore
from server.web_agent import WebAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Persistent config file (survives server restarts)
_config_file = Path(__file__).parent / ".server_config.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    session_store.start_cleanup()
    yield
    await session_store.stop_cleanup()


app = FastAPI(title="Agent Web Interface", lifespan=lifespan)

# Session store
session_store = SessionStore(ttl_seconds=1800)


def _load_persistent_config() -> dict:
    """Load saved config from disk."""
    if _config_file.exists():
        try:
            return json.loads(_config_file.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load persistent config", exc_info=True)
    return {}


def _save_persistent_config(config: dict) -> None:
    """Save config to disk so it survives server restarts."""
    try:
        _config_file.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        logger.warning("Failed to save persistent config", exc_info=True)


# Load saved config on startup
_persistent_config = _load_persistent_config()
if _persistent_config:
    logger.info(f"Loaded persistent config: model={_persistent_config.get('model')}")

# Static files directory (mounted after routes to avoid conflicts)
static_dir = Path(__file__).parent / "static"


def _create_agent(config: dict | None = None, session_id: str | None = None) -> Agent:
    """Create a new Agent instance, mirroring CLI config resolution.

    Priority: client config (from frontend settings) > persistent config > env vars > defaults.

    Args:
        config: Optional per-session config overrides (model, api_key, api_base)
                from the frontend settings dialog.
        session_id: If provided, attempt to restore session from disk.
    """
    config = config or {}

    # Merge persistent config (survives restarts) with session config
    merged = dict(_persistent_config)
    merged.update(config)
    config = merged

    # ── Model: client config → env → default ──────────────────
    model = (
        config.get("model")
        or os.environ.get("AGENT_TEMPLATE_MODEL")
        or os.environ.get("MINI_CLAUDE_MODEL")
        or "claude-opus-4-6"
    )

    # ── API config: resolve from env first, then apply client overrides ──

    # Step 1: resolve from env (same logic as __main__.py)
    resolved_api_key: str | None = None
    resolved_api_base: str | None = None
    resolved_use_openai: bool = False

    env_has_openai_key = bool(os.environ.get("OPENAI_API_KEY"))
    env_has_openai_base = bool(os.environ.get("OPENAI_BASE_URL"))
    env_has_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    if env_has_openai_key and env_has_openai_base:
        resolved_api_key = os.environ["OPENAI_API_KEY"]
        resolved_api_base = os.environ["OPENAI_BASE_URL"]
        resolved_use_openai = True
    elif env_has_anthropic_key:
        resolved_api_key = os.environ["ANTHROPIC_API_KEY"]
        resolved_api_base = os.environ.get("ANTHROPIC_BASE_URL")
        resolved_use_openai = False
    elif env_has_openai_key:
        resolved_api_key = os.environ["OPENAI_API_KEY"]
        resolved_api_base = os.environ.get("OPENAI_BASE_URL")
        resolved_use_openai = True

    # Step 2: apply client config overrides (from frontend settings)
    client_key = config.get("api_key")
    client_base = config.get("api_base")

    if client_key:
        resolved_api_key = client_key
        if client_base:
            # Both key and base → OpenAI mode
            resolved_api_base = client_base
            resolved_use_openai = True
        else:
            # Key without base → keep the mode & base resolved from env
            # (preserves ANTHROPIC_BASE_URL / OPENAI_BASE_URL from env)
            resolved_use_openai = resolved_use_openai
    elif client_base:
        # Base URL without key → OpenAI mode, use env key or none
        resolved_api_base = client_base
        resolved_use_openai = True

    # Remove the "skill" tool from definitions — LLMs often confuse it with
    # directly available tools like literature_agent, lit_search, etc.
    _all_tools = get_tool_definitions()
    _web_tools = [t for t in _all_tools if t["name"] != "skill"]

    agent = Agent(
        permission_mode="default",
        model=model,
        api_base=resolved_api_base if resolved_use_openai else None,
        anthropic_base_url=resolved_api_base if not resolved_use_openai else None,
        api_key=resolved_api_key,
        custom_tools=_web_tools,
    )

    # Propagate API key to env so tools (e.g. literature_agent) can read it
    if resolved_api_key:
        os.environ["DEEPSEEK_API_KEY"] = resolved_api_key
        os.environ["OPENAI_API_KEY"] = resolved_api_key

    # Restore session from disk if available
    if session_id:
        session_data = load_core_session(session_id)
        if session_data:
            try:
                agent.restore_session(session_data)
                logger.info(f"Restored session {session_id} ({agent._get_message_count()} messages)")
            except Exception:
                logger.exception(f"Failed to restore session {session_id}")

    return agent


# ─── REST API ────────────────────────────────────────────────


@app.get("/")
async def get_index():
    """Serve the chat UI."""
    index_path = static_dir / "index.html"
    if not index_path.exists():
        return HTMLResponse(content="index.html not found", status_code=404)
    content = index_path.read_text(encoding="utf-8")
    return HTMLResponse(content=content, media_type="text/html")


@app.get("/api/sessions")
async def api_list_sessions():
    """List all sessions with metadata for the sidebar."""
    sessions = session_store.list_sessions()
    return JSONResponse(content=sessions)


@app.get("/api/sessions/{session_id}/messages")
async def api_get_messages(session_id: str):
    """Get message history for a session."""
    messages = session_store.get_messages(session_id)
    return JSONResponse(content=messages)


@app.get("/api/sessions/{session_id}/lit_state")
async def api_get_lit_state(session_id: str):
    """Get literature card state for a session (used after page refresh)."""
    state = session_store.get_lit_state(session_id)
    return JSONResponse(content=state)


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """Delete a session and its history."""
    session_store.remove(session_id)
    return JSONResponse(content={"success": True})


@app.get("/api/report")
async def api_get_report(path: str = "", download: bool = False):
    """Serve a generated HTML report file by absolute path."""
    if not path:
        return HTMLResponse("No path provided", status_code=400)
    file_path = Path(path).resolve()
    if not file_path.exists() or not file_path.is_file():
        return HTMLResponse("Report not found", status_code=404)
    if file_path.suffix == ".md":
        content = file_path.read_text(encoding="utf-8")
        # Wrap in a minimal HTML page for display
        escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Markdown Report</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 900px; margin: 0 auto; padding: 2em; line-height: 1.8; color: #333; background: #fff; }}
pre {{ background: #f5f5f5; padding: 1em; border-radius: 4px; overflow-x: auto; white-space: pre-wrap; word-wrap: break-word; }}
</style></head><body><pre>{escaped}</pre></body></html>"""
        resp = HTMLResponse(content=html_content, media_type="text/html")
        if download:
            resp.headers["Content-Disposition"] = f'attachment; filename="{file_path.name}"'
        return resp
    if file_path.suffix not in (".html", ".htm"):
        return HTMLResponse("Only HTML and Markdown files can be served", status_code=403)
    # Basic path traversal protection
    content = file_path.read_text(encoding="utf-8")
    resp = HTMLResponse(content=content, media_type="text/html")
    if download:
        resp.headers["Content-Disposition"] = f'attachment; filename="{file_path.name}"'
    return resp


@app.get("/api/get_latest_report")
async def api_get_latest_report():
    """Get the latest generated literature report path."""
    try:
        reports_dir = Path(__file__).resolve().parent.parent / "output" / "reports"
        if not reports_dir.exists():
            return JSONResponse(content={"error": "No reports directory"})
        html_files = sorted(reports_dir.glob("report_*.html"), key=os.path.getmtime, reverse=True)
        md_files = sorted(reports_dir.glob("report_*.md"), key=os.path.getmtime, reverse=True)
        if not html_files:
            return JSONResponse(content={"error": "No report found"})
        result = {"html_path": str(html_files[0])}
        if md_files:
            result["markdown_path"] = str(md_files[0])
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)})


# ─── PDF Upload ───────────────────────────────────────────────


@app.post("/api/upload_pdf")
async def api_upload_pdf(session_id: str = Form(...), file: UploadFile = File(...)):
    """Upload a PDF, extract text, and inject into session history."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return JSONResponse(content={"error": "Only PDF files are accepted"}, status_code=400)

    try:
        content = await file.read()
        import fitz
        doc = fitz.open(stream=content, filetype="pdf")
        extracted = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                extracted.append(text)
        doc.close()
        full_text = "\n".join(extracted)
        if not full_text.strip():
            return JSONResponse(content={"error": "No text could be extracted from this PDF (may be scanned images)"}, status_code=400)

        # Save to output/pdfs for reference
        pdf_dir = Path(_project_root) / "output" / "pdfs"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        save_path = pdf_dir / f"{int(__import__('time').time())}_{file.filename}"
        save_path.write_bytes(content)

        # 只存元数据，不把全文塞入对话
        config = session_store.get_config(session_id) or {}
        uploaded = config.get("uploaded_pdfs", [])
        uploaded.append({
            "filename": file.filename,
            "path": str(save_path),
            "char_count": len(full_text),
            "page_count": len(extracted),
            "text_content": full_text,
        })
        config["uploaded_pdfs"] = uploaded
        session_store.set_config(session_id, config)

        # 在消息历史中存一条轻量标记（不含全文），重新打开会话时显示 PDF 卡片
        session_store.append_message(session_id, "user", f"[上传PDF: {file.filename}] ({len(extracted)}页, {len(full_text)}字符)")

        logger.info("PDF uploaded: %s (%d chars)", file.filename, len(full_text))
        return JSONResponse(content={
            "success": True,
            "filename": file.filename,
            "char_count": len(full_text),
            "page_count": len(extracted),
        })
    except Exception as e:
        logger.exception("PDF upload error")
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ─── WebSocket ───────────────────────────────────────────────


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    logger.info(f"WebSocket connected: session={session_id}")

    # Get or create the WebAgent for this session
    web_agent: WebAgent | None = session_store.get(session_id)
    session_config = session_store.get_config(session_id)

    if web_agent is None:
        agent = _create_agent(session_config, session_id=session_id)
        web_agent = WebAgent(agent, websocket, session_store=session_store, session_id=session_id)
        session_store.set(session_id, web_agent)
    else:
        # Update the WebSocket reference (new connection)
        web_agent._ws = websocket
        web_agent._session_store = session_store
        web_agent._session_id = session_id
        web_agent._agent.set_emit_fn(web_agent._on_emit_text)
        web_agent._agent.confirm_fn = web_agent._on_confirm
        web_agent._agent._plan_approval_fn = web_agent._on_plan_approval
        web_agent._agent.set_progress_callback(lambda phase, data: None)
        web_agent._register_literature_v2_callback()

    try:
        async def _reader():
            """Read messages from the WebSocket client."""
            nonlocal web_agent
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from client: {raw[:200]}")
                    continue

                msg_type = data.get("type", "")

                if msg_type == "message":
                    message = data.get("content", "")
                    if message.strip():
                        asyncio.create_task(web_agent.run_chat(message))

                elif msg_type == "update_config":
                    # Merge with existing config so session_topic is not lost
                    existing = session_store.get_config(session_id) or {}
                    config = data.get("config", {})
                    existing.update(config)
                    session_store.set_config(session_id, existing)
                    # Persist to disk so it survives server restarts
                    _persistent_config.update(config)
                    _save_persistent_config(_persistent_config)
                    logger.info(f"Config saved to disk: model={config.get('model')}")
                    # Recreate agent with new config (preserve session history)
                    new_agent = _create_agent(config, session_id=session_id)
                    web_agent = WebAgent(new_agent, websocket, session_store=session_store, session_id=session_id)
                    session_store.set(session_id, web_agent)
                    logger.info(f"Session {session_id} config updated: model={config.get('model')}")

                elif msg_type in ("confirm_response", "plan_approval_response", "literature_confirm_response", "abort"):
                    web_agent.handle_client_message(data)

        await _reader()

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: session={session_id}")
    except Exception as e:
        logger.exception(f"WebSocket error for session={session_id}: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# Mount static files after all routes to avoid path conflicts
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
