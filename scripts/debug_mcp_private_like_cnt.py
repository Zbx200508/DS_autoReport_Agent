#!/usr/bin/env python3
"""Probe whether MCP exposes private_like_cnt for WeChat Channels.

The probe intentionally stores only schema paths, response field paths, field
names, counts, and small numeric samples. It does not store full posts, headers,
tokens, or original post text.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

load_dotenv(ROOT_DIR / ".env")

from brand_mapping import get_mcp_query_brand_for_config
from build_block_1_2_platform_overall_table import normalize_query_config as normalize_block_1_2_config
from build_report_data_package import McpError, McpHttpClient, McpToolCaller, load_headers, redact


DEFAULT_CONFIG = ROOT_DIR / "configs" / "query_config.ui.json"
DEBUG_DIR = ROOT_DIR / "outputs" / "debug"
JSON_OUTPUT = DEBUG_DIR / "mcp_private_like_cnt_probe.json"
HTML_OUTPUT = DEBUG_DIR / "mcp_private_like_cnt_probe.html"
TARGET_PLATFORM = "微信视频号"
TARGET_DISPLAY_BRAND = "海信"
PRIVATE_LIKE_KEYS = (
    "private_like_cnt",
    "privateLikeCnt",
    "private_like_count",
    "privateLikeCount",
    "love_like_cnt",
    "loveLikeCnt",
    "likeCntPrivate",
    "爱心赞",
)
INTERACTION_KEYS = ("interaction", "interactionCnt", "titanInteractionCnt", "totalInteraction", "total_interaction", "互动量")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe MCP private_like_cnt support.")
    parser.add_argument("--query-config-file", default=str(DEFAULT_CONFIG), help="Path to query_config JSON.")
    parser.add_argument("--output-dir", default=str(DEBUG_DIR), help="Directory for debug outputs.")
    parser.add_argument("--post-count", type=int, default=5, help="Small getPosts sample size.")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_query_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("query config must be a JSON object")
    return data


def coerce_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.replace(",", "").replace("%", "").strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def format_number(value: float | None) -> str:
    if value is None:
        return "-"
    if value.is_integer():
        return f"{value:.0f}"
    return f"{value:.6g}"


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "dataList", "list", "rows", "result", "trend", "posts"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def top_level_fields(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return [str(key) for key in payload.keys()]
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return [str(key) for key in payload[0].keys()]
    return []


def flatten_schema_hits(value: Any, path: str = "") -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            key_hit = next((candidate for candidate in PRIVATE_LIKE_KEYS if candidate.lower() in str(key).lower()), None)
            desc = ""
            if isinstance(item, dict):
                desc_value = item.get("description") or item.get("title")
                desc = str(desc_value) if desc_value is not None else ""
            desc_hit = next((candidate for candidate in PRIVATE_LIKE_KEYS if candidate.lower() in desc.lower()), None)
            if key_hit or desc_hit:
                hits.append(
                    {
                        "path": child_path,
                        "field_name": str(key),
                        "description": desc or "-",
                        "matched": key_hit or desc_hit or str(key),
                    }
                )
            hits.extend(flatten_schema_hits(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(flatten_schema_hits(item, f"{path}[{index}]" if path else f"[{index}]"))
    return hits


def flatten_response_hits(value: Any, path: str = "") -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if any(candidate.lower() == str(key).lower() for candidate in PRIVATE_LIKE_KEYS):
                hits.append({"path": child_path, "field_name": str(key), "value": item})
            elif any(candidate.lower() in str(key).lower() for candidate in PRIVATE_LIKE_KEYS):
                hits.append({"path": child_path, "field_name": str(key), "value": item})
            hits.extend(flatten_response_hits(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value[:10]):
            hits.extend(flatten_response_hits(item, f"{path}[{index}]" if path else f"[{index}]"))
    return hits


def first_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def sum_hit_values(hits: list[dict[str, Any]]) -> float | None:
    numbers = [coerce_number(hit.get("value")) for hit in hits]
    numbers = [value for value in numbers if value is not None]
    return sum(numbers) if numbers else None


def build_wechat_video_arg0(raw_config: dict[str, Any], *, count: int | None = None, sort: str | None = None) -> dict[str, Any]:
    notes: list[str] = []
    query_config = normalize_block_1_2_config(raw_config, notes)
    platform = TARGET_PLATFORM if TARGET_PLATFORM in query_config["platform_mappings"] else query_config["platforms"][0]
    mapping = query_config["platform_mappings"][platform]
    brand = query_config["brand"] if query_config["brand"] == TARGET_DISPLAY_BRAND else TARGET_DISPLAY_BRAND
    arg0: dict[str, Any] = {
        "analysisObject": {"brand": get_mcp_query_brand_for_config(brand, raw_config)},
        "startTimeStr": query_config["start_date"],
        "endTimeStr": query_config["end_date"],
        "dataSource": mapping["data_sources"],
        "keywords": query_config["keywords"],
        "filterWords": query_config["filter_words"],
        "statisticBy": "day",
    }
    arg0.update(mapping.get("extra_params") or {})
    if count is not None:
        arg0["count"] = count
    if sort is not None:
        arg0["sort"] = sort
    return arg0


def tool_input_schema(tool: dict[str, Any]) -> Any:
    return tool.get("inputSchema") or tool.get("input_schema") or {}


def probe_schema(caller: McpToolCaller) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    result = caller.client.request("tools/list")
    tools = result.get("tools") if isinstance(result, dict) else []
    if not isinstance(tools, list):
        tools = []
    tools_by_name = {str(tool.get("name")): tool for tool in tools if isinstance(tool, dict) and tool.get("name")}
    rows: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_name = str(tool.get("name") or "-")
        hits = flatten_schema_hits(tool_input_schema(tool))
        if hits:
            for hit in hits:
                rows.append(
                    {
                        "tool_name": tool_name,
                        "found": True,
                        "location": "schema",
                        "schema_path": hit["path"],
                        "field_name": hit["field_name"],
                        "description": hit["description"],
                    }
                )
        else:
            rows.append(
                {
                    "tool_name": tool_name,
                    "found": False,
                    "location": "-",
                    "schema_path": "-",
                    "field_name": "-",
                    "description": "-",
                }
            )
    return rows, tools_by_name


def probe_aggregate_tool(caller: McpToolCaller, tool_name: str, arg0: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = caller.call_tool(tool_name, {"arg0": arg0})
    except Exception as exc:
        return {
            "tool_name": tool_name,
            "success": False,
            "error": str(exc),
            "returned_private_like": False,
            "private_like_value": None,
            "interaction_value": None,
            "top_level_fields": [],
            "matched_paths": [],
            "field_source": "-",
        }
    hits = flatten_response_hits(payload)
    records = extract_records(payload)
    interaction_values = [coerce_number(first_value(record, INTERACTION_KEYS)) for record in records]
    interaction_values = [value for value in interaction_values if value is not None]
    interaction_value = sum(interaction_values) if interaction_values else None
    return {
        "tool_name": tool_name,
        "success": True,
        "error": "-",
        "returned_private_like": bool(hits),
        "private_like_value": sum_hit_values(hits),
        "interaction_value": interaction_value,
        "top_level_fields": top_level_fields(payload),
        "matched_paths": [str(hit["path"]) for hit in hits],
        "matched_fields": [str(hit["field_name"]) for hit in hits],
        "field_source": "aggregate_response" if hits else "-",
    }


def probe_posts(caller: McpToolCaller, arg0: dict[str, Any], tools_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if "getPosts" not in tools_by_name:
        return {
            "tool_name": "getPosts",
            "supported": False,
            "success": False,
            "error": "getPosts not found in tools/list",
            "post_count": 0,
            "raw_keys": [],
            "returned_private_like": False,
            "sample_private_like_values": [],
            "sample_interaction_values": [],
            "matched_paths": [],
        }
    try:
        payload = caller.call_tool("getPosts", {"arg0": arg0})
    except Exception as exc:
        return {
            "tool_name": "getPosts",
            "supported": True,
            "success": False,
            "error": str(exc),
            "post_count": 0,
            "raw_keys": [],
            "returned_private_like": False,
            "sample_private_like_values": [],
            "sample_interaction_values": [],
            "matched_paths": [],
        }
    records = extract_records(payload)
    hits = flatten_response_hits(records)
    sample_private_values = [coerce_number(hit.get("value")) for hit in hits[:5]]
    sample_private_values = [value for value in sample_private_values if value is not None]
    sample_interaction_values: list[float] = []
    for record in records[:5]:
        value = coerce_number(first_value(record, INTERACTION_KEYS))
        if value is not None:
            sample_interaction_values.append(value)
    raw_keys: list[str] = []
    for record in records[:3]:
        for key in record.keys():
            if key not in raw_keys:
                raw_keys.append(str(key))
    return {
        "tool_name": "getPosts",
        "supported": True,
        "success": True,
        "error": "-",
        "post_count": len(records),
        "raw_keys": raw_keys[:80],
        "returned_private_like": bool(hits),
        "sample_private_like_values": sample_private_values,
        "sample_interaction_values": sample_interaction_values,
        "matched_paths": [str(hit["path"]) for hit in hits[:20]],
        "field_source": "posts_response" if hits else "-",
    }


def summarize_capability(aggregate_rows: list[dict[str, Any]], posts_probe: dict[str, Any], schema_rows: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate_supported = any(row.get("returned_private_like") for row in aggregate_rows if row.get("success"))
    posts_supported = bool(posts_probe.get("returned_private_like"))
    schema_supported = any(row.get("found") for row in schema_rows)
    if aggregate_supported:
        status = "aggregate_supported"
        message = "MCP 聚合接口已返回 private_like_cnt 相关字段，可以评估正式 SOE 公式修复。"
    elif posts_supported:
        status = "posts_only"
        message = "MCP 原帖接口返回 private_like_cnt，但聚合接口未返回 private_like_cnt，当前无法直接计算聚合 SOE，除非额外拉取全量帖子后聚合。"
    elif schema_supported:
        status = "schema_only"
        message = "MCP schema 中发现 private_like_cnt 相关字段，但当前聚合/原帖样本响应未返回该字段。"
    else:
        status = "not_found"
        message = "MCP 未发现 private_like_cnt 相关字段。"
    return {
        "status": status,
        "aggregate_supported": aggregate_supported,
        "posts_supported": posts_supported,
        "schema_supported": schema_supported,
        "message": message,
    }


def run_probe(caller: McpToolCaller, raw_config: dict[str, Any], post_count: int = 5) -> dict[str, Any]:
    arg0 = build_wechat_video_arg0(raw_config)
    posts_arg0 = build_wechat_video_arg0(raw_config, count=post_count, sort="titanInteractionCnt")
    schema_rows, tools_by_name = probe_schema(caller)
    aggregate_rows = [
        probe_aggregate_tool(caller, "getVolumeInteractionTrend", arg0),
        probe_aggregate_tool(caller, "getNsrTrend", arg0),
    ]
    posts_probe = probe_posts(caller, posts_arg0, tools_by_name)
    summary = summarize_capability(aggregate_rows, posts_probe, schema_rows)
    return {
        "generated_at": now_iso(),
        "target": {
            "display_brand": TARGET_DISPLAY_BRAND,
            "mcp_query_brand": arg0.get("analysisObject", {}).get("brand"),
            "platform": TARGET_PLATFORM,
            "dataSource": arg0.get("dataSource"),
            "startTimeStr": arg0.get("startTimeStr"),
            "endTimeStr": arg0.get("endTimeStr"),
            "extra_platform_params": {
                key: arg0.get(key)
                for key in ("platform", "platformName", "subDataSource", "sourceName", "mediaType", "channel", "site", "appName")
                if arg0.get(key)
            },
        },
        "private_like_candidates": list(PRIVATE_LIKE_KEYS),
        "summary": summary,
        "schema_probe": schema_rows,
        "aggregate_probe": aggregate_rows,
        "posts_probe": posts_probe,
        "requests": {
            "aggregate_arg0": redact(arg0),
            "posts_arg0": redact(posts_arg0),
        },
    }


def render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body: list[str] = []
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column, "-")
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            cells.append(f"<td>{html.escape(str(value if value not in (None, '') else '-'))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    if not body:
        body.append(f'<tr><td colspan="{len(columns)}">暂无数据</td></tr>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_html(payload: dict[str, Any]) -> str:
    schema_rows = [
        {
            "工具名": row["tool_name"],
            "是否发现": "是" if row["found"] else "否",
            "出现位置": row["location"],
            "schema path": row["schema_path"],
            "字段名称": row["field_name"],
            "字段说明": row["description"],
        }
        for row in payload["schema_probe"]
    ]
    aggregate_rows = [
        {
            "工具名": row["tool_name"],
            "是否调用成功": "是" if row["success"] else "否",
            "是否返回 private_like_cnt": "是" if row["returned_private_like"] else "否",
            "private_like_cnt 值": format_number(coerce_number(row.get("private_like_value"))),
            "普通互动量返回值": format_number(coerce_number(row.get("interaction_value"))),
            "顶层字段": "；".join(row.get("top_level_fields") or []) or "-",
            "命中字段路径": "；".join(row.get("matched_paths") or []) or "-",
            "备注": row.get("error") or "-",
        }
        for row in payload["aggregate_probe"]
    ]
    posts = payload["posts_probe"]
    posts_rows = [
        {
            "工具名": "getPosts",
            "是否支持 getPosts": "是" if posts.get("supported") else "否",
            "是否调用成功": "是" if posts.get("success") else "否",
            "返回帖子数": posts.get("post_count", 0),
            "raw_keys": "；".join(posts.get("raw_keys") or []) or "-",
            "是否存在 private_like_cnt": "是" if posts.get("returned_private_like") else "否",
            "样例 private_like_cnt 值": "；".join(format_number(value) for value in posts.get("sample_private_like_values") or []) or "-",
            "样例互动量字段": "；".join(format_number(value) for value in posts.get("sample_interaction_values") or []) or "-",
            "字段路径": "；".join(posts.get("matched_paths") or []) or "-",
            "备注": posts.get("error") or "-",
        }
    ]
    target = payload["target"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>MCP private_like_cnt 探测</title>
  <style>
    body {{ margin: 0; padding: 28px; color: #1f2937; font-family: Arial, "Microsoft YaHei", sans-serif; }}
    main {{ max-width: 1440px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin: 0 0 14px; }}
    h2 {{ font-size: 18px; margin: 28px 0 12px; }}
    .notice {{ padding: 10px 12px; border: 1px solid #facc15; background: #fefce8; color: #854d0e; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid #d9dee7; }}
    table {{ width: 100%; min-width: 1100px; border-collapse: collapse; }}
    th, td {{ border: 1px solid #d9dee7; padding: 8px 10px; font-size: 12px; vertical-align: top; }}
    th {{ background: #f3f5f8; white-space: nowrap; }}
  </style>
</head>
<body>
<main>
  <h1>MCP private_like_cnt 探测</h1>
  <p class="notice">{html.escape(payload["summary"]["message"])}</p>
  <p>展示品牌：{html.escape(str(target.get("display_brand")))}；MCP查询品牌：{html.escape(str(target.get("mcp_query_brand")))}；平台：{html.escape(str(target.get("platform")))}；dataSource：{html.escape(json.dumps(target.get("dataSource"), ensure_ascii=False))}</p>
  <h2>Schema 探测</h2>
  <div class="table-wrap">{render_table(schema_rows, ["工具名", "是否发现", "出现位置", "schema path", "字段名称", "字段说明"])}</div>
  <h2>聚合接口探测</h2>
  <div class="table-wrap">{render_table(aggregate_rows, ["工具名", "是否调用成功", "是否返回 private_like_cnt", "private_like_cnt 值", "普通互动量返回值", "顶层字段", "命中字段路径", "备注"])}</div>
  <h2>原帖接口探测</h2>
  <div class="table-wrap">{render_table(posts_rows, ["工具名", "是否支持 getPosts", "是否调用成功", "返回帖子数", "raw_keys", "是否存在 private_like_cnt", "样例 private_like_cnt 值", "样例互动量字段", "字段路径", "备注"])}</div>
</main>
</body>
</html>
"""


