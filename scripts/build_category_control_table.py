#!/usr/bin/env python3
"""Build a standalone category control table from MCP aggregate tools.

This script is intentionally not connected to the formal report pipeline. It
can run in dry-run mode, mock mode, or real MCP mode. It never calls an LLM.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
import time
import urllib.parse
from json import JSONDecodeError
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - local environments may not install python-dotenv.
    load_dotenv = None

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if load_dotenv:
    load_dotenv(ROOT_DIR / ".env")
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_report_data_package import McpError, McpHttpClient, McpToolCaller, load_headers, redact


DEFAULT_OUTPUT_DIR = Path("outputs") / "category_control"
DEFAULT_MODULE_TITLE = "各品线重点媒介表现-1"
PAIR_CONTROL_COLUMNS = [
    "品线",
    "媒介平台",
    "用户互动SOE/视频号爱心赞",
    "同比/视频号环比",
    "控比",
    "NSR实际",
    "同比变化",
    "备注",
]
BRAND_POOL_COLUMNS = [
    "品线",
    "媒介平台",
    "SOE",
    "SOE同比",
    "NSR实际",
    "同比变化",
]
PROBE_COLUMNS = [
    "analysisObject",
    "platform_display_name",
    "dataSource",
    "tool_name",
    "success",
    "error_type",
    "data_list_length",
    "has_data",
    "total_volume",
    "total_interaction",
    "avg_nsr",
    "raw_text_preview",
]
RAW_AUDIT_COLUMNS = [
    "品线",
    "平台",
    "dataSource",
    "对象类型",
    "对象展示名",
    "MCP查询品牌名",
    "对象名称",
    "analysisObject",
    "周期类型",
    "查询开始日期",
    "查询结束日期",
    "关键词",
    "过滤词",
    "MCP返回声量",
    "MCP返回互动量",
    "MCP返回NSR",
    "volume_dataList条数",
    "interaction是否为空",
    "nsr_dataList条数",
    "是否可用于当前SOE计算",
    "是否可用于同比计算",
    "备注",
]
REPORT_PERCENT_COLUMNS = set(PAIR_CONTROL_COLUMNS[2:-1] + BRAND_POOL_COLUMNS[2:])
WECHAT_VIDEO_NOTE = "MCP当前不返回 private_like_cnt，且本轮不进行人工补数，视频号爱心赞、环比、控比暂不计算。"
VOLUME_KEYS = ("volume", "totalVolume", "cnt", "count", "total", "value")
INTERACTION_KEYS = ("interaction", "interactionCnt", "interaction_count", "titanInteractionCnt", "totalInteraction")
NSR_KEYS = ("nsr", "NSR", "netSentimentRate", "net_sentiment_rate")


def get_mcp_query_brand(analysis_object: dict[str, Any]) -> str:
    brand = analysis_object.get("brand")
    return brand if isinstance(brand, str) else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build category control table assets.")
    parser.add_argument("--config", required=True, help="Path to category control config JSON.")
    parser.add_argument("--query-config-file", help="Optional table 2 query_config JSON used for keywords, filterWords, and platform mappings.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned analysisObject and dataSource only.")
    parser.add_argument("--mock", action="store_true", help="Use local fake MCP responses to verify formulas.")
    parser.add_argument("--debug-mcp", action="store_true", help="Print MCP request context and raw text preview.")
    parser.add_argument("--limit-calls", type=int, help="Execute only the first N MCP tool calls.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop at the first MCP failure.")
    parser.add_argument("--probe-analysis-object", action="store_true", help="Probe brand/category analysisObject variants for WeChat Video.")
    parser.add_argument("--probe-product-objects",action="store_true",help="Probe product analysisObject across normal platforms without generating the formal category control table.")
    parser.add_argument("--probe-air-conditioner-object", action="store_true", help="Probe Hisense air-conditioner object and dataSource variants.")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{name}[{index}] must be a string")
        if item:
            result.append(item)
    return result


def table_type(config: dict[str, Any]) -> str:
    return str(config.get("table_type") or "pair_control").strip() or "pair_control"


def module_title(config: dict[str, Any]) -> str:
    return str(config.get("title") or DEFAULT_MODULE_TITLE)


def csv_columns(config: dict[str, Any]) -> list[str]:
    return BRAND_POOL_COLUMNS if table_type(config) == "brand_pool" else PAIR_CONTROL_COLUMNS


def report_html_columns(config: dict[str, Any]) -> list[str]:
    columns = csv_columns(config)
    return columns[:-1] if columns and columns[-1] == "备注" else columns


def normalize_analysis_item(item: dict[str, Any], path: str) -> None:
    query_name = item.get("query_name") or item.get("mcp_brand")
    if query_name is not None and (not isinstance(query_name, str) or not query_name.strip()):
        raise ValueError(f"{path}.query_name/mcp_brand must be a non-empty string")
    analysis_object = item.get("analysisObject")
    if analysis_object is None and query_name:
        analysis_object = {"brand": query_name}
        item["analysisObject"] = analysis_object
    if not isinstance(analysis_object, dict):
        raise ValueError(f"{path}.analysisObject must be an object")
    if query_name:
        analysis_object["brand"] = query_name
    allowed_analysis_fields = ("brand", "category", "product", "demandType", "demand")
    if not any(analysis_object.get(field) for field in allowed_analysis_fields):
        raise ValueError(
            f"{path}.analysisObject must include at least one of "
            "brand/category/product/demandType/demand"
        )


def normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    config_table_type = table_type(raw)
    if config_table_type not in {"pair_control", "brand_pool"}:
        raise ValueError("config.table_type must be pair_control or brand_pool")

    required = ("start_date", "end_date", "compare_start_date", "compare_end_date")
    for key in required:
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise ValueError(f"config.{key} is required")

    platforms = raw.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        raise ValueError("config.platforms must be a non-empty list")

    if config_table_type == "brand_pool":
        brands = raw.get("brands")
        if not isinstance(brands, list) or not brands:
            raise ValueError("config.brands must be a non-empty list for brand_pool")
        for index, brand in enumerate(brands):
            if not isinstance(brand, dict):
                raise ValueError("each brand must be an object")
            if not isinstance(brand.get("display_name"), str) or not brand["display_name"].strip():
                raise ValueError(f"brands[{index}].display_name is required")
            normalize_analysis_item(brand, f"brands[{index}]")
    else:
        required_pair = ("brand_display_name", "brand_query_name")
        for key in required_pair:
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise ValueError(f"config.{key} is required")
        lines = raw.get("lines")
        if not isinstance(lines, list) or not lines:
            raise ValueError("config.lines must be a non-empty list")
        for line in lines:
            if not isinstance(line, dict):
                raise ValueError("each line must be an object")
            for side in ("own", "competitor"):
                item = line.get(side)
                if not isinstance(item, dict):
                    raise ValueError(f"line.{side} must be an object")
                normalize_analysis_item(item, f"line.{side}")
            own_brand = line["own"]["analysisObject"].get("brand")
            brand_display_name = raw.get("brand_display_name", "海信")
            brand_query_name = raw.get("brand_query_name", "海信本系")

            if own_brand and own_brand != brand_query_name and not own_brand.startswith(brand_display_name):
                raise ValueError(
                    "海信品类 analysisObject.brand must use 海信本系 or a 海信-prefixed compound brand, "
                    "such as 海信电视 / 海信冰箱 / 海信空调 / 海信洗衣机"
                )

    for platform in platforms:
        if not isinstance(platform, dict):
            raise ValueError("each platform must be an object")
        display_name = platform.get("display_name")
        data_sources = ensure_list(platform.get("dataSource"), f"platforms.{display_name}.dataSource")
        if display_name == "视频号" and data_sources != ["微信视频号"]:
            raise ValueError("视频号 dataSource must be ['微信视频号']; do not use 微信 or 短视频")
        if display_name == "抖音" and data_sources != ["抖音app"]:
            raise ValueError("抖音 dataSource must be ['抖音app']; do not use 短视频")

    return {
        **raw,
        "keywords": ensure_list(raw.get("keywords"), "keywords"),
        "filter_words": ensure_list(raw.get("filter_words"), "filter_words"),
    }


def apply_query_config_overrides(config: dict[str, Any], raw_query_config: dict[str, Any]) -> dict[str, Any]:
    """Align table 4 request fields with the validated table 2 query config."""
    updated = dict(config)
    updated["keywords"] = ensure_list(raw_query_config.get("keywords"), "query_config.keywords")
    updated["filter_words"] = ensure_list(raw_query_config.get("filter_words"), "query_config.filter_words")

    mappings = raw_query_config.get("platform_mappings")
    if not isinstance(mappings, dict):
        raise ValueError("query_config.platform_mappings must be an object")

    table4_to_table2 = {
        "小红书": "小红书",
        "抖音": "抖音",
        "视频号": "微信视频号",
        "B站": "B站",
        "知乎": "知乎",
    }
    platforms: list[dict[str, Any]] = []
    for platform in updated["platforms"]:
        item = dict(platform)
        display_name = str(item.get("display_name") or "")
        table2_name = table4_to_table2.get(display_name)
        if table2_name:
            mapping = mappings.get(table2_name)
            if not isinstance(mapping, dict):
                raise ValueError(f"query_config.platform_mappings.{table2_name} must be an object")
            data_sources = mapping.get("data_sources")
            if data_sources is None:
                data_sources = mapping.get("dataSource")
            item["dataSource"] = ensure_list(data_sources, f"query_config.platform_mappings.{table2_name}.data_sources")
        platforms.append(item)
    updated["platforms"] = platforms
    return updated


def periods(config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {
        "current": (config["start_date"], config["end_date"]),
        "compare": (config["compare_start_date"], config["compare_end_date"]),
    }


def build_arg0(config: dict[str, Any], analysis_object: dict[str, Any], platform: dict[str, Any], start_date: str, end_date: str) -> dict[str, Any]:
    return {
        "analysisObject": dict(analysis_object),
        "startTimeStr": start_date,
        "endTimeStr": end_date,
        "dataSource": list(platform["dataSource"]),
        "keywords": config["keywords"],
        "filterWords": config["filter_words"],
        "statisticBy": "day",
    }


def iter_requests(config: dict[str, Any]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    if table_type(config) == "brand_pool":
        for brand in config["brands"]:
            for platform in config["platforms"]:
                for period_type, (start_date, end_date) in periods(config).items():
                    arg0 = build_arg0(config, brand["analysisObject"], platform, start_date, end_date)
                    mcp_query_brand = get_mcp_query_brand(brand["analysisObject"])
                    requests.append(
                        {
                            "line_name": brand["display_name"],
                            "own_display_name": brand["display_name"],
                            "competitor_display_name": "",
                            "object_type": "brand",
                            "object_display_name": brand["display_name"],
                            "mcp_query_brand": mcp_query_brand,
                            "platform": platform["display_name"],
                            "platform_type": platform.get("platform_type", "normal"),
                            "analysisObject": brand["analysisObject"],
                            "dataSource": platform["dataSource"],
                            "period_type": period_type,
                            "start_date": start_date,
                            "end_date": end_date,
                            "arg0": arg0,
                        }
                    )
        return requests

    for line in config["lines"]:
        for platform in config["platforms"]:
            for object_type in ("own", "competitor"):
                item = line[object_type]
                for period_type, (start_date, end_date) in periods(config).items():
                    arg0 = build_arg0(config, item["analysisObject"], platform, start_date, end_date)
                    mcp_query_brand = get_mcp_query_brand(item["analysisObject"])
                    requests.append(
                        {
                            "line_name": line["line_name"],
                            "own_display_name": line["own"]["display_name"],
                            "competitor_display_name": line["competitor"]["display_name"],
                            "object_type": object_type,
                            "object_display_name": item["display_name"],
                            "mcp_query_brand": mcp_query_brand,
                            "platform": platform["display_name"],
                            "platform_type": platform.get("platform_type", "normal"),
                            "analysisObject": item["analysisObject"],
                            "dataSource": platform["dataSource"],
                            "period_type": period_type,
                            "start_date": start_date,
                            "end_date": end_date,
                            "arg0": arg0,
                        }
                    )
    return requests


def print_dry_run(requests: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str]] = set()

    for request in requests:
        analysis_object = request["analysisObject"]
        analysis_object_text = json.dumps(analysis_object, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        data_source_text = ",".join(request["dataSource"])
        display_name = request["object_display_name"]
        mcp_query_brand = request.get("mcp_query_brand") or get_mcp_query_brand(analysis_object)

        key = (display_name, mcp_query_brand, analysis_object_text, data_source_text)
        if key in seen:
            continue

        seen.add(key)

        print(
            f"展示名={display_name} | "
            f"MCP查询名={mcp_query_brand} | "
            f"analysisObject={analysis_object_text} | "
            f"{request['platform']} | dataSource={data_source_text} | "
            f"keywords={json.dumps(request['arg0'].get('keywords', []), ensure_ascii=False)} | "
            f"filterWords={json.dumps(request['arg0'].get('filterWords', []), ensure_ascii=False)}"
        )


def coerce_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "dataList", "list", "rows", "result", "trend"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def extract_scalar_metric(payload: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        for key in keys:
            value = coerce_number(payload.get(key))
            if value is not None:
                return value
    return None


def sum_metric(payload: Any, keys: tuple[str, ...]) -> float | None:
    scalar = extract_scalar_metric(payload, keys)
    if scalar is not None:
        return scalar
    values: list[float] = []
    for record in extract_records(payload):
        for key in keys:
            value = coerce_number(record.get(key))
            if value is not None:
                values.append(value)
                break
    if not values:
        return None
    return sum(values)


def average_metric(payload: Any, keys: tuple[str, ...]) -> float | None:
    scalar = extract_scalar_metric(payload, keys)
    if scalar is not None:
        return scalar
    values: list[float] = []
    for record in extract_records(payload):
        for key in keys:
            value = coerce_number(record.get(key))
            if value is not None:
                values.append(value)
                break
    if not values:
        return None
    return sum(values) / len(values)


def mock_payload(request: dict[str, Any], tool_name: str) -> dict[str, Any]:
    object_text = json.dumps(request["analysisObject"], ensure_ascii=False)
    line_keywords = ["电视", "冰箱", "空调", "洗衣机"]
    line_index = next((index + 1 for index, keyword in enumerate(line_keywords) if keyword in object_text), 1)
    platform_index = ["小红书", "抖音", "视频号", "B站", "知乎"].index(request["platform"]) + 1
    if request["object_type"] == "brand":
        object_factor = 0.85 + (sum(ord(char) for char in request["object_display_name"]) % 9) / 10
    else:
        object_factor = 1.15 if request["object_type"] == "own" else 1.0
    period_factor = 1.0 if request["period_type"] == "current" else 0.88
    base = (line_index * 1000 + platform_index * 100) * object_factor * period_factor
    if tool_name == "getVolumeInteractionTrend":
        return {"volume": round(base), "interaction": round(base * 12)}
    return {"nsr": round(0.68 + line_index * 0.015 + platform_index * 0.006 + (0.02 if request["period_type"] == "current" else 0), 4)}


def request_context(request: dict[str, Any], tool_name: str) -> dict[str, Any]:
    return {
        "line_name": request["line_name"],
        "object_role": request["object_type"],
        "display_name": request["object_display_name"],
        "mcp_query_brand": request.get("mcp_query_brand") or get_mcp_query_brand(request["analysisObject"]),
        "analysisObject": request["analysisObject"],
        "platform_display_name": request["platform"],
        "dataSource": request["dataSource"],
        "period_type": request["period_type"],
        "tool_name": tool_name,
        "query_start_date": request["start_date"],
        "query_end_date": request["end_date"],
    }


def preview_text(value: str, limit: int = 1000) -> str:
    return value[:limit]


def response_structure_summary(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        content = result.get("content")
        return {
            "has_structured_content": isinstance(result.get("structuredContent"), (dict, list)),
            "has_content": isinstance(content, list),
            "content_length": len(content) if isinstance(content, list) else 0,
            "is_error": result.get("isError") if isinstance(result.get("isError"), bool) else "unknown",
            "response_type": "dict",
            "response_keys": sorted(str(key) for key in result.keys()),
        }
    return {
        "has_structured_content": False,
        "has_content": False,
        "content_length": 0,
        "is_error": "unknown",
        "response_type": type(result).__name__,
        "response_keys": [],
    }


def extract_tool_text(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return None
    first_content = content[0]
    if not isinstance(first_content, dict):
        return None
    text = first_content.get("text")
    return text if isinstance(text, str) else None


def classify_text_parse_error(text: str, exc: Exception | None = None) -> tuple[str, str]:
    if text == "":
        return "empty_text_response", "MCP returned empty content[0].text."
    if "Response missing structured content which is expected when calling tool with non-empty outputSchema" in text:
        return "mcp_output_schema_structured_content_missing", "MCP outputSchema expected structuredContent, but response only contained error text."
    lowered = text.lower()
    if "error" in lowered or "exception" in lowered or "failed" in lowered:
        return "mcp_tool_error_text", "MCP returned plain error text."
    if exc is not None:
        return "json_decode_error", str(exc)
    return "unknown_mcp_parse_error", "Unable to parse MCP response."


def record_mcp_parse_error(
    context: dict[str, Any],
    text: str,
    exception_type: str,
    exception_message: str,
    warnings: list[dict[str, Any]],
    mcp_errors: list[dict[str, Any]],
) -> None:
    error_record = {
        "request_context": context,
        "raw_text_preview": preview_text(text, 1000),
        "raw_text_length": len(text),
        "exception_type": exception_type,
        "exception_message": exception_message,
    }
    mcp_errors.append(error_record)
    warnings.append(
        {
            "level": "error",
            "type": exception_type,
            **context,
            "message": exception_message,
        }
    )


def parse_tool_result(result: Any, context: dict[str, Any], warnings: list[dict[str, Any]], mcp_errors: list[dict[str, Any]], debug_mcp: bool) -> Any:
    summary = response_structure_summary(result)
    if debug_mcp:
        print(f"[MCP response summary] {json.dumps(redact(summary), ensure_ascii=False)}")

    if isinstance(result, dict):
        structured_content = result.get("structuredContent")
        if isinstance(structured_content, (dict, list)):
            if debug_mcp:
                print(f"[MCP structuredContent preview] {json.dumps(redact(structured_content), ensure_ascii=False)[:300]}")
            return structured_content

    text = extract_tool_text(result)
    if text is None:
        if debug_mcp:
            print(f"[MCP raw preview] {json.dumps(redact(result), ensure_ascii=False)[:300]}")
        return result

    if debug_mcp:
        print(f"[MCP raw text preview] {preview_text(str(redact(text)), 300)}")

    try:
        return json.loads(text)
    except JSONDecodeError as exc:
        exception_type, exception_message = classify_text_parse_error(text, exc)
        record_mcp_parse_error(context, text, exception_type, exception_message, warnings, mcp_errors)
        return None


def call_tool_safely(
    caller: McpToolCaller,
    request: dict[str, Any],
    tool_name: str,
    warnings: list[dict[str, Any]],
    mcp_errors: list[dict[str, Any]],
    debug_mcp: bool,
    fail_fast: bool,
) -> Any:
    context = request_context(request, tool_name)
    if debug_mcp:
        print(f"[MCP request] {json.dumps(context, ensure_ascii=False)}")
    try:
        result = caller.client.request("tools/call", {"name": tool_name, "arguments": {"arg0": request["arg0"]}})
        return parse_tool_result(result, context, warnings, mcp_errors, debug_mcp)
    except Exception as exc:
        classified_type, classified_message = classify_text_parse_error(str(exc), None)
        if classified_type == "unknown_mcp_parse_error":
            classified_type = exc.__class__.__name__
            classified_message = str(exc)
        error_record = {
            "request_context": context,
            "raw_text_preview": "",
            "raw_text_length": 0,
            "exception_type": classified_type,
            "exception_message": classified_message,
        }
        mcp_errors.append(error_record)
        warnings.append(
            {
                "level": "error",
                "type": classified_type,
                **context,
                "message": classified_message,
            }
        )
        if debug_mcp:
            print(f"[MCP error] {json.dumps(redact(error_record), ensure_ascii=False)}")
        if fail_fast:
            raise
        return None


def call_mcp(
    requests: list[dict[str, Any]],
    mock: bool,
    warnings: list[dict[str, Any]],
    mcp_errors: list[dict[str, Any]],
    debug_mcp: bool = False,
    limit_calls: int | None = None,
    fail_fast: bool = False,
) -> list[dict[str, Any]]:
    caller: McpToolCaller | None = None
    if not mock:
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

    raw_rows: list[dict[str, Any]] = []
    calls_executed = 0
    for request in requests:
        call_started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        volume_payload = None
        nsr_payload = None
        if mock:
            if limit_calls is None or calls_executed < limit_calls:
                volume_payload = mock_payload(request, "getVolumeInteractionTrend")
                calls_executed += 1
            if limit_calls is None or calls_executed < limit_calls:
                nsr_payload = mock_payload(request, "getNsrTrend")
                calls_executed += 1
        else:
            assert caller is not None
            if limit_calls is None or calls_executed < limit_calls:
                volume_payload = call_tool_safely(caller, request, "getVolumeInteractionTrend", warnings, mcp_errors, debug_mcp, fail_fast)
                calls_executed += 1
            if limit_calls is None or calls_executed < limit_calls:
                nsr_payload = call_tool_safely(caller, request, "getNsrTrend", warnings, mcp_errors, debug_mcp, fail_fast)
                calls_executed += 1
        call_ended = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        raw_rows.append(
            {
                "line_name": request["line_name"],
                "own_display_name": request["own_display_name"],
                "competitor_display_name": request["competitor_display_name"],
                "object_type": request["object_type"],
                "object_display_name": request["object_display_name"],
                "mcp_query_brand": request.get("mcp_query_brand") or get_mcp_query_brand(request["analysisObject"]),
                "platform": request["platform"],
                "platform_type": request["platform_type"],
                "analysisObject": request["analysisObject"],
                "dataSource": request["dataSource"],
                "period_type": request["period_type"],
                "MCP工具名": "getVolumeInteractionTrend + getNsrTrend",
                "MCP调用开始时间": call_started,
                "MCP调用结束时间": call_ended,
                "MCP查询开始时间": request["start_date"],
                "MCP查询结束时间": request["end_date"],
                "keywords": request["arg0"].get("keywords", []),
                "filterWords": request["arg0"].get("filterWords", []),
                "MCP返回声量": sum_metric(volume_payload, VOLUME_KEYS),
                "MCP返回互动量": sum_metric(volume_payload, INTERACTION_KEYS),
                "MCP返回NSR": average_metric(nsr_payload, NSR_KEYS),
                "raw_volume_payload": volume_payload,
                "raw_nsr_payload": nsr_payload,
            }
        )
        if limit_calls is not None and calls_executed >= limit_calls:
            break
    return raw_rows


def init_mcp_caller() -> McpToolCaller:
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
    return caller


def probe_request(config: dict[str, Any], analysis_object: dict[str, Any], tool_name: str) -> dict[str, Any]:
    platform = next((item for item in config["platforms"] if item["display_name"] == "视频号"), None)
    if not platform:
        raise ValueError("probe requires 视频号 platform in config")
    start_date, end_date = config["start_date"], config["end_date"]
    arg0 = build_arg0(config, analysis_object, platform, start_date, end_date)
    return {
        "line_name": "analysisObject probe",
        "own_display_name": "probe",
        "competitor_display_name": "",
        "object_type": "probe",
        "object_display_name": json.dumps(analysis_object, ensure_ascii=False),
        "platform": "视频号",
        "platform_type": "wechat_video",
        "analysisObject": analysis_object,
        "dataSource": ["微信视频号"],
        "period_type": "current",
        "start_date": start_date,
        "end_date": end_date,
        "arg0": arg0,
        "tool_name": tool_name,
    }

def product_probe_request(
    config: dict[str, Any],
    product_display_name: str,
    analysis_object: dict[str, Any],
    platform: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    start_date, end_date = config["start_date"], config["end_date"]
    arg0 = build_arg0(config, analysis_object, platform, start_date, end_date)
    return {
        "line_name": "product probe",
        "own_display_name": product_display_name,
        "competitor_display_name": "",
        "object_type": "product_probe",
        "object_display_name": product_display_name,
        "platform": platform["display_name"],
        "platform_type": platform.get("platform_type", "normal"),
        "analysisObject": analysis_object,
        "dataSource": platform["dataSource"],
        "period_type": "current",
        "start_date": start_date,
        "end_date": end_date,
        "arg0": arg0,
        "tool_name": tool_name,
    }

def summarize_probe_parsed_data(parsed_data: Any, tool_name: str) -> dict[str, Any]:
    data_list: list[dict[str, Any]] = []

    if isinstance(parsed_data, dict):
        maybe_data_list = parsed_data.get("dataList")
        if isinstance(maybe_data_list, list):
            data_list = [item for item in maybe_data_list if isinstance(item, dict)]

    data_list_length = len(data_list)
    has_data = data_list_length > 0

    total_volume = None
    total_interaction = None
    avg_nsr = None

    if tool_name == "getVolumeInteractionTrend":
        total_volume = sum((item.get("volume") or 0) for item in data_list)
        total_interaction = sum((item.get("interaction") or 0) for item in data_list)

    if tool_name == "getNsrTrend":
        nsr_values = [
            item.get("nsr")
            for item in data_list
            if isinstance(item.get("nsr"), (int, float))
        ]
        if nsr_values:
            avg_nsr = sum(nsr_values) / len(nsr_values)

    return {
        "data_list_length": data_list_length,
        "has_data": has_data,
        "total_volume": total_volume,
        "total_interaction": total_interaction,
        "avg_nsr": avg_nsr,
    }

def safe_probe_call(caller: McpToolCaller, request: dict[str, Any], tool_name: str, debug_mcp: bool) -> dict[str, Any]:
    context = request_context(request, tool_name)
    if debug_mcp:
        print(f"[MCP probe request] {json.dumps(context, ensure_ascii=False)}")
    try:
        result = caller.client.request("tools/call", {"name": tool_name, "arguments": {"arg0": request["arg0"]}})
        summary = response_structure_summary(result)
        text = extract_tool_text(result) or ""
        warnings: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        parsed = parse_tool_result(result, context, warnings, errors, debug_mcp)
        error = errors[0] if errors else None
        summary_data = summarize_probe_parsed_data(parsed, tool_name)
        return {
            "analysisObject": request["analysisObject"],
            "tool_name": tool_name,
            "dataSource": request["dataSource"],
            "period_type": request["period_type"],
            "success": parsed is not None and not error,
            "has_structured_content": summary["has_structured_content"],
            "has_content": summary["has_content"],
            "content_length": summary["content_length"],
            "is_error": summary["is_error"],
            "response_type": summary["response_type"],
            "response_keys": summary["response_keys"],
            "raw_text_preview": preview_text(text, 1000),
            "parsed_data_preview": preview_text(json.dumps(redact(parsed), ensure_ascii=False), 1000) if parsed is not None else "",
            "error_type": error.get("exception_type") if error else "",
            "error_message": error.get("exception_message") if error else "",
            **summary_data,
        }
    except Exception as exc:
        classified_type, classified_message = classify_text_parse_error(str(exc), None)
        if classified_type == "unknown_mcp_parse_error":
            classified_type = exc.__class__.__name__
            classified_message = str(exc)
        return {
            "analysisObject": request["analysisObject"],
            "tool_name": tool_name,
            "dataSource": request["dataSource"],
            "period_type": request["period_type"],
            "success": False,
            "has_structured_content": False,
            "has_content": False,
            "content_length": 0,
            "is_error": "unknown",
            "response_type": "",
            "response_keys": [],
            "raw_text_preview": "",
            "parsed_data_preview": "",
            "error_type": classified_type,
            "error_message": classified_message,
            "data_list_length": 0,
            "has_data": False,
            "total_volume": None,
            "total_interaction": None,
            "avg_nsr": None,
        }

def run_analysis_object_probe(config: dict[str, Any], output_dir: Path, debug_mcp: bool) -> list[dict[str, Any]]:
    caller = init_mcp_caller()
    category = "电视"
    probes = [
        {"brand": config["brand_query_name"]},
        {"category": category},
        {"brand": config["brand_query_name"], "category": category},
        {"category": "海信电视"},
        {"brand": config["brand_query_name"], "category": "海信电视"},
        {"product": "海信电视"},
        {"brand": config["brand_query_name"], "product": "电视"},
        {"brand": "海信电视"},
    ]
    records: list[dict[str, Any]] = []
    for analysis_object in probes:
        for tool_name in ("getVolumeInteractionTrend", "getNsrTrend"):
            request = probe_request(config, analysis_object, tool_name)
            records.append(safe_probe_call(caller, request, tool_name, debug_mcp))
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "category_control_analysis_object_probe.json", records)
    return records

def run_product_objects_probe(
    config: dict[str, Any],
    output_dir: Path,
    debug_mcp: bool,
    limit_calls: int | None = None,
    fail_fast: bool = False,
) -> list[dict[str, Any]]:
    caller = init_mcp_caller()

    product_objects = [
        {"product_display_name": "海信电视", "analysisObject": {"product": "海信电视"}},
        {"product_display_name": "TCL电视", "analysisObject": {"product": "TCL电视"}},
        {"product_display_name": "海信冰箱", "analysisObject": {"product": "海信冰箱"}},
        {"product_display_name": "美的冰箱", "analysisObject": {"product": "美的冰箱"}},
        {"product_display_name": "海信空调", "analysisObject": {"product": "海信空调"}},
        {"product_display_name": "美的空调", "analysisObject": {"product": "美的空调"}},
        {"product_display_name": "海信洗衣机", "analysisObject": {"product": "海信洗衣机"}},
        {"product_display_name": "小天鹅洗衣机", "analysisObject": {"product": "小天鹅洗衣机"}},
    ]

    normal_platform_names = {"小红书", "抖音", "B站", "知乎"}
    platforms = [
        item
        for item in config.get("platforms", [])
        if item.get("display_name") in normal_platform_names
    ]

    records: list[dict[str, Any]] = []
    call_count = 0

    for product_item in product_objects:
        for platform in platforms:
            for tool_name in ("getVolumeInteractionTrend", "getNsrTrend"):
                if limit_calls is not None and call_count >= limit_calls:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    write_json(output_dir / "category_control_product_probe.json", records)
                    return records

                request = product_probe_request(
                    config=config,
                    product_display_name=product_item["product_display_name"],
                    analysis_object=product_item["analysisObject"],
                    platform=platform,
                    tool_name=tool_name,
                )

                record = safe_probe_call(caller, request, tool_name, debug_mcp)
                record["product_display_name"] = product_item["product_display_name"]
                record["platform_display_name"] = platform.get("display_name")
                records.append(record)

                call_count += 1

                if fail_fast and not record.get("success"):
                    output_dir.mkdir(parents=True, exist_ok=True)
                    write_json(output_dir / "category_control_product_probe.json", records)
                    return records

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "category_control_product_probe.json", records)
    return records


def write_probe_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PROBE_COLUMNS)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["analysisObject"] = json.dumps(record.get("analysisObject"), ensure_ascii=False)
            row["dataSource"] = json.dumps(record.get("dataSource"), ensure_ascii=False)
            writer.writerow({column: format_cell(row.get(column)) for column in PROBE_COLUMNS})


def render_air_conditioner_probe_html(records: list[dict[str, Any]]) -> str:
    object_names = sorted({json.dumps(record.get("analysisObject"), ensure_ascii=False) for record in records})
    platform_names = sorted({str(record.get("platform_display_name") or "") for record in records})
    has_data_records = [record for record in records if record.get("has_data")]
    error_records = [record for record in records if not record.get("success")]

    if has_data_records:
        conclusion = "部分候选对象或 dataSource 已返回数据，可据此判断正式配置应优先调整对象名或 dataSource。"
    elif records and all(record.get("success") for record in records):
        conclusion = "所有候选请求结构可用但均未返回数据，需要向 MCP 侧确认系统页面实际传入的 analysisObject 和 dataSource。"
    else:
        conclusion = "部分候选请求失败，需要结合错误类型判断 MCP / 数据接口侧对 analysisObject 的支持范围。"

    header_html = "".join(f"<th>{html.escape(column)}</th>" for column in PROBE_COLUMNS)
    rows_html = "\n".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(format_cell(value))}</td>"
            for value in (
                json.dumps(record.get("analysisObject"), ensure_ascii=False),
                record.get("platform_display_name"),
                json.dumps(record.get("dataSource"), ensure_ascii=False),
                record.get("tool_name"),
                record.get("success"),
                record.get("error_type"),
                record.get("data_list_length"),
                record.get("has_data"),
                record.get("total_volume"),
                record.get("total_interaction"),
                record.get("avg_nsr"),
                record.get("raw_text_preview"),
            )
        )
        + "</tr>"
        for record in records
    )
    error_rows = "\n".join(
        f"<li>{html.escape(json.dumps(record, ensure_ascii=False))}</li>"
        for record in error_records
    ) or "<li>无</li>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>海信空调对象探测</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2937; }}
    section {{ margin: 24px 0; }}
    pre {{ background: #f6f8fa; padding: 12px; overflow: auto; border: 1px solid #d0d7de; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px; vertical-align: top; }}
    th {{ background: #f3f4f6; }}
    .notice {{ background: #eff6ff; border: 1px solid #bfdbfe; padding: 12px; }}
  </style>
</head>
<body>
  <h1>海信空调对象探测</h1>
  <section>
    <h2>探测对象</h2>
    <pre>{html.escape(json.dumps(object_names, ensure_ascii=False, indent=2))}</pre>
  </section>
  <section>
    <h2>探测平台</h2>
    <pre>{html.escape(json.dumps(platform_names, ensure_ascii=False, indent=2))}</pre>
  </section>
  <section>
    <h2>汇总表</h2>
    <table>
      <thead><tr>{header_html}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </section>
  <section>
    <h2>错误明细</h2>
    <ul>{error_rows}</ul>
  </section>
  <section class="notice">
    <h2>结论提示</h2>
    <p>{html.escape(conclusion)}</p>
  </section>
</body>
</html>
"""


