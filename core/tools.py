"""Tool definitions and execution — 12 tools with 5 permission modes.
Mirrors Claude Code's tool system: read_file, write_file, edit_file, list_files,
grep_search, run_shell, skill, web_fetch, enter/exit_plan_mode, agent, tool_search."""

from __future__ import annotations

import asyncio
import fnmatch
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tools.base import BaseTool

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ─── Permission modes ──────────────────────────────────────

PermissionMode = str  # "default" | "plan" | "acceptEdits" | "bypassPermissions" | "dontAsk"

READ_TOOLS: set[str] = set()
EDIT_TOOLS: set[str] = set()

# Concurrency-safe tools can run in parallel (read-only, no side effects)
CONCURRENCY_SAFE_TOOLS: set[str] = set()
WORKFLOW_CONFIRM_TOOLS: set[str] = set()

IS_WIN = sys.platform == "win32"

# ─── Type alias ──────────────────────────────────────────────

ToolDef = dict  # Anthropic tool schema dict

tool_definitions: list[ToolDef] = []
_activated_tools: set[str] = set()


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file. Returns the file content with line numbers."
    input_schema = {
        "type": "object",
        "properties": {"file_path": {"type": "string", "description": "The path to the file to read"}},
        "required": ["file_path"],
    }
    read_only = True
    concurrency_safe = True

    def run(self, inp: dict) -> str:
        return _read_file(inp)


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file. Creates the file if it doesn't exist, overwrites if it does."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The path to the file to write"},
            "content": {"type": "string", "description": "The content to write to the file"},
        },
        "required": ["file_path", "content"],
    }
    edit_operation = True

    def run(self, inp: dict) -> str:
        return _write_file(inp)


class EditFileTool(BaseTool):
    name = "edit_file"
    description = (
        "Edit a file by replacing an exact string match with new content. The old_string must match exactly (including whitespace and indentation)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "The path to the file to edit"},
            "old_string": {"type": "string", "description": "The exact string to find and replace"},
            "new_string": {"type": "string", "description": "The string to replace it with"},
        },
        "required": ["file_path", "old_string", "new_string"],
    }
    edit_operation = True

    def run(self, inp: dict) -> str:
        return _edit_file(inp)


class ListFilesTool(BaseTool):
    name = "list_files"
    description = "List files matching a glob pattern. Returns matching file paths."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": 'Glob pattern to match files (e.g., "**/*.ts", "src/**/*")'},
            "path": {"type": "string", "description": "Base directory to search from. Defaults to current directory."},
        },
        "required": ["pattern"],
    }
    read_only = True
    concurrency_safe = True

    def run(self, inp: dict) -> str:
        return _list_files(inp)


class GrepSearchTool(BaseTool):
    name = "grep_search"
    description = "Search for a pattern in files. Returns matching lines with file paths and line numbers."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "The regex pattern to search for"},
            "path": {"type": "string", "description": "Directory or file to search in. Defaults to current directory."},
            "include": {"type": "string", "description": 'File glob pattern to include (e.g., "*.ts", "*.py")'},
        },
        "required": ["pattern"],
    }
    read_only = True
    concurrency_safe = True

    def run(self, inp: dict) -> str:
        return _grep_search(inp)


class RunShellTool(BaseTool):
    name = "run_shell"
    description = "Execute a shell command and return its output. Use this for running tests, installing packages, git operations, etc."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute"},
            "timeout": {"type": "number", "description": "Timeout in milliseconds (default: 300000). Use <=0 to disable timeout."},
        },
        "required": ["command"],
    }

    def run(self, inp: dict) -> str:
        return _run_shell(inp)


class SkillTool(BaseTool):
    name = "skill"
    description = "Invoke a registered skill by name. Returns the skill's resolved prompt to follow."
    input_schema = {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "The name of the skill to invoke"},
            "args": {"type": "string", "description": "Optional arguments to pass to the skill"},
        },
        "required": ["skill_name"],
    }

    def run(self, inp: dict) -> str:
        return "Error: skill tool is handled by the agent runtime"


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = (
        "Fetch a URL and return its content as text. For HTML pages, tags are stripped to return readable text. "
        "For JSON/text responses, content is returned directly."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch"},
            "max_length": {"type": "number", "description": "Maximum content length in characters (default 50000)"},
        },
        "required": ["url"],
    }
    read_only = True
    concurrency_safe = True

    def run(self, inp: dict) -> str:
        return _web_fetch(inp)