def write_outputs(payload: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mcp_private_like_cnt_probe.json"
    html_path = output_dir / "mcp_private_like_cnt_probe.html"
    json_path.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_html(payload), encoding="utf-8")
    return json_path, html_path


def main() -> int:
    args = parse_args()
    raw_config = load_query_config(Path(args.query_config_file))
    server_url = os.getenv("MCP_SERVER_URL")
    if not server_url:
        raise McpError("MCP_SERVER_URL is required")
    if not os.getenv("MCP_AUTHORIZATION"):
        raise McpError("MCP_AUTHORIZATION is required")
    parsed = urllib.parse.urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise McpError("MCP_SERVER_URL must be an absolute http(s) URL")

    client = McpHttpClient(server_url, load_headers())
    caller = McpToolCaller(client)
    caller.initialize()
    payload = run_probe(caller, raw_config, post_count=max(1, min(args.post_count, 20)))
    json_path, html_path = write_outputs(payload, Path(args.output_dir))
    print(f"private_like_cnt probe json written to: {json_path}")
    print(f"private_like_cnt probe html written to: {html_path}")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    started = time.perf_counter()
    try:
        raise SystemExit(main())
    finally:
        print(f"elapsed_seconds={time.perf_counter() - started:.3f}", file=sys.stderr)