def write_air_conditioner_probe_outputs(output_dir: Path, records: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "category_control_air_conditioner_probe.json", records)
    write_probe_csv(output_dir / "category_control_air_conditioner_probe.csv", records)
    (output_dir / "category_control_air_conditioner_probe.html").write_text(
        render_air_conditioner_probe_html(records),
        encoding="utf-8",
    )


def run_air_conditioner_probe(
    config: dict[str, Any],
    output_dir: Path,
    debug_mcp: bool,
    limit_calls: int | None = None,
    fail_fast: bool = False,
) -> list[dict[str, Any]]:
    caller = init_mcp_caller()
    candidates = [
        {"display_name": "海信空调", "analysisObject": {"brand": "海信空调"}},
        {"display_name": "美的空调", "analysisObject": {"brand": "美的空调"}},
        {"display_name": "海信空调品线", "analysisObject": {"brand": "海信空调品线"}},
        {"display_name": "美的空调品线", "analysisObject": {"brand": "美的空调品线"}},
        {"display_name": "海信空调产品", "analysisObject": {"brand": "海信空调产品"}},
        {"display_name": "美的空调产品", "analysisObject": {"brand": "美的空调产品"}},
        {"display_name": "海信空调", "analysisObject": {"product": "海信空调"}},
        {"display_name": "美的空调", "analysisObject": {"product": "美的空调"}},
        {"display_name": "海信+空调", "analysisObject": {"brand": "海信", "product": "空调"}},
        {"display_name": "美的+空调", "analysisObject": {"brand": "美的", "product": "空调"}},
        {"display_name": "海信本系+空调", "analysisObject": {"brand": "海信本系", "product": "空调"}},
    ]
    platforms = [
        {"display_name": "小红书", "dataSource": ["小红书"], "platform_type": "normal"},
        {"display_name": "视频", "dataSource": ["视频"], "platform_type": "normal"},
        {"display_name": "问答", "dataSource": ["问答"], "platform_type": "normal"},
        {"display_name": "短视频", "dataSource": ["短视频"], "platform_type": "normal"},
        {"display_name": "抖音app", "dataSource": ["抖音app"], "platform_type": "normal"},
        {"display_name": "微信视频号", "dataSource": ["微信视频号"], "platform_type": "wechat_video"},
    ]

    records: list[dict[str, Any]] = []
    call_count = 0
    for candidate in candidates:
        for platform in platforms:
            for tool_name in ("getVolumeInteractionTrend", "getNsrTrend"):
                if limit_calls is not None and call_count >= limit_calls:
                    write_air_conditioner_probe_outputs(output_dir, records)
                    return records
                request = product_probe_request(
                    config=config,
                    product_display_name=candidate["display_name"],
                    analysis_object=candidate["analysisObject"],
                    platform=platform,
                    tool_name=tool_name,
                )
                record = safe_probe_call(caller, request, tool_name, debug_mcp)
                record["platform_display_name"] = platform["display_name"]
                records.append(record)
                call_count += 1
                if fail_fast and not record.get("success"):
                    write_air_conditioner_probe_outputs(output_dir, records)
                    return records

    write_air_conditioner_probe_outputs(output_dir, records)
    return records