class EnterPlanModeTool(BaseTool):
    name = "enter_plan_mode"
    description = "Enter plan mode to switch to a read-only planning phase."
    input_schema = {"type": "object", "properties": {}}
    deferred = True
    read_only = True

    def run(self, inp: dict) -> str:
        return "Error: enter_plan_mode is handled by the agent runtime"


class ExitPlanModeTool(BaseTool):
    name = "exit_plan_mode"
    description = "Exit plan mode after you have finished writing your plan to the plan file."
    input_schema = {"type": "object", "properties": {}}
    deferred = True
    read_only = True

    def run(self, inp: dict) -> str:
        return "Error: exit_plan_mode is handled by the agent runtime"


class AgentTool(BaseTool):
    name = "agent"
    description = (
        "Launch a sub-agent to handle a task autonomously. Sub-agents have isolated context and return their result."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short (3-5 word) description of the sub-agent's task"},
            "prompt": {"type": "string", "description": "Detailed task instructions for the sub-agent"},
            "type": {"type": "string", "enum": ["explore", "plan", "general"], "description": "Agent type. Default: general"},
        },
        "required": ["description", "prompt"],
    }

    def run(self, inp: dict) -> str:
        return "Error: agent tool is handled by the agent runtime"


class ToolSearchTool(BaseTool):
    name = "tool_search"
    description = (
        "Search for available tools by name or keyword. Returns full schema definitions for matching deferred tools so you can use them."
    )
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Tool name or search keywords"}},
        "required": ["query"],
    }

    def run(self, inp: dict) -> str:
        return "Error: tool_search is handled by the tool runtime"


BASE_TOOLS = [
    ReadFileTool(),
    WriteFileTool(),
    EditFileTool(),
    ListFilesTool(),
    GrepSearchTool(),
    RunShellTool(),
    SkillTool(),
    WebFetchTool(),
    EnterPlanModeTool(),
    ExitPlanModeTool(),
    AgentTool(),
    ToolSearchTool(),
]

_TOOL_CACHE: list[BaseTool] | None = None
_TOOL_BY_NAME: dict[str, BaseTool] | None = None


def _load_tools_from_module(module_path: Path) -> list[BaseTool]:
    # Use deterministic module name so global state (e.g. LiteratureAgent singleton)
    # is shared with standard imports like "from tools.literature_agent import ..."
    rel_path = module_path.resolve().relative_to(PROJECT_ROOT.resolve())
    module_name = str(rel_path.with_suffix("")).replace("/", ".")
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

    loaded: list[BaseTool] = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if isinstance(attr, type) and issubclass(attr, BaseTool) and attr is not BaseTool:
            try:
                loaded.append(attr())
            except Exception:
                continue
    return loaded


def _build_tool_cache() -> None:
    global _TOOL_CACHE, _TOOL_BY_NAME
    global tool_definitions, READ_TOOLS, EDIT_TOOLS, CONCURRENCY_SAFE_TOOLS, WORKFLOW_CONFIRM_TOOLS
    if _TOOL_CACHE is not None and _TOOL_BY_NAME is not None:
        return

    tools: list[BaseTool] = list(BASE_TOOLS)
    tools_dir = PROJECT_ROOT / "tools"
    if tools_dir.is_dir():
        for p in tools_dir.glob("*.py"):
            if p.name in ("__init__.py", "base.py", "schema.py"):
                continue
            try:
                tools.extend(_load_tools_from_module(p))
            except Exception:
                continue

    by_name: dict[str, BaseTool] = {}
    for tool in tools:
        if tool.name in by_name:
            raise ValueError(f"Duplicate tool name detected: {tool.name}")
        by_name[tool.name] = tool

    _TOOL_CACHE = tools
    _TOOL_BY_NAME = by_name
    tool_definitions = [tool.definition() for tool in tools]
    READ_TOOLS = {tool.name for tool in tools if tool.read_only}
    EDIT_TOOLS = {tool.name for tool in tools if tool.edit_operation}
    CONCURRENCY_SAFE_TOOLS = {tool.name for tool in tools if tool.concurrency_safe}
    WORKFLOW_CONFIRM_TOOLS = {tool.name for tool in tools if tool.requires_confirmation}


