"""Persistent session store with file-based message history."""

import asyncio
import json
import os
import time
import logging

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), ".session_data")
_HISTORY_FILE = os.path.join(_DATA_DIR, "histories.json")
_CONFIG_FILE = os.path.join(_DATA_DIR, "configs.json")


def _ensure_dir() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path: str, data: dict) -> None:
    _ensure_dir()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class SessionStore:
    """Manages WebAgent sessions with TTL expiration and persistent message history."""

    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._sessions: dict[str, tuple[object, float]] = {}  # id → (agent, last_access)
        self._configs: dict[str, dict] = _load_json(_CONFIG_FILE)
        self._histories: dict[str, list[dict]] = _load_json(_HISTORY_FILE)
        self._ttl = ttl_seconds
        self._cleanup_task: asyncio.Task | None = None
        self._dirty = False
        self._save_task: asyncio.Task | None = None

    def start_cleanup(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        if self._save_task is None:
            self._save_task = asyncio.create_task(self._save_loop())

    async def stop_cleanup(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        if self._save_task:
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
            self._save_task = None
        self._flush_save()

    def _flush_save(self) -> None:
        if self._dirty:
            _save_json(_HISTORY_FILE, self._histories)
            _save_json(_CONFIG_FILE, self._configs)
            self._dirty = False

    async def _save_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            self._flush_save()

    def get(self, session_id: str) -> object | None:
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        agent, _ = entry
        self._sessions[session_id] = (agent, time.time())
        return agent

    def set(self, session_id: str, agent: object) -> None:
        self._sessions[session_id] = (agent, time.time())

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._configs.pop(session_id, None)
        self._histories.pop(session_id, None)
        self._dirty = True
        self._flush_save()  # Save immediately so deletion survives page reload

    def get_config(self, session_id: str) -> dict | None:
        return self._configs.get(session_id)

    def set_config(self, session_id: str, config: dict) -> None:
        self._configs[session_id] = config
        self._dirty = True

    def get_or_create(
        self, session_id: str, factory
    ) -> object:
        existing = self.get(session_id)
        if existing is not None:
            return existing
        agent = factory()
        self.set(session_id, agent)
        return agent

    # ─── Message history ───────────────────────────────────────

    def append_message(self, session_id: str, role: str, content: str) -> None:
        if session_id not in self._histories:
            self._histories[session_id] = []
        self._histories[session_id].append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })
        self._dirty = True

    def get_messages(self, session_id: str) -> list[dict]:
        return self._histories.get(session_id, [])

    # ─── Literature state (cards) ──────────────────────────────

    def set_lit_state(self, session_id: str, lit_state: dict) -> None:
        """保存文献卡片状态（页面刷新后恢复用）"""
        config = self._configs.get(session_id, {})
        prev = config.get("_lit_state", {})
        prev.update(lit_state)
        config["_lit_state"] = prev
        self._configs[session_id] = config
        self._dirty = True

    def get_lit_state(self, session_id: str) -> dict:
        return (self._configs.get(session_id, {}) or {}).get("_lit_state", {})

    def list_sessions(self) -> list[dict]:
        """Return all sessions with metadata for the sidebar."""
        results = []
        for sid in set(list(self._histories.keys()) + list(self._sessions.keys())):
            history = self._histories.get(sid, [])
            # Use latest message timestamp for sorting (not last_access which changes on every click)
            last_msg_time = history[-1]["timestamp"] if history else 0

            # 优先使用会话存储的主题名，回退到第一条用户消息
            topic = ""
            config = self._configs.get(sid) or {}
            topic = config.get("session_topic", "")
            if not topic:
                for msg in history:
                    if msg["role"] == "user":
                        raw = msg["content"]
                        # 提取引号中的内容（通常是文章标题），更简洁
                        import re
                        m = re.search(r'[""""]([^"""]{5,50})["""]', raw)
                        if m:
                            topic = m.group(1)[:40]
                        else:
                            topic = raw[:40]
                        break
                if not topic and history:
                    topic = history[0]["content"][:40]

            results.append({
                "id": sid,
                "preview": topic or "",
                "message_count": len(history),
                "last_msg_time": last_msg_time,
                "config": self._configs.get(sid) or {},
            })
        # Sort by last message time descending (most recent first)
        results.sort(key=lambda s: s["last_msg_time"], reverse=True)
        return results

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [
                sid
                for sid, (_, last_access) in self._sessions.items()
                if now - last_access > self._ttl
            ]
            for sid in expired:
                logger.info(f"Removing expired session: {sid}")
                self._sessions.pop(sid, None)