def raw_lookup(raw_rows: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    return {
        (row["line_name"], row["platform"], row["object_type"], row["period_type"]): row
        for row in raw_rows
    }


def brand_pool_lookup(raw_rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        (row["line_name"], row["platform"], row["period_type"]): row
        for row in raw_rows
    }


def safe_div(numerator: float | None, denominator: float | None, warnings: list[dict[str, Any]], context: dict[str, Any], metric: str) -> float | None:
    if numerator is None or denominator is None:
        warnings.append({**context, "metric": metric, "message": "分子或分母缺失，返回空。"})
        return None
    if denominator == 0:
        warnings.append({**context, "metric": metric, "message": "分母为 0，返回空。"})
        return None
    return numerator / denominator


def metric_diff(current: float | None, compare: float | None) -> float | None:
    if current is None or compare is None:
        return None
    return current - compare


def calculate_brand_pool_table(config: dict[str, Any], raw_rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = brand_pool_lookup(raw_rows)
    rows: list[dict[str, Any]] = []
    brands = config["brands"]
    for platform in config["platforms"]:
        platform_name = platform["display_name"]
        current_values = [
            coerce_number(lookup.get((brand["display_name"], platform_name, "current"), {}).get("MCP返回互动量"))
            for brand in brands
        ]
        compare_values = [
            coerce_number(lookup.get((brand["display_name"], platform_name, "compare"), {}).get("MCP返回互动量"))
            for brand in brands
        ]
        current_total = sum(value for value in current_values if value is not None) if all(value is not None for value in current_values) else None
        compare_total = sum(value for value in compare_values if value is not None) if all(value is not None for value in compare_values) else None

        for brand in brands:
            brand_name = brand["display_name"]
            context = {"line_name": brand_name, "platform": platform_name}
            current_row = lookup.get((brand_name, platform_name, "current"), {})
            compare_row = lookup.get((brand_name, platform_name, "compare"), {})
            current_nsr = coerce_number(current_row.get("MCP返回NSR"))
            compare_nsr = coerce_number(compare_row.get("MCP返回NSR"))

            if platform.get("platform_type") == "wechat_video":
                current_soe = None
                compare_soe = None
            else:
                current_soe = safe_div(
                    coerce_number(current_row.get("MCP返回互动量")),
                    current_total,
                    warnings,
                    context,
                    "用户心智SOE",
                )
                compare_soe = safe_div(
                    coerce_number(compare_row.get("MCP返回互动量")),
                    compare_total,
                    warnings,
                    context,
                    "去年同期用户心智SOE",
                )

            rows.append(
                {
                    "品线": brand_name,
                    "媒介平台": platform_name,
                    "SOE": current_soe,
                    "SOE同比": metric_diff(current_soe, compare_soe),
                    "NSR实际": current_nsr,
                    "同比变化": metric_diff(current_nsr, compare_nsr),
                }
            )
    ordered_rows: list[dict[str, Any]] = []
    for brand in brands:
        brand_name = brand["display_name"]
        ordered_rows.extend(row for row in rows if row["品线"] == brand_name)
    return ordered_rows


def calculate_pair_control_table(config: dict[str, Any], raw_rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = raw_lookup(raw_rows)
    rows: list[dict[str, Any]] = []
    for line in config["lines"]:
        for platform in config["platforms"]:
            line_name = line["line_name"]
            platform_name = platform["display_name"]
            context = {"line_name": line_name, "platform": platform_name}
            own_current = lookup.get((line_name, platform_name, "own", "current"), {})
            own_compare = lookup.get((line_name, platform_name, "own", "compare"), {})
            competitor_current = lookup.get((line_name, platform_name, "competitor", "current"), {})
            competitor_compare = lookup.get((line_name, platform_name, "competitor", "compare"), {})

            current_nsr = coerce_number(own_current.get("MCP返回NSR"))
            compare_nsr = coerce_number(own_compare.get("MCP返回NSR"))

            if platform.get("platform_type") == "wechat_video":
                rows.append(
                    {
                        "品线": line_name,
                        "媒介平台": platform_name,
                        "用户互动SOE/视频号爱心赞": "",
                        "同比/视频号环比": "",
                        "控比": "",
                        "NSR实际": current_nsr,
                        "同比变化": metric_diff(current_nsr, compare_nsr),
                        "备注": WECHAT_VIDEO_NOTE,
                    }
                )
                continue

            own_interaction_current = coerce_number(own_current.get("MCP返回互动量"))
            competitor_interaction_current = coerce_number(competitor_current.get("MCP返回互动量"))
            own_interaction_compare = coerce_number(own_compare.get("MCP返回互动量"))
            competitor_interaction_compare = coerce_number(competitor_compare.get("MCP返回互动量"))
            current_soe = safe_div(
                own_interaction_current,
                (own_interaction_current or 0) + (competitor_interaction_current or 0)
                if own_interaction_current is not None and competitor_interaction_current is not None
                else None,
                warnings,
                context,
                "用户互动SOE",
            )
            compare_soe = safe_div(
                own_interaction_compare,
                (own_interaction_compare or 0) + (competitor_interaction_compare or 0)
                if own_interaction_compare is not None and competitor_interaction_compare is not None
                else None,
                warnings,
                context,
                "去年同期用户互动SOE",
            )
            control_ratio = safe_div(own_interaction_current, competitor_interaction_current, warnings, context, "控比")

            rows.append(
                {
                    "品线": line_name,
                    "媒介平台": platform_name,
                    "用户互动SOE/视频号爱心赞": current_soe,
                    "同比/视频号环比": metric_diff(current_soe, compare_soe),
                    "控比": control_ratio,
                    "NSR实际": current_nsr,
                    "同比变化": metric_diff(current_nsr, compare_nsr),
                    "备注": "控比当前默认公式为本品互动量 / 竞品互动量。如业务后续确认控比不是该公式，只调整计算函数，不影响 MCP 取数逻辑。",
                }
            )
    return rows


def calculate_table(config: dict[str, Any], raw_rows: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if table_type(config) == "brand_pool":
        return calculate_brand_pool_table(config, raw_rows, warnings)
    return calculate_pair_control_table(config, raw_rows, warnings)


def format_cell(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def format_report_percent(value: Any) -> str:
    number = coerce_number(value)
    if number is None:
        return "-"
    percent = number * 100
    if abs(percent) < 0.005:
        percent = 0.0
    return f"{percent:.2f}%"


def format_report_cell(column: str, value: Any) -> str:
    if column in REPORT_PERCENT_COLUMNS:
        return format_report_percent(value)
    if value is None or value == "":
        return "-"
    return str(value)


def format_line_name_for_html(line_name: str) -> str:
    if "控比竞品" in line_name:
        own, competitor = line_name.split("控比竞品", 1)
        return f"{html.escape(own)}<br>控比竞品<br>{html.escape(competitor)}"
    return html.escape(line_name)


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: format_cell(row.get(column)) for column in columns})


def data_list_length(payload: Any) -> int:
    return len(extract_records(payload))


def list_display(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item) for item in value if str(item))
    if value is None:
        return ""
    return str(value)


def is_empty_metric(value: Any) -> bool:
    return value is None or value == ""


def build_raw_audit_rows(raw_rows: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    audit_rows: list[dict[str, Any]] = []
    is_brand_pool = table_type(config) == "brand_pool"
    for row in raw_rows:
        interaction = row.get("MCP返回互动量")
        nsr = row.get("MCP返回NSR")
        period_type = row.get("period_type")
        platform = row.get("platform")
        interaction_empty = is_empty_metric(interaction)
        nsr_empty = is_empty_metric(nsr)

        notes: list[str] = []
        if interaction_empty and period_type == "current":
            notes.append("MCP未返回互动量，相关SOE可能为空。" if is_brand_pool else "MCP未返回互动量，相关SOE/控比可能为空。")
        if interaction_empty and period_type == "compare":
            notes.append("MCP未返回去年同期互动量，相关同比可能为空。")
        if nsr_empty:
            notes.append("MCP未返回NSR，NSR实际或NSR同比可能为空。")
        if platform == "视频号":
            notes.append(
                "视频号SOE依赖private_like_cnt，当前不使用普通互动量计算SOE。"
                if is_brand_pool
                else "视频号爱心赞字段依赖private_like_cnt，当前不使用普通互动量计算SOE/控比。"
            )

        audit_rows.append(
            {
                "品线": row.get("line_name"),
                "平台": platform,
                "dataSource": list_display(row.get("dataSource")),
                "对象类型": row.get("object_type"),
                "对象展示名": row.get("object_display_name"),
                "MCP查询品牌名": row.get("mcp_query_brand") or get_mcp_query_brand(row.get("analysisObject") or {}),
                "对象名称": row.get("object_display_name"),
                "analysisObject": json.dumps(row.get("analysisObject") or {}, ensure_ascii=False, sort_keys=True),
                "周期类型": period_type,
                "查询开始日期": row.get("MCP查询开始时间"),
                "查询结束日期": row.get("MCP查询结束时间"),
                "关键词": list_display(row.get("keywords")),
                "过滤词": list_display(row.get("filterWords")),
                "MCP返回声量": row.get("MCP返回声量"),
                "MCP返回互动量": interaction,
                "MCP返回NSR": nsr,
                "volume_dataList条数": data_list_length(row.get("raw_volume_payload")),
                "interaction是否为空": interaction_empty,
                "nsr_dataList条数": data_list_length(row.get("raw_nsr_payload")),
                "是否可用于当前SOE计算": (not interaction_empty) if period_type == "current" else "",
                "是否可用于同比计算": (not interaction_empty) if period_type == "compare" else "",
                "备注": "；".join(notes),
            }
        )
    return audit_rows


def format_audit_cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    return format_cell(value)


def write_raw_audit_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=RAW_AUDIT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: format_audit_cell(row.get(column)) for column in RAW_AUDIT_COLUMNS})


def render_category_control_table_rows(rows: list[dict[str, Any]], columns: list[str]) -> str:
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for row in rows:
        line_name = str(row.get("品线") or "")
        if groups and groups[-1][0] == line_name:
            groups[-1][1].append(row)
        else:
            groups.append((line_name, [row]))

    rendered_rows: list[str] = []
    value_columns = columns[1:]
    for line_name, group_rows in groups:
        rowspan = len(group_rows)
        for index, row in enumerate(group_rows):
            cells: list[str] = []
            if index == 0:
                cells.append(
                    f'<td rowspan="{rowspan}" class="category-line-cell">'
                    f'<div class="category-line-cell-inner">'
                    f"{format_line_name_for_html(format_report_cell('品线', line_name))}</div></td>"
                )
            for column in value_columns:
                cell_class = ' class="platform-cell"' if column == value_columns[0] else ""
                cells.append(f"<td{cell_class}>{html.escape(format_report_cell(column, row.get(column)))}</td>")
            rendered_rows.append("<tr>" + "".join(cells) + "</tr>")
    return "\n".join(rendered_rows)


def render_table_header(config: dict[str, Any], columns: list[str]) -> str:
    if table_type(config) != "brand_pool":
        return "<tr>" + "".join(f"<th>{html.escape(column)}</th>" for column in columns) + "</tr>"
    return """
      <tr>
        <th rowspan="2">品线</th>
        <th rowspan="2">媒介平台</th>
        <th colspan="2">用户心智</th>
        <th colspan="2">口碑</th>
      </tr>
      <tr>
        <th>SOE</th>
        <th>SOE同比</th>
        <th>NSR实际</th>
        <th>同比变化</th>
      </tr>
    """


def render_raw_audit_html(config: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    mapping_summary = [
        {
            "平台": platform.get("display_name"),
            "dataSource": platform.get("dataSource"),
            "platform_type": platform.get("platform_type"),
        }
        for platform in config.get("platforms", [])
    ]
    header_html = "".join(f"<th>{html.escape(column)}</th>" for column in RAW_AUDIT_COLUMNS)
    rows_html = "\n".join(
        "<tr>"
        + "".join(f"<td>{html.escape(format_audit_cell(row.get(column)))}</td>" for column in RAW_AUDIT_COLUMNS)
        + "</tr>"
        for row in rows
    )
    empty_rows = [row for row in rows if row.get("interaction是否为空") or is_empty_metric(row.get("MCP返回NSR"))]
    empty_items = "\n".join(
        f"<li>{html.escape(row.get('品线') or '')} / {html.escape(row.get('平台') or '')} / "
        f"{html.escape(row.get('对象类型') or '')} / {html.escape(row.get('周期类型') or '')}："
        f"{html.escape(row.get('备注') or '存在空值')}</li>"
        for row in empty_rows[:80]
    ) or "<li>无</li>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(module_title(config))}原数据核对表</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2937; }}
    h1, h2 {{ margin: 0 0 12px; }}
    section {{ margin: 24px 0; }}
    pre {{ background: #f6f8fa; padding: 12px; overflow: auto; border: 1px solid #d0d7de; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px; vertical-align: top; }}
    th {{ background: #f3f4f6; position: sticky; top: 0; }}
    .notice {{ background: #fff7ed; border: 1px solid #fed7aa; padding: 12px; }}
  </style>
</head>
<body>
  <h1>{html.escape(module_title(config))}原数据核对表</h1>
  <section class="notice">
    <h2>当前取数口径说明</h2>
    <p>本表展示每个品线、平台、对象、current/compare 周期下 MCP 返回的原始聚合值，不做正式表格计算。</p>
    <p>本表复用表格2的关键词、过滤词和站点映射；对象展示名用于表格呈现，analysisObject.brand 使用 MCP 库内查询品牌名。</p>
    <pre>{html.escape(json.dumps(mapping_summary, ensure_ascii=False, indent=2))}</pre>
  </section>
  <section>
    <h2>原数据明细表</h2>
    <table>
      <thead><tr>{header_html}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </section>
  <section>
    <h2>空值提示说明</h2>
    <ul>{empty_items}</ul>
  </section>
</body>
</html>
"""


def render_html(config: dict[str, Any], rows: list[dict[str, Any]], warnings: list[dict[str, Any]], mcp_errors: list[dict[str, Any]]) -> str:
    columns = report_html_columns(config)
    table_headers = render_table_header(config, columns)
    table_rows = render_category_control_table_rows(rows, columns)
    title = module_title(config)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2937; }}
    h1 {{ margin: 0 0 16px; font-size: 22px; line-height: 1.35; }}
    .category-control-table {{ border-collapse: collapse; width: 100%; font-size: 14px; table-layout: fixed; }}
    th, td {{ border: 1px solid #d0d7de; padding: 9px 10px; vertical-align: middle; text-align: center; }}
    th {{ background: #f3f4f6; font-weight: 700; }}
    .category-control-table td.category-line-cell {{
      text-align: center;
      vertical-align: middle;
      white-space: normal;
      line-height: 1.6;
      font-weight: 600;
      min-width: 96px;
      max-width: 120px;
      word-break: keep-all;
    }}
    .category-line-cell-inner {{
      display: block;
      text-align: center;
      width: 100%;
    }}
    .category-control-table td.platform-cell {{
      text-align: center;
      vertical-align: middle;
      white-space: nowrap;
    }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <table class="category-control-table">
    <thead>{table_headers}</thead>
    <tbody>{table_rows}</tbody>
  </table>
</body>
</html>
"""


def write_outputs(
    output_dir: Path,
    config: dict[str, Any],
    rows: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    mcp_errors: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "table_name": module_title(config),
        "block_id": config.get("block_id"),
        "table_type": table_type(config),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "rows": rows,
        "notes": [
            "同比变化 = 当期数值 - 去年同期数值，不是增长率。",
            "视频号爱心赞相关字段不使用普通互动量计算。",
        ],
    }
    write_json(output_dir / "category_control_table.json", payload)
    write_csv(output_dir / "category_control_table.csv", rows, csv_columns(config))
    (output_dir / "category_control_table.html").write_text(render_html(config, rows, warnings, mcp_errors), encoding="utf-8")
    write_json(output_dir / "category_control_raw_mcp.json", raw_rows)
    raw_audit_rows = build_raw_audit_rows(raw_rows, config)
    write_json(output_dir / "category_control_raw_audit.json", raw_audit_rows)
    write_raw_audit_csv(output_dir / "category_control_raw_audit.csv", raw_audit_rows)
    (output_dir / "category_control_raw_audit.html").write_text(render_raw_audit_html(config, raw_audit_rows), encoding="utf-8")
    write_json(output_dir / "category_control_warnings.json", warnings)
    write_json(output_dir / "category_control_mcp_errors.json", mcp_errors)


def print_mock_checks(config: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    normal_rows = [row for row in rows if row["媒介平台"] != "视频号"]
    video_rows = [row for row in rows if row["媒介平台"] == "视频号"]
    if table_type(config) == "brand_pool":
        checks = {
            "普通平台 SOE 可计算": all(row["SOE"] is not None for row in normal_rows),
            "普通平台 SOE同比可计算": all(row["SOE同比"] is not None for row in normal_rows),
            "普通平台 NSR 实际可计算": all(row["NSR实际"] is not None for row in normal_rows),
            "普通平台 NSR 同比变化可计算": all(row["同比变化"] is not None for row in normal_rows),
            "视频号 SOE 为空": all(row["SOE"] is None for row in video_rows),
            "视频号 SOE同比为空": all(row["SOE同比"] is None for row in video_rows),
            "视频号 NSR 实际可计算": all(row["NSR实际"] is not None for row in video_rows),
            "视频号 NSR 同比变化可计算": all(row["同比变化"] is not None for row in video_rows),
        }
    else:
        checks = {
            "普通平台 SOE 可计算": all(row["用户互动SOE/视频号爱心赞"] is not None for row in normal_rows),
            "普通平台同比可计算": all(row["同比/视频号环比"] is not None for row in normal_rows),
            "普通平台控比可计算": all(row["控比"] is not None for row in normal_rows),
            "普通平台 NSR 实际可计算": all(row["NSR实际"] is not None for row in normal_rows),
            "普通平台 NSR 同比变化可计算": all(row["同比变化"] is not None for row in normal_rows),
            "视频号用户互动 SOE 为空": all(row["用户互动SOE/视频号爱心赞"] == "" for row in video_rows),
            "视频号环比为空": all(row["同比/视频号环比"] == "" for row in video_rows),
            "视频号控比为空": all(row["控比"] == "" for row in video_rows),
            "视频号 NSR 实际可计算": all(row["NSR实际"] is not None for row in video_rows),
            "视频号 NSR 同比变化可计算": all(row["同比变化"] is not None for row in video_rows),
        }
    for label, ok in checks.items():
        print(f"{label}: {'OK' if ok else 'FAIL'}")


def main() -> int:
    args = parse_args()
    warnings: list[dict[str, Any]] = []
    mcp_errors: list[dict[str, Any]] = []
    config = normalize_config(read_json(Path(args.config)))
    if args.query_config_file:
        config = apply_query_config_overrides(config, read_json(Path(args.query_config_file)))
    requests = iter_requests(config)
    output_dir = Path(args.output_dir)
    if args.probe_analysis_object:
        records = run_analysis_object_probe(config, output_dir, args.debug_mcp)
        print(f"analysisObject probe written to: {output_dir / 'category_control_analysis_object_probe.json'}")
        print(json.dumps({"probe_records": len(records), "success": sum(1 for item in records if item.get("success"))}, ensure_ascii=False))
        return 0
    if args.probe_product_objects:
        records = run_product_objects_probe(
            config=config,
            output_dir=output_dir,
            debug_mcp=args.debug_mcp,
            limit_calls=args.limit_calls,
            fail_fast=args.fail_fast,
        )
        print(f"product probe written to: {output_dir / 'category_control_product_probe.json'}")
        print(json.dumps({
            "probe_records": len(records),
            "success": sum(1 for item in records if item.get("success")),
            "has_data": sum(1 for item in records if item.get("has_data")),
        }, ensure_ascii=False))
        return 0
    if args.probe_air_conditioner_object:
        records = run_air_conditioner_probe(
            config=config,
            output_dir=output_dir,
            debug_mcp=args.debug_mcp,
            limit_calls=args.limit_calls,
            fail_fast=args.fail_fast,
        )
        print(f"air conditioner probe json written to: {output_dir / 'category_control_air_conditioner_probe.json'}")
        print(f"air conditioner probe csv written to: {output_dir / 'category_control_air_conditioner_probe.csv'}")
        print(f"air conditioner probe html written to: {output_dir / 'category_control_air_conditioner_probe.html'}")
        print(json.dumps({
            "probe_records": len(records),
            "success": sum(1 for item in records if item.get("success")),
            "has_data": sum(1 for item in records if item.get("has_data")),
        }, ensure_ascii=False))
        return 0
    if args.dry_run:
        print_dry_run(requests)
        return 0

    raw_rows = call_mcp(
        requests,
        mock=args.mock,
        warnings=warnings,
        mcp_errors=mcp_errors,
        debug_mcp=args.debug_mcp,
        limit_calls=args.limit_calls,
        fail_fast=args.fail_fast,
    )
    rows = calculate_table(config, raw_rows, warnings)
    write_outputs(output_dir, config, rows, raw_rows, warnings, mcp_errors)
    if args.mock:
        print_mock_checks(config, rows)
    print(f"category control table json written to: {output_dir / 'category_control_table.json'}")
    print(f"category control table csv written to: {output_dir / 'category_control_table.csv'}")
    print(f"category control table html written to: {output_dir / 'category_control_table.html'}")
    print(json.dumps({"rows": len(rows), "raw_rows": len(raw_rows), "warnings": len(warnings), "mcp_errors": len(mcp_errors)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
