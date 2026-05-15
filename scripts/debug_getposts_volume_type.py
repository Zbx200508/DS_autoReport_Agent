#!/usr/bin/env python3
"""Debug whether getPosts arg0.volumeType can distinguish PGC/BGC/UGC posts.

This script is diagnostic only. It does not modify the Insight 3 input package,
does not call an LLM, and does not generate report content.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_report_data_package import McpError, McpHttpClient, McpToolCaller, load_headers, redact


QUERY_CONFIG_PATH = Path("configs") / "query_config.local.json"
OUTPUT_PATH = Path("outputs") / "debug" / "getposts_volume_type_test.json"
TEST_BRAND = "美的"
TEST_PLATFORM = "抖音"
TEST_DATA_SOURCE = ["抖音app"]
TEST_COUNT = 5
SORT_FIELD = "titanInteractionCnt"
VOLUME_TYPES = ["PGC", "BGC", "UGC"]


def read_query_config() -> dict[str, Any]:
    if not QUERY_CONFIG_PATH.exists():
        raise FileNotFoundError(f"query_config file does not exist: {QUERY_CONFIG_PATH}")
    payload = json.loads(QUERY_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("query_config must be a JSON object")
    return payload


def build_arg0(query_config: dict[str, Any], volume_type: str | None, request_shape: str) -> dict[str, Any]:
    start_date = query_config.get("start_date")
    end_date = query_config.get("end_date")
    if not isinstance(start_date, str) or not start_date:
        raise ValueError("query_config.start_date is required")
    if not isinstance(end_date, str) or not end_date:
        raise ValueError("query_config.end_date is required")

    arg0: dict[str, Any] = {
        "sort": SORT_FIELD,
        "count": TEST_COUNT,
        "analysisObject": {"brand": TEST_BRAND},
        "startTimeStr": start_date,
        "endTimeStr": end_date,
        "dataSource": TEST_DATA_SOURCE,
    }
    if volume_type is not None:
        if request_shape == "array":
            arg0["volumeType"] = [volume_type]
        elif request_shape == "string":
            arg0["volumeType"] = volume_type
        else:
            raise ValueError(f"unsupported request_shape: {request_shape}")
    return arg0


def extract_posts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("posts", "data", "list", "rows", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def coerce_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.replace(",", "").strip()))
        except ValueError:
            return 0
    return 0


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def preview(value: Any, limit: int = 160) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def top_posts_summary(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    posts = sorted(posts, key=lambda item: coerce_int(item.get("interactionCnt") or item.get("interaction")), reverse=True)
    result = []
    for index, post in enumerate(posts[:TEST_COUNT], start=1):
        result.append(
            {
                "rank": index,
                "publishTime": clean_text(post.get("publishTime")),
                "author": clean_text(post.get("author")),
                "content_preview": preview(post.get("content")),
                "interactionCnt": coerce_int(post.get("interactionCnt") or post.get("interaction")),
                "url": clean_text(post.get("url")),
            }
        )
    return result


def is_type_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in ("type", "schema", "invalid", "array", "string", "参数", "类型", "格式"))


def call_getposts(
    caller: McpToolCaller,
    query_config: dict[str, Any],
    volume_type: str | None,
    request_shape: str,
) -> dict[str, Any]:
    label = volume_type or "omitted"
    try:
        payload = caller.call_tool("getPosts", {"arg0": build_arg0(query_config, volume_type, request_shape)})
        posts = extract_posts(payload)
        return {
            "volume_type": label,
            "request_shape": request_shape,
            "success": True,
            "returned_count": len(posts),
            "top_posts": top_posts_summary(posts),
            "error": None,
        }
    except Exception as exc:
        return {
            "volume_type": label,
            "request_shape": request_shape,
            "success": False,
            "returned_count": 0,
            "top_posts": [],
            "error": {
                "error_type": exc.__class__.__name__,
                "message": str(exc),
                "looks_like_type_error": is_type_error(exc),
            },
        }


def test_volume_type(caller: McpToolCaller, query_config: dict[str, Any], volume_type: str | None) -> dict[str, Any]:
    if volume_type is None:
        return call_getposts(caller, query_config, None, "omitted")

    array_result = call_getposts(caller, query_config, volume_type, "array")
    if array_result["success"] or not array_result.get("error", {}).get("looks_like_type_error"):
        return array_result

    string_result = call_getposts(caller, query_config, volume_type, "string")
    string_result["fallback_from_array_error"] = array_result["error"]
    return string_result


def url_signature(result: dict[str, Any]) -> tuple[str, ...]:
    return tuple(post.get("url", "") for post in result.get("top_posts", []) if post.get("url"))


def build_conclusion(results: list[dict[str, Any]]) -> dict[str, Any]:
    notes: list[str] = []
    typed_results = [result for result in results if result["volume_type"] in VOLUME_TYPES]
    omitted = next((result for result in results if result["volume_type"] == "omitted"), None)

    all_typed_success = all(result["success"] for result in typed_results)
    all_typed_non_empty = all(result["returned_count"] > 0 for result in typed_results)
    typed_signatures = {result["volume_type"]: url_signature(result) for result in typed_results}
    unique_typed_signatures = {signature for signature in typed_signatures.values() if signature}
    typed_results_differ = len(unique_typed_signatures) > 1
    omitted_differs = False
    if omitted is not None:
        omitted_signature = url_signature(omitted)
        omitted_differs = any(signature and signature != omitted_signature for signature in typed_signatures.values())

    if not all_typed_success:
        failed = [result["volume_type"] for result in typed_results if not result["success"]]
        notes.append(f"volumeType calls failed for: {', '.join(failed)}")
    if all_typed_success and not all_typed_non_empty:
        empty = [result["volume_type"] for result in typed_results if result["returned_count"] == 0]
        notes.append(f"volumeType calls returned empty results for: {', '.join(empty)}")
    if all_typed_success and all_typed_non_empty and not typed_results_differ:
        notes.append("PGC/BGC/UGC returned identical or indistinguishable URL sets; volumeType may be ineffective.")
    if all_typed_success and all_typed_non_empty and typed_results_differ:
        notes.append("PGC/BGC/UGC returned different URL sets; volumeType appears to distinguish author/content type buckets.")
    if omitted is not None and omitted["success"] and not omitted_differs:
        notes.append("Omitted volumeType result is not clearly different from typed results.")

    can_use = all_typed_success and all_typed_non_empty and typed_results_differ
    return {
        "volume_type_works": all_typed_success and all_typed_non_empty,
        "can_use_as_author_type": can_use,
        "notes": notes,
        "typed_url_signatures": typed_signatures,
        "omitted_differs_from_typed": omitted_differs,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    try:
        query_config = read_query_config()
        server_url = os.getenv("MCP_SERVER_URL")
        if not server_url:
            raise McpError("MCP_SERVER_URL is required")
        parsed = urllib.parse.urlparse(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McpError("MCP_SERVER_URL must be an absolute http(s) URL")

        client = McpHttpClient(server_url, load_headers())
        caller = McpToolCaller(client)
        caller.initialize()

        results = [test_volume_type(caller, query_config, None)]
        for volume_type in VOLUME_TYPES:
            results.append(test_volume_type(caller, query_config, volume_type))

        output = {
            "test_config": {
                "brand": TEST_BRAND,
                "platform": TEST_PLATFORM,
                "dataSource": TEST_DATA_SOURCE,
                "count": TEST_COUNT,
                "sort": SORT_FIELD,
                "start_date": query_config.get("start_date"),
                "end_date": query_config.get("end_date"),
            },
            "results": results,
            "conclusion": build_conclusion(results),
            "warnings": [],
        }
        write_json(OUTPUT_PATH, output)
        print(f"volumeType debug result written to: {OUTPUT_PATH}")
        print(json.dumps(output["conclusion"], ensure_ascii=False))
        return 0
    except Exception as exc:
        output = {
            "test_config": {
                "brand": TEST_BRAND,
                "platform": TEST_PLATFORM,
                "dataSource": TEST_DATA_SOURCE,
                "count": TEST_COUNT,
                "sort": SORT_FIELD,
            },
            "results": [],
            "conclusion": {
                "volume_type_works": False,
                "can_use_as_author_type": False,
                "notes": ["debug script failed before completing volumeType calls"],
            },
            "warnings": [{"stage": "debug_getposts_volume_type", "error_type": exc.__class__.__name__, "message": str(exc)}],
        }
        write_json(OUTPUT_PATH, output)
        print(f"failed to debug getPosts volumeType: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
