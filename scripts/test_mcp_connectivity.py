#!/usr/bin/env python3
"""Minimal MCP connectivity and tool discovery test.

Environment variables:
  MCP_SERVER_URL       Required. HTTP endpoint for the MCP server.
  MCP_AUTHORIZATION    Optional. Authorization header value, for example
                       "Bearer <token>". Redacted from logs and output.
  MCP_EXTRA_HEADERS    Optional JSON object with additional HTTP headers.

Optional tool call test:
  MCP_TEST_TOOL_NAME       Tool name to call.
  MCP_TEST_TOOL_ARGUMENTS  JSON object with arguments for the tool call.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


OUTPUT_DIR = Path("outputs")
TOOLS_OUTPUT = OUTPUT_DIR / "mcp_tools.json"
RESULT_OUTPUT = OUTPUT_DIR / "mcp_test_result.json"


class McpTestError(Exception):
    """Error raised for expected MCP test failures."""


SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "key",
    "secret",
    "token",
}


def redact_string(value: str) -> str:
    authorization = os.getenv("MCP_AUTHORIZATION")
    if authorization:
        value = value.replace(authorization, "<redacted>")

    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc and parsed.query:
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        redacted_pairs = [
            (key, "<redacted>" if key.lower() in SENSITIVE_QUERY_KEYS else item)
            for key, item in pairs
        ]
        value = urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(redacted_pairs),
                parsed.fragment,
            )
        )
    return value


def redact(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in {"authorization", "proxy-authorization", "x-api-key", "api-key"}:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_string(value)
    return value


def load_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }

    authorization = os.getenv("MCP_AUTHORIZATION")
    if authorization:
        headers["Authorization"] = authorization

    extra_headers_raw = os.getenv("MCP_EXTRA_HEADERS")
    if extra_headers_raw:
        try:
            extra_headers = json.loads(extra_headers_raw)
        except json.JSONDecodeError as exc:
            raise McpTestError(f"MCP_EXTRA_HEADERS is not valid JSON: {exc}") from exc
        if not isinstance(extra_headers, dict):
            raise McpTestError("MCP_EXTRA_HEADERS must be a JSON object")
        for key, value in extra_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise McpTestError("MCP_EXTRA_HEADERS keys and values must be strings")
            headers[key] = value

    return headers


def parse_response(body: bytes, content_type: str) -> Any:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return None

    if "text/event-stream" in content_type:
        return parse_sse(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpTestError(f"Response was not valid JSON: {exc}; body={text[:500]!r}") from exc


def parse_sse(text: str) -> Any:
    events: list[dict[str, str]] = []
    current: dict[str, list[str]] = {}

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if current:
                events.append({key: "\n".join(value) for key, value in current.items()})
                current = {}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        current.setdefault(field, []).append(value)

    if current:
        events.append({key: "\n".join(value) for key, value in current.items()})

    for event in events:
        data = event.get("data")
        if not data:
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and ("jsonrpc" in payload or "result" in payload or "error" in payload):
            return payload

    raise McpTestError("SSE response did not contain a JSON-RPC payload")


def classify_http_error(exc: urllib.error.HTTPError) -> str:
    if exc.code in {401, 403}:
        return "认证失败或权限不足"
    if exc.code == 404:
        return "MCP 地址不存在或路径错误"
    if exc.code == 405:
        return "当前 MCP 地址不支持 HTTP POST JSON-RPC"
    return f"HTTP 请求失败，状态码 {exc.code}"


class McpHttpClient:
    def __init__(self, server_url: str, headers: dict[str, str]) -> None:
        self.server_url = server_url
        self.headers = headers
        self.next_id = 1
        self.session_id: str | None = None

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self.next_id
        self.next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        response = self._post(payload)
        if not isinstance(response, dict):
            raise McpTestError(f"{method} returned a non-object response")
        if response.get("id") != request_id:
            raise McpTestError(f"{method} returned an unexpected JSON-RPC id: {response.get('id')!r}")
        if "error" in response:
            raise McpTestError(f"{method} failed: {json.dumps(redact(response['error']), ensure_ascii=False)}")
        if "result" not in response:
            raise McpTestError(f"{method} response did not include result")
        return response["result"]

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        self._post(payload)

    def _post(self, payload: dict[str, Any]) -> Any:
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        request = urllib.request.Request(
            self.server_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                return parse_response(response.read(), response.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = body[:1000] if body else exc.reason
            raise McpTestError(f"{classify_http_error(exc)}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise McpTestError(f"连接失败: {exc.reason}") from exc
        except TimeoutError as exc:
            raise McpTestError("连接超时") from exc


def normalize_tools(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        raise McpTestError("tools/list result was not an object")
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise McpTestError("tools/list result did not contain a tools array")

    normalized: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise McpTestError(f"tools[{index}] was not an object")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise McpTestError(f"tools[{index}] did not include a valid name")
        normalized.append(
            {
                "name": name,
                "description": tool.get("description") if isinstance(tool.get("description"), str) else "",
                "input_schema": tool.get("inputSchema", tool.get("input_schema", {})),
            }
        )
    return normalized


def load_test_tool_arguments() -> tuple[str | None, dict[str, Any] | None]:
    tool_name = os.getenv("MCP_TEST_TOOL_NAME")
    arguments_raw = os.getenv("MCP_TEST_TOOL_ARGUMENTS")
    if not tool_name:
        return None, None
    if not arguments_raw:
        return tool_name, {}
    try:
        arguments = json.loads(arguments_raw)
    except json.JSONDecodeError as exc:
        raise McpTestError(f"MCP_TEST_TOOL_ARGUMENTS is not valid JSON: {exc}") from exc
    if not isinstance(arguments, dict):
        raise McpTestError("MCP_TEST_TOOL_ARGUMENTS must be a JSON object")
    return tool_name, arguments


def write_json(path: Path, data: Any) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    result: dict[str, Any] = {
        "started_at": started_at,
        "server_url": os.getenv("MCP_SERVER_URL"),
        "steps": [],
        "success": False,
    }

    try:
        server_url = os.getenv("MCP_SERVER_URL")
        if not server_url:
            raise McpTestError("MCP_SERVER_URL is required")
        parsed = urllib.parse.urlparse(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McpTestError("MCP_SERVER_URL must be an absolute http(s) URL")

        headers = load_headers()
        result["request_headers"] = redact(headers)
        client = McpHttpClient(server_url, headers)

        initialize_result = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "ds-auto-report-mcp-test", "version": "0.1.0"},
            },
        )
        result["steps"].append({"name": "initialize", "success": True, "result": initialize_result})

        try:
            client.notify("notifications/initialized")
            result["steps"].append({"name": "notifications/initialized", "success": True})
        except McpTestError as exc:
            result["steps"].append({"name": "notifications/initialized", "success": False, "error": str(exc)})

        tools_result = client.request("tools/list")
        tools = normalize_tools(tools_result)
        write_json(TOOLS_OUTPUT, tools)
        result["steps"].append({"name": "tools/list", "success": True, "tool_count": len(tools)})

        tool_name, arguments = load_test_tool_arguments()
        if tool_name:
            if tool_name not in {tool["name"] for tool in tools}:
                raise McpTestError(f"MCP_TEST_TOOL_NAME={tool_name!r} was not found in tools/list")
            try:
                call_result = client.request("tools/call", {"name": tool_name, "arguments": arguments or {}})
                result["steps"].append(
                    {"name": "tools/call", "success": True, "tool_name": tool_name, "result": call_result}
                )
            except McpTestError as exc:
                result["steps"].append({"name": "tools/call", "success": False, "tool_name": tool_name, "error": str(exc)})
        else:
            result["steps"].append(
                {
                    "name": "tools/call",
                    "success": None,
                    "skipped": True,
                    "reason": "Set MCP_TEST_TOOL_NAME and optional MCP_TEST_TOOL_ARGUMENTS to run a minimal tool call.",
                }
            )

        result["success"] = True
        print(f"工具列表已写入: {TOOLS_OUTPUT}")
        print(f"测试结果已写入: {RESULT_OUTPUT}")
        print(json.dumps({"success": True, "tool_count": len(tools)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        result["success"] = False
        result["error"] = str(exc)
        result["error_type"] = exc.__class__.__name__
        if os.getenv("MCP_DEBUG") == "1":
            result["traceback"] = traceback.format_exc()
        print(f"测试失败: {exc}", file=sys.stderr)
        return 1
    finally:
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_json(RESULT_OUTPUT, result)


if __name__ == "__main__":
    raise SystemExit(main())