def get_all_tools() -> list[BaseTool]:
    _build_tool_cache()
    return list(_TOOL_CACHE or [])


def get_tool_by_name(name: str) -> BaseTool | None:
    _build_tool_cache()
    if _TOOL_BY_NAME is None:
        return None
    return _TOOL_BY_NAME.get(name)


def get_deferred_tool_names() -> list[str]:
    _build_tool_cache()
    return [t.name for t in (_TOOL_CACHE or []) if t.deferred]


def get_active_tool_definitions(tools: list[ToolDef]) -> list[ToolDef]:
    if not _activated_tools:
        return [t for t in tools if not t.get("deferred")]
    return [
        t for t in tools
        if not t.get("deferred") or t["name"] in _activated_tools
    ]


def get_tool_definitions() -> list[ToolDef]:
    """Return tool definitions, ensuring the cache is built first."""
    _build_tool_cache()
    return tool_definitions


# ─── Core file I/O ──────────────────────────────────────────


def _read_file(inp: dict) -> str:
    file_path = inp.get("file_path", "")
    if not file_path:
        return "Error: file_path is required"
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found: {file_path}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        # Calculate padding for line numbers
        line_count = len(lines)
        padding = len(str(line_count))
        numbered = "\n".join(f"{i+1:>{padding}}│{line}" for i, line in enumerate(lines))
        info = f"File: {file_path} ({line_count} lines)"
        return f"{info}\n{numbered}"
    except Exception as e:
        return f"Error reading file: {e}"


def _write_file(inp: dict) -> str:
    file_path = inp.get("file_path", "")
    content = inp.get("content", "")
    if not file_path:
        return "Error: file_path is required"
    if content is None:
        content = ""
    path = Path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} characters to {file_path}"
    except Exception as e:
        return f"Error writing file: {e}"


def _edit_file(inp: dict) -> str:
    file_path = inp.get("file_path", "")
    old_string = inp.get("old_string", "")
    new_string = inp.get("new_string", "")
    if not file_path or not old_string:
        return "Error: file_path and old_string are required"
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found: {file_path}"
    try:
        text = path.read_text(encoding="utf-8")
        if old_string not in text:
            return f"Error: old_string not found in {file_path}. The exact text to replace must exist in the file."
        new_text = text.replace(old_string, new_string, 1)
        path.write_text(new_text, encoding="utf-8")
        return f"Successfully edited {file_path}"
    except Exception as e:
        return f"Error editing file: {e}"


def _list_files(inp: dict) -> str:
    pattern = inp.get("pattern", "")
    base_path = inp.get("path", "")
    if not pattern:
        return "Error: pattern is required"
    try:
        search_root = Path(base_path).resolve() if base_path else Path.cwd()
        if not search_root.exists():
            return f"Error: directory not found: {base_path}"
        matches = sorted(search_root.glob(pattern))
        if not matches:
            return f"No files matching '{pattern}' found in {search_root}"
        lines = [str(m.relative_to(search_root)) for m in matches]
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing files: {e}"


