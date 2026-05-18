#!/usr/bin/env python3
"""Build a report data package from MCP tools.

This script intentionally does not call an LLM and does not generate report
content. It only calls MCP data tools and writes a normalized JSON package.

Required environment variables:
  MCP_SERVER_URL

Optional environment variables:
  MCP_AUTHORIZATION
  MCP_EXTRA_HEADERS       JSON object with extra HTTP headers.
  QUERY_CONFIG_JSON       JSON object for query_config.
  QUERY_CONFIG_FILE       Path to a JSON file containing query_config.

Optional CLI arguments:
  --query-config-file path
  --query-config-json '{"brand":"..."}'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from brand_mapping import get_mcp_query_brand_for_config


OUTPUT_DIR = Path("outputs")
REPORT_DATA_PACKAGE_OUTPUT = OUTPUT_DIR / "report_data_package.json"

DEFAULT_DATA_SOURCES = ["微信", "新闻", "小红书", "微博", "抖音app", "论坛", "视频", "问答"]
DEFAULT_QUERY_CONFIG = {
    "brand": "海信",
    "competitors": ["美的", "海尔", "TCL"],
    "start_date": "2025-01-01",
    "end_date": "2025-01-07",
    "data_sources": ["微博", "小红书", "抖音app"],
    "keywords": ["海信", "海信+电视", "Vidda"],
    "filter_words": ["京东+年货节", "抽奖", "广告"],
}

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

URL_PATTERN = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
AUTHORIZATION_PATTERN = re.compile(
    r"\b(authorization)\b(\s*[:=]?\s*)(bearer\s+)?[^\s,\"'{}\]]+",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(r"\b(bearer|bear)\s+[A-Za-z0-9._~+/\-=]+", re.IGNORECASE)
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"\b(access_token|api_key|apikey|token|auth|secret)\b(\s*[:=]\s*)([\"']?)[^&\s,\"'{}\]]+",
    re.IGNORECASE,
)


class McpError(Exception):
    """Raised for expected MCP request and response failures."""


def redact_basic_string(value: str) -> str:
    authorization = os.getenv("MCP_AUTHORIZATION")
    if authorization:
        value = value.replace(authorization, "<redacted>")

    value = AUTHORIZATION_PATTERN.sub(r"\1\2<redacted>", value)
    value = BEARER_PATTERN.sub(r"\1 <redacted>", value)
    value = SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\1\2\3<redacted>", value)
    return value


def redact_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return url

    if parsed.scheme in {"http", "https"} and parsed.netloc and parsed.query:
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        redacted_pairs = [
            (key, "<redacted>" if key.lower() in SENSITIVE_QUERY_KEYS else item)
            for key, item in pairs
        ]
        return urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(redacted_pairs, safe="<>"),
                parsed.fragment,
            )
        )
    return url


def redact_string(value: str) -> str:
    value = redact_basic_string(value)
    if "http://" not in value.lower() and "https://" not in value.lower():
        return value
    return URL_PATTERN.sub(lambda match: redact_url(match.group(0)), value)


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


def redact_basic(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in {"authorization", "proxy-authorization", "x-api-key", "api-key"}:
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_basic(item)
        return redacted
    if isinstance(value, list):
        return [redact_basic(item) for item in value]
    if isinstance(value, str):
        return redact_basic_string(value)
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
            raise McpError(f"MCP_EXTRA_HEADERS is not valid JSON: {exc}") from exc
        if not isinstance(extra_headers, dict):
            raise McpError("MCP_EXTRA_HEADERS must be a JSON object")
        for key, value in extra_headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise McpError("MCP_EXTRA_HEADERS keys and values must be strings")
            headers[key] = value
    return headers


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
    raise McpError("SSE response did not contain a JSON-RPC payload")


def parse_response(body: bytes, content_type: str) -> Any:
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    if "text/event-stream" in content_type:
        return parse_sse(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpError(f"Response was not valid JSON: {exc}; body={text[:500]!r}") from exc


def classify_http_error(exc: urllib.error.HTTPError) -> str:
    if exc.code in {401, 403}:
        return "authentication failed or permission denied"
    if exc.code == 404:
        return "MCP URL was not found"
    if exc.code == 405:
        return "MCP URL does not support HTTP POST JSON-RPC"
    return f"HTTP request failed with status {exc.code}"


class McpHttpClient:
    def __init__(self, server_url: str, headers: dict[str, str]) -> None:
        self.server_url = server_url
        self.headers = headers
        self.next_id = 1
        self.session_id: str | None = None

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self.next_id
        self.next_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        response = self._post(payload)
        if not isinstance(response, dict):
            raise McpError(f"{method} returned a non-object response")
        if response.get("id") != request_id:
            raise McpError(f"{method} returned an unexpected JSON-RPC id: {response.get('id')!r}")
        if "error" in response:
            raise McpError(f"{method} failed: {json.dumps(redact(response['error']), ensure_ascii=False)}")
        if "result" not in response:
            raise McpError(f"{method} response did not include result")
        return response["result"]

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
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
            with urllib.request.urlopen(request, timeout=60) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                return parse_response(response.read(), response.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = body[:1000] if body else exc.reason
            raise McpError(f"{classify_http_error(exc)}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"connection failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise McpError("connection timed out") from exc


def parse_mcp_tool_text_result(result: Any) -> Any:
    """Parse result.content[0].text as JSON when MCP returns text content."""
    if not isinstance(result, dict):
        return result

    content = result.get("content")
    if not isinstance(content, list) or not content:
        return result

    first_content = content[0]
    if not isinstance(first_content, dict):
        return result

    text = first_content.get("text")
    if not isinstance(text, str):
        return result

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpError(f"result.content[0].text was not valid JSON: {exc}") from exc


class McpToolCaller:
    def __init__(self, client: McpHttpClient) -> None:
        self.client = client

    def initialize(self) -> Any:
        result = self.client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "ds-auto-report-data-package", "version": "0.1.0"},
            },
        )
        try:
            self.client.notify("notifications/initialized")
        except McpError:
            pass
        return result

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = self.client.request("tools/call", {"name": tool_name, "arguments": arguments})
        return parse_mcp_tool_text_result(result)


def ensure_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"query_config.{field_name} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"query_config.{field_name}[{index}] must be a string")
        if item:
            result.append(item)
    return result


def normalize_query_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_config, dict):
        raise ValueError("query_config must be a JSON object")

    brand = raw_config.get("brand")
    start_date = raw_config.get("start_date")
    end_date = raw_config.get("end_date")
    if not isinstance(brand, str) or not brand:
        raise ValueError("query_config.brand is required")
    if not isinstance(start_date, str) or not start_date:
        raise ValueError("query_config.start_date is required")
    if not isinstance(end_date, str) or not end_date:
        raise ValueError("query_config.end_date is required")

    data_sources = ensure_string_list(raw_config.get("data_sources"), "data_sources")
    if not data_sources:
        data_sources = list(DEFAULT_DATA_SOURCES)

    return {
        "brand": brand,
        "competitors": ensure_string_list(raw_config.get("competitors"), "competitors"),
        "start_date": start_date,
        "end_date": end_date,
        "data_sources": data_sources,
        "keywords": ensure_string_list(raw_config.get("keywords"), "keywords"),
        "filter_words": ensure_string_list(raw_config.get("filter_words"), "filter_words"),
    }


def base_arg0(query_config: dict[str, Any], brand: str) -> dict[str, Any]:
    return {
        "analysisObject": {"brand": get_mcp_query_brand_for_config(brand, query_config)},
        "startTimeStr": query_config["start_date"],
        "endTimeStr": query_config["end_date"],
        "dataSource": query_config["data_sources"],
        "keywords": query_config["keywords"],
        "filterWords": query_config["filter_words"],
    }


class BusinessMcpTools:
    def __init__(self, caller: McpToolCaller, query_config: dict[str, Any]) -> None:
        self.caller = caller
        self.query_config = query_config

    def get_volume_interaction_trend(self, brand: str) -> Any:
        arg0 = base_arg0(self.query_config, brand)
        arg0["statisticBy"] = "day"
        return self.caller.call_tool("getVolumeInteractionTrend", {"arg0": arg0})

    def get_posts(self, brand: str, count: int = 20) -> Any:
        arg0 = base_arg0(self.query_config, brand)
        arg0["sort"] = "titanInteractionCnt"
        arg0["count"] = count
        return self.caller.call_tool("getPosts", {"arg0": arg0})

    def get_nsr_trend(self, brand: str) -> Any:
        arg0 = base_arg0(self.query_config, brand)
        arg0["statisticBy"] = "day"
        return self.caller.call_tool("getNsrTrend", {"arg0": arg0})

    def get_top_brand(self, brand: str, count: int = 100) -> Any:
        arg0 = base_arg0(self.query_config, brand)
        arg0["sort"] = "volume"
        arg0["count"] = count
        return self.caller.call_tool("getTopBrand", {"arg0": arg0})

    def get_keyword_rank(self, brand: str, count: int = 100) -> Any:
        arg0 = base_arg0(self.query_config, brand)
        arg0["sort"] = "volume"
        arg0["count"] = count
        return self.caller.call_tool("getKeywordRank", {"arg0": arg0})


def collect_brand_data(tools: BusinessMcpTools, brand: str, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {"brand": brand, "data": {}}
    calls = [
        ("volume_interaction_trend", tools.get_volume_interaction_trend),
        ("posts", tools.get_posts),
        ("nsr_trend", tools.get_nsr_trend),
        ("top_brand", tools.get_top_brand),
        ("keyword_rank", tools.get_keyword_rank),
    ]

    for output_key, method in calls:
        try:
            data["data"][output_key] = method(brand)
        except Exception as exc:
            warnings.append(
                {
                    "brand": brand,
                    "tool": output_key,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )
            data["data"][output_key] = None
    return data


def build_report_data_package(query_config: dict[str, Any], caller: McpToolCaller) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    initialize_result = caller.initialize()
    tools = BusinessMcpTools(caller, query_config)

    own_brand = collect_brand_data(tools, query_config["brand"], warnings)
    competitors = [
        collect_brand_data(tools, competitor, warnings)
        for competitor in query_config["competitors"]
    ]

    return {
        "task_info": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "mcp_server_url": os.getenv("MCP_SERVER_URL"),
            "mcp_server_info": initialize_result.get("serverInfo") if isinstance(initialize_result, dict) else None,
            "query_config": query_config,
        },
        "own_brand": own_brand,
        "competitors": competitors,
        "warnings": warnings,
    }


def load_query_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.query_config_json:
        return normalize_query_config(json.loads(args.query_config_json))
    if args.query_config_file:
        return normalize_query_config(json.loads(Path(args.query_config_file).read_text(encoding="utf-8")))

    env_json = os.getenv("QUERY_CONFIG_JSON")
    if env_json:
        return normalize_query_config(json.loads(env_json))

    env_file = os.getenv("QUERY_CONFIG_FILE")
    if env_file:
        return normalize_query_config(json.loads(Path(env_file).read_text(encoding="utf-8")))

    return normalize_query_config(DEFAULT_QUERY_CONFIG)


def write_json(path: Path, data: Any) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        safe_data = redact(data)
    except Exception as exc:
        print(f"warning: full redaction failed, using basic redaction: {exc}", file=sys.stderr)
        safe_data = redact_basic(data)
    path.write_text(json.dumps(safe_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build outputs/report_data_package.json from MCP data tools.")
    parser.add_argument("--query-config-file", help="Path to query_config JSON file.")
    parser.add_argument("--query-config-json", help="Inline query_config JSON object.")
    return parser.parse_args()


def main() -> int:
    package: dict[str, Any] = {
        "task_info": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "mcp_server_url": os.getenv("MCP_SERVER_URL"),
        },
        "own_brand": None,
        "competitors": [],
        "warnings": [],
    }
    try:
        args = parse_args()
        server_url = os.getenv("MCP_SERVER_URL")
        if not server_url:
            raise McpError("MCP_SERVER_URL is required")
        parsed = urllib.parse.urlparse(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McpError("MCP_SERVER_URL must be an absolute http(s) URL")

        query_config = load_query_config(args)
        client = McpHttpClient(server_url, load_headers())
        caller = McpToolCaller(client)
        package = build_report_data_package(query_config, caller)
        write_json(REPORT_DATA_PACKAGE_OUTPUT, package)
        print(f"report data package written to: {REPORT_DATA_PACKAGE_OUTPUT}")
        print(json.dumps({"warnings": len(package["warnings"])}, ensure_ascii=False))
        return 0
    except Exception as exc:
        package["warnings"].append(
            {
                "stage": "build_report_data_package",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
        )
        if os.getenv("MCP_DEBUG") == "1":
            package["debug_traceback"] = traceback.format_exc()
        write_json(REPORT_DATA_PACKAGE_OUTPUT, package)
        print(f"failed to build report data package: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