def _grep_search(inp: dict) -> str:
    pattern = inp.get("pattern", "")
    search_path = inp.get("path", "")
    include = inp.get("include", "")
    if not pattern:
        return "Error: pattern is required"
    try:
        cmd = ["rg", "-n", "--heading"]
        if include:
            cmd.extend(["--glob", include])
        cmd.append(pattern)
        if search_path:
            cmd.append(search_path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout or "(no matches found)"
        elif result.returncode == 1:
            return "(no matches found)"
        else:
            return f"Error searching: {result.stderr}"
    except FileNotFoundError:
        # Fallback to Python implementation if ripgrep is not available
        try:
            search_root = Path(search_path).resolve() if search_path else Path.cwd()
            matches: list[str] = []
            rgx = re.compile(pattern)
            for f in search_root.rglob("*"):
                if f.is_file():
                    if include and not fnmatch.fnmatch(f.name, include):
                        continue
                    try:
                        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                            if rgx.search(line):
                                matches.append(f"{f}:{i}:{line}")
                    except Exception:
                        pass
            if not matches:
                return "(no matches found)"
            return "\n".join(matches)
        except Exception as e:
            return f"Error searching files: {e}"


def _run_shell(inp: dict) -> str:
    try:
        timeout_ms = inp.get("timeout", 300000)
        timeout_s = None if timeout_ms is None or float(timeout_ms) <= 0 else float(timeout_ms) / 1000.0
        result = subprocess.run(
            inp["command"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        output = result.stdout or ""
        if result.returncode != 0:
            stderr = f"\nStderr: {result.stderr}" if result.stderr else ""
            stdout = f"\nStdout: {result.stdout}" if result.stdout else ""
            return f"Command failed (exit code {result.returncode}){stdout}{stderr}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {inp.get('timeout', 300000)}ms"
    except Exception as e:
        return f"Error: {e}"


def _web_fetch(inp: dict) -> str:
    import urllib.request
    import urllib.error

    url = inp.get("url", "")
    max_length = inp.get("max_length", 50000)
    req = urllib.request.Request(url, headers={"User-Agent": "agent-template/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return f"HTTP error: {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return f"Error fetching {url}: {e.reason}"
    except Exception as e:
        return f"Error fetching {url}: {e}"

    if "html" in content_type:
        text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]*>", " ", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        text = re.sub(r"\s{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

    if len(text) > max_length:
        text = text[:max_length] + f"\n\n[... truncated at {max_length} characters]"

    return text or "(empty response)"


# ─── Dangerous command patterns ─────────────────────────────

DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s"),
    re.compile(r"\bgit\s+(push|reset|clean|checkout\s+\.)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s"),
    re.compile(r">\s*/dev/"),
    re.compile(r"\bkill\b"),
    re.compile(r"\bpkill\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\bdel\s", re.IGNORECASE),
    re.compile(r"\brmdir\s", re.IGNORECASE),
    re.compile(r"\bformat\s", re.IGNORECASE),
    re.compile(r"\btaskkill\s", re.IGNORECASE),
    re.compile(r"\bRemove-Item\s", re.IGNORECASE),
    re.compile(r"\bStop-Process\s", re.IGNORECASE),
]


def is_dangerous(command: str) -> bool:
    return any(p.search(command) for p in DANGEROUS_PATTERNS)


# ─── Permission rules (.claude/settings.json) ───────────────


def _parse_rule(rule: str) -> dict:
    m = re.match(r"^([a-z_]+)\((.+)\)$", rule)
    if m:
        return {"tool": m.group(1), "pattern": m.group(2)}
    return {"tool": rule, "pattern": None}


def _load_settings(file_path: Path) -> dict | None:
    if not file_path.exists():
        return None
    try:
        return json.loads(file_path.read_text())
    except Exception:
        return None


_cached_rules: dict | None = None


def load_permission_rules() -> dict:
    global _cached_rules
    if _cached_rules is not None:
        return _cached_rules

    allow: list[dict] = []
    deny: list[dict] = []

    user_settings = _load_settings(Path.home() / ".claude" / "settings.json")
    project_settings = _load_settings(Path.cwd() / ".claude" / "settings.json")

    for settings in [user_settings, project_settings]:
        if not settings or "permissions" not in settings:
            continue
        perms = settings["permissions"]
        for r in perms.get("allow", []):
            allow.append(_parse_rule(r))
        for r in perms.get("deny", []):
            deny.append(_parse_rule(r))

    _cached_rules = {"allow": allow, "deny": deny}
    return _cached_rules


def _matches_rule(rule: dict, tool_name: str, inp: dict) -> bool:
    if rule["tool"] != tool_name:
        return False
    if rule["pattern"] is None:
        return True

    value = ""
    if tool_name == "run_shell":
        value = inp.get("command", "")
    elif "file_path" in inp:
        value = inp["file_path"]
    else:
        return True

    pattern = rule["pattern"]
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def _check_permission_rules(tool_name: str, inp: dict) -> str | None:
    rules = load_permission_rules()
    for rule in rules["deny"]:
        if _matches_rule(rule, tool_name, inp):
            return "deny"
    for rule in rules["allow"]:
        if _matches_rule(rule, tool_name, inp):
            return "allow"
    return None


def check_permission(
    tool_name: str,
    inp: dict,
    mode: str = "default",
    plan_file_path: str | None = None,
) -> dict:
    """Returns {"action": "allow"|"deny"|"confirm", "message": ...}"""
    if mode == "bypassPermissions":
        return {"action": "allow"}

    rule_result = _check_permission_rules(tool_name, inp)
    if rule_result == "deny":
        return {"action": "deny", "message": f"Denied by permission rule for {tool_name}"}
    if rule_result == "allow":
        return {"action": "allow"}

    if tool_name in READ_TOOLS:
        return {"action": "allow"}

    if mode == "plan":
        if tool_name in EDIT_TOOLS:
            file_path = inp.get("file_path") or inp.get("path")
            if plan_file_path and file_path == plan_file_path:
                return {"action": "allow"}
            return {"action": "deny", "message": f"Blocked in plan mode: {tool_name}"}
        if tool_name == "run_shell":
            return {"action": "deny", "message": "Shell commands blocked in plan mode"}

    if tool_name in ("enter_plan_mode", "exit_plan_mode"):
        return {"action": "allow"}

    if tool_name in WORKFLOW_CONFIRM_TOOLS:
        return {"action": "confirm", "message": f"Workflow confirmation step: {tool_name}"}

    if mode == "acceptEdits" and tool_name in EDIT_TOOLS:
        return {"action": "allow"}

    needs_confirm = False
    confirm_message = ""

    if tool_name == "run_shell" and is_dangerous(inp.get("command", "")):
        needs_confirm = True
        confirm_message = inp.get("command", "")
    elif tool_name == "write_file" and not Path(inp.get("file_path", "")).exists():
        needs_confirm = True
        confirm_message = f"write new file: {inp.get('file_path', '')}"
    elif tool_name == "edit_file" and not Path(inp.get("file_path", "")).exists():
        needs_confirm = True
        confirm_message = f"edit non-existent file: {inp.get('file_path', '')}"

    if needs_confirm:
        if mode == "dontAsk":
            return {"action": "deny", "message": f"Auto-denied (dontAsk mode): {confirm_message}"}
        return {"action": "confirm", "message": confirm_message}

    return {"action": "allow"}


# ─── Truncate long tool results ─────────────────────────────

MAX_RESULT_CHARS = 50000
MAX_JSON_RESPONSE_CHARS = 12000


def _truncate_result(result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result
    keep_each = (MAX_RESULT_CHARS - 60) // 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {len(result) - keep_each * 2} chars ...]\n\n"
        + result[-keep_each:]
    )


def _compact_json_value(
    value,
    *,
    max_depth: int = 3,
    max_items: int = 20,
    max_string_chars: int = 400,
):
    if max_depth <= 0:
        if isinstance(value, dict):
            return f"<dict with {len(value)} keys>"
        if isinstance(value, (list, tuple)):
            return f"<list with {len(value)} items>"
        return value

    if isinstance(value, dict):
        compact: dict = {}
        items = list(value.items())
        for k, v in items[:max_items]:
            compact[str(k)] = _compact_json_value(
                v,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
        if len(items) > max_items:
            compact["_truncated_keys"] = len(items) - max_items
        return compact

    if isinstance(value, (list, tuple)):
        compact_list = [
            _compact_json_value(
                item,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            compact_list.append(f"... ({len(value) - max_items} more items)")
        return compact_list

    if isinstance(value, str) and len(value) > max_string_chars:
        cut = len(value) - max_string_chars
        return value[:max_string_chars] + f" ... [truncated {cut} chars]"

    return value


def _json_response(payload: object, *, max_chars: int = MAX_JSON_RESPONSE_CHARS) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text

    compact_payload = {
        "_note": (
            "Result was compacted for chat context safety. "
            "Use read_file on generated artifact/handoff files for full details."
        ),
        "result": _compact_json_value(payload),
    }
    compact_text = json.dumps(compact_payload, ensure_ascii=False, indent=2)
    if len(compact_text) <= max_chars:
        return compact_text

    return compact_text[:max_chars] + "\n... [response truncated for context safety]"


# ─── Execute a tool call ────────────────────────────────────
# "agent" and "skill" tools are handled in agent.py to avoid circular deps.


async def execute_tool(
    name: str, inp: dict, read_file_state: dict[str, float] | None = None
) -> str:
    # Normalize common alias fields so downstream tools are more tolerant.
    if name == "write_file":
        if "file_path" not in inp:
            alias_path = inp.get("path") or inp.get("output_path")
            if alias_path is not None:
                inp["file_path"] = alias_path
        if "content" not in inp:
            alias_content = inp.get("text") or inp.get("new_content")
            if alias_content is not None:
                inp["content"] = alias_content

    if name == "edit_file":
        if "file_path" not in inp:
            alias_path = inp.get("path")
            if alias_path is not None:
                inp["file_path"] = alias_path
        if "old_string" not in inp:
            alias_old = inp.get("old") or inp.get("find")
            if alias_old is not None:
                inp["old_string"] = alias_old
        if "new_string" not in inp:
            alias_new = inp.get("new") or inp.get("replace")
            if alias_new is not None:
                inp["new_string"] = alias_new

    tool = get_tool_by_name(name)
    if tool is None:
        return f"Error: unknown tool '{name}'."

    missing = [k for k in tool.required_fields() if k not in inp]
    if missing:
        if name == "write_file":
            return (
                "Error: missing required input field(s) for write_file: "
                + ", ".join(missing)
                + '. Example: {"file_path": "scripts/validate.py", "content": "print(\'hello\')\\n"}.'
            )
        return (
            f"Error: missing required input field(s) for {name}: {', '.join(missing)}. "
            "Please provide all required tool arguments."
        )

    # ─── read-before-edit + mtime freshness checks ───────────
    if name == "read_file":
        result = _read_file(inp)
        if read_file_state is not None and not result.startswith("Error"):
            abs_path = str(Path(inp["file_path"]).resolve())
            try:
                read_file_state[abs_path] = os.path.getmtime(abs_path)
            except OSError:
                pass
        return _truncate_result(result)

    if name in ("write_file", "edit_file") and read_file_state is not None:
        file_path = inp.get("file_path")
        if not file_path:
            return f"Error: missing required input field for {name}: file_path"
        abs_path = str(Path(file_path).resolve())
        if os.path.exists(abs_path):
            if abs_path not in read_file_state:
                verb = "writing" if name == "write_file" else "editing"
                return f"Error: You must read this file before {verb}. Use read_file first to see its current contents."
            if os.path.getmtime(abs_path) != read_file_state[abs_path]:
                verb = "writing" if name == "write_file" else "editing"
                return f"Warning: {inp['file_path']} was modified externally since your last read. Please read_file again before {verb}."

    # tool_search: activate deferred tools and return their schemas
    if name == "tool_search":
        _build_tool_cache()
        query = (inp.get("query") or "").lower()
        deferred = [t for t in tool_definitions if t.get("deferred")]
        matches = [
            t for t in deferred
            if query in t["name"].lower() or query in (t.get("description") or "").lower()
        ]
        if not matches:
            return "No matching deferred tools found."
        for m in matches:
            _activated_tools.add(m["name"])
        return json.dumps(
            [{"name": t["name"], "description": t.get("description", ""), "input_schema": t["input_schema"]} for t in matches],
            indent=2,
        )

    # tool runtime: call the class-based tool
    try:
        if "_progress_callback" in inp:
            # Run in thread to avoid blocking event loop
            # so progress WebSocket messages can be sent during execution.
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: tool.run(inp))
        else:
            result = tool.run(inp)
    except Exception as e:
        return f"Error: {e}"

    if isinstance(result, str):
        return _truncate_result(result)
    return _truncate_result(_json_response(result))


def reset_permission_cache() -> None:
    global _cached_rules
    _cached_rules = None
