#!/usr/bin/env python3
"""Audit the MCP fetches used by table 1.1 and 1.2.

This script calls only the MCP tools used by the first two report tables. It
does not call any LLM, read prompts, generate the final report, or register a
history report.
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
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
SCRIPT_DIR = ROOT_DIR / "scripts"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_block_1_1_brand_overall_table import build_arg0 as build_block_1_1_arg0
from build_block_1_1_brand_overall_table import normalize_query_config as normalize_block_1_1_config
from build_block_1_2_platform_overall_table import build_arg0 as build_block_1_2_arg0
from build_block_1_2_platform_overall_table import build_brand_pool, normalize_query_config as normalize_block_1_2_config
from build_report_data_package import McpError, McpHttpClient, McpToolCaller, load_headers, redact
from brand_mapping import get_mcp_query_brand_for_config
from debug_mcp_private_like_cnt import run_probe as run_private_like_probe


DEFAULT_CONFIG = ROOT_DIR / "configs" / "query_config.ui.json"
AUDIT_DIR = ROOT_DIR / "outputs" / "audit"
HTML_OUTPUT = AUDIT_DIR / "table_mcp_fetch_audit.html"
CSV_OUTPUT = AUDIT_DIR / "table_mcp_fetch_audit.csv"
JSON_OUTPUT = AUDIT_DIR / "table_mcp_fetch_audit.json"
EMPTY = "-"

MAIN_COLUMNS = [
    "审计子模块",
    "模块",
    "数据周期类型",
    "报告周期",
    "同比周期",
    "品牌",
    "展示品牌",
    "MCP查询品牌",
    "平台/站点",
    "MCP工具名",
    "dataSource",
    "正式dataSource",
    "额外平台参数",
    "keywords_raw",
    "keywords_sent_to_mcp",
    "keywords_count",
    "是否传入关键词",
    "filter_words_raw",
    "filter_words_sent_to_mcp",
    "filter_words_count",
    "是否传入过滤词",
    "MCP调用开始时间",
    "MCP调用结束时间",
    "MCP查询开始时间",
    "MCP查询结束时间",
    "MCP返回声量",
    "MCP返回互动量",
    "MCP返回正面声量",
    "MCP返回中性声量",
    "MCP返回负面声量",
    "MCP返回NSR",
    "MCP原始返回字段摘要",
    "备注",
]

SPLIT_COLUMNS = [
    "审计子模块",
    "核对类型",
    "模块",
    "数据周期类型",
    "品牌",
    "展示品牌",
    "MCP查询品牌",
    "dataSource请求方式",
    "dataSource请求值",
    "MCP调用开始时间",
    "MCP调用结束时间",
    "MCP查询开始时间",
    "MCP查询结束时间",
    "关键词原文",
    "传入MCP的关键词",
    "过滤词原文",
    "传入MCP的过滤词",
    "声量类型",
    "MCP返回声量",
    "MCP返回互动量",
    "MCP返回NSR",
    "combined_query_volume",
    "sum_single_datasource_volume",
    "声量差异",
    "声量差异率",
    "combined_query_interaction",
    "sum_single_datasource_interaction",
    "互动量差异",
    "互动量差异率",
    "备注",
]

MAPPING_COLUMNS = [
    "审计子模块",
    "平台",
    "MCP dataSource",
    "额外平台参数",
    "映射说明",
    "特殊口径",
]

TREND_COLUMNS = [
    "审计子模块",
    "品牌",
    "展示品牌",
    "MCP查询品牌",
    "dataSource",
    "周期类型",
    "MCP调用开始时间",
    "MCP调用结束时间",
    "MCP查询开始时间",
    "MCP查询结束时间",
    "日期",
    "MCP返回当日声量",
    "MCP返回当日互动量",
    "MCP返回当日NSR",
    "MCP原始趋势字段名",
    "日声量加总",
    "日互动量加总",
    "MCP聚合声量",
    "MCP聚合互动量",
    "日加总是否等于MCP总量",
    "系统声量",
    "系统互动量",
    "MCP-系统声量差值",
    "MCP-系统互动量差值",
    "备注",
]

PLATFORM_DETAIL_COLUMNS = [
    "审计子模块",
    "展示品牌",
    "MCP查询品牌",
    "目标平台",
    "候选口径名称",
    "候选口径类型",
    "是否推荐正式使用",
    "不推荐原因",
    "MCP工具名",
    "dataSource请求值",
    "额外平台参数",
    "数据周期类型",
    "MCP查询开始时间",
    "MCP查询结束时间",
    "关键词原文",
    "传入MCP的关键词",
    "过滤词原文",
    "传入MCP的过滤词",
    "是否调用成功",
    "MCP返回声量",
    "MCP返回互动量",
    "MCP返回NSR",
    "失败原因",
    "备注",
    "系统声量",
    "系统互动量",
    "MCP-系统声量差值",
    "MCP-系统互动量差值",
    "是否接近系统",
]

PRIVATE_LIKE_COLUMNS = [
    "审计子模块",
    "展示品牌",
    "MCP查询品牌",
    "平台",
    "dataSource",
    "MCP工具名",
    "是否支持 private_like_cnt",
    "private_like_cnt 返回值",
    "普通互动量返回值",
    "字段来源",
    "字段路径",
    "备注",
]

CSV_COLUMNS = list(dict.fromkeys([*MAIN_COLUMNS, *SPLIT_COLUMNS, *MAPPING_COLUMNS, *TREND_COLUMNS, *PLATFORM_DETAIL_COLUMNS, *PRIVATE_LIKE_COLUMNS]))

RIGHT_ALIGN_COLUMNS = {
    "keywords_count",
    "filter_words_count",
    "MCP返回声量",
    "MCP返回互动量",
    "MCP返回正面声量",
    "MCP返回中性声量",
    "MCP返回负面声量",
    "MCP返回NSR",
    "combined_query_volume",
    "sum_single_datasource_volume",
    "声量差异",
    "声量差异率",
    "combined_query_interaction",
    "sum_single_datasource_interaction",
    "互动量差异",
    "互动量差异率",
    "MCP返回当日声量",
    "MCP返回当日互动量",
    "MCP返回当日NSR",
    "日声量加总",
    "日互动量加总",
    "MCP聚合声量",
    "MCP聚合互动量",
    "系统声量",
    "系统互动量",
    "MCP-系统声量差值",
    "MCP-系统互动量差值",
}

SPLIT_DATA_SOURCES = ["新闻", "微博", "微信", "小红书", "短视频", "视频", "论坛", "问答"]
PLATFORM_DETAIL_AUDIT_SUBMODULE = "1.2 平台细分口径核对"
PLATFORM_DETAIL_CANDIDATES = {
    "抖音": ["抖音app", "抖音", "短视频"],
    "微信视频号": ["微信视频号", "短视频", "微信"],
}
EXTRA_PLATFORM_FIELD_CANDIDATES = ("platform", "platformName", "subDataSource", "sourceName", "mediaType", "channel", "site", "appName")
HIGH_RISK_DATA_SOURCE_NOTES = {
    "短视频": "高风险口径：短视频可能包含抖音、快手、微信视频号及其他短视频来源。",
    "微信": "高风险口径：微信可能包含微信公众号、微信文章，也可能与微信视频号口径存在交叉。",
    "视频": "高风险口径：视频可能包含哔哩哔哩及其他视频站点。",
    "问答": "高风险口径：问答可能包含知乎及其他问答站点。",
}

VOLUME_KEYS = ("volume", "volumeCnt", "cnt", "totalVolume", "total_volume", "声量")
INTERACTION_KEYS = ("interaction", "interactionCnt", "titanInteractionCnt", "totalInteraction", "total_interaction", "互动量")
POSITIVE_KEYS = ("positive", "positiveCnt", "positiveVolume", "positive_volume", "正面声量")
NEUTRAL_KEYS = ("neutral", "neutralCnt", "neutralVolume", "neutral_volume", "中性声量")
NEGATIVE_KEYS = ("negative", "negativeCnt", "negativeVolume", "negative_volume", "负面声量")
NSR_KEYS = ("nsr", "NSR", "value", "口碑指数")
DATE_KEYS = ("date", "time", "day", "publishDate", "publish_date", "statDate", "stat_date", "dt", "日期")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build table MCP fetch audit assets.")
    parser.add_argument("--query-config-file", default=str(DEFAULT_CONFIG), help="Path to query_config JSON.")
    parser.add_argument("--output-dir", default=str(AUDIT_DIR), help="Directory for audit outputs.")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_query_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"query config does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("query config must be a JSON object")
    return data


def period_text(start_date: Any, end_date: Any) -> str:
    start = str(start_date or "").strip()
    end = str(end_date or "").strip()
    if start and end:
        return f"{start} 至 {end}"
    return EMPTY


def list_text(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return EMPTY
    text_values = [str(item) for item in values if str(item).strip()]
    return "；".join(text_values) if text_values else EMPTY


def count_list(values: Any) -> int:
    if not isinstance(values, list):
        return 0
    return len([item for item in values if str(item).strip()])


def has_list(values: Any) -> str:
    return "是" if count_list(values) else "否"


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


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
        return EMPTY
    if value.is_integer():
        return f"{value:.0f}"
    return f"{value:.6g}"


def format_rate(value: float | None) -> str:
    if value is None:
        return EMPTY
    return f"{value * 100:.2f}%"


def row_number(row: dict[str, str], key: str) -> float | None:
    return coerce_number(row.get(key))


def numbers_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) < 0.000001


def first_metric(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = coerce_number(record.get(key))
        if value is not None:
            return value
    return None


def first_record_date(record: dict[str, Any]) -> str | None:
    for key in DATE_KEYS:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
        return text
    return None


def trend_field_names(records: list[dict[str, Any]]) -> str:
    if not records:
        return EMPTY
    keys: list[str] = []
    for record in records[:3]:
        for key in record.keys():
            if key not in keys:
                keys.append(str(key))
    return "；".join(keys[:24]) if keys else EMPTY


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "dataList", "list", "rows", "result", "trend"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def metric_sum(payload: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        direct = first_metric(payload, keys)
        if direct is not None:
            return direct
    values = [first_metric(record, keys) for record in extract_records(payload)]
    numbers = [value for value in values if value is not None]
    return sum(numbers) if numbers else None


def metric_average(payload: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        direct = first_metric(payload, keys)
        if direct is not None:
            return direct
    values = [first_metric(record, keys) for record in extract_records(payload)]
    numbers = [value for value in values if value is not None]
    return sum(numbers) / len(numbers) if numbers else None


def summarize_fields(payload: Any) -> str:
    if isinstance(payload, dict):
        keys = list(payload.keys())
        records = extract_records(payload)
        if records:
            sample_keys = list(records[0].keys())
            return f"顶层字段：{', '.join(keys[:12])}；记录数：{len(records)}；记录字段：{', '.join(sample_keys[:16])}"
        return f"顶层字段：{', '.join(keys[:20])}" if keys else EMPTY
    if isinstance(payload, list):
        sample_keys = list(payload[0].keys()) if payload and isinstance(payload[0], dict) else []
        return f"列表记录数：{len(payload)}；记录字段：{', '.join(sample_keys[:16])}" if sample_keys else f"列表记录数：{len(payload)}"
    return type(payload).__name__


def response_summary(tool_name: str, payload: Any) -> dict[str, str]:
    return {
        "MCP返回声量": format_number(metric_sum(payload, VOLUME_KEYS)),
        "MCP返回互动量": format_number(metric_sum(payload, INTERACTION_KEYS)),
        "MCP返回正面声量": format_number(metric_sum(payload, POSITIVE_KEYS)),
        "MCP返回中性声量": format_number(metric_sum(payload, NEUTRAL_KEYS)),
        "MCP返回负面声量": format_number(metric_sum(payload, NEGATIVE_KEYS)),
        "MCP返回NSR": format_number(metric_average(payload, NSR_KEYS)) if tool_name == "getNsrTrend" else EMPTY,
        "MCP原始返回字段摘要": summarize_fields(payload),
    }


def base_row(
    *,
    module: str,
    period_type: str,
    report_period: str,
    compare_period: str,
    brand: str,
    platform: str,
    tool_name: str,
    arg0: dict[str, Any],
    raw_config: dict[str, Any],
) -> dict[str, str]:
    keywords = arg0.get("keywords") if isinstance(arg0, dict) else []
    filter_words = arg0.get("filterWords") if isinstance(arg0, dict) else []
    data_sources = arg0.get("dataSource") if isinstance(arg0, dict) else []
    extra_params = {
        key: arg0.get(key)
        for key in EXTRA_PLATFORM_FIELD_CANDIDATES
        if isinstance(arg0.get(key), str) and str(arg0.get(key)).strip()
    }
    mcp_brand = (
        arg0.get("analysisObject", {}).get("brand")
        if isinstance(arg0.get("analysisObject"), dict)
        else get_mcp_query_brand_for_config(brand, raw_config)
    )
    return {
        "审计子模块": "1.1 MCP总体取数" if module == "block_1_1" else "1.2 MCP平台取数",
        "核对类型": "总体合并查询",
        "dataSource请求方式": "combined",
        "dataSource请求值": list_text(data_sources),
        "模块": module,
        "数据周期类型": period_type,
        "报告周期": report_period,
        "同比周期": compare_period,
        "品牌": brand or EMPTY,
        "展示品牌": brand or EMPTY,
        "MCP查询品牌": str(mcp_brand or EMPTY),
        "平台/站点": platform or EMPTY,
        "MCP工具名": tool_name,
        "dataSource": list_text(data_sources),
        "正式dataSource": list_text(data_sources),
        "额外平台参数": json.dumps(extra_params, ensure_ascii=False, sort_keys=True) if extra_params else EMPTY,
        "keywords_raw": str(raw_config.get("keywords_raw") or "").strip() or EMPTY,
        "keywords_sent_to_mcp": list_text(keywords),
        "keywords_count": str(count_list(keywords)),
        "是否传入关键词": has_list(keywords),
        "filter_words_raw": str(raw_config.get("filter_words_raw") or "").strip() or EMPTY,
        "filter_words_sent_to_mcp": list_text(filter_words),
        "filter_words_count": str(count_list(filter_words)),
        "是否传入过滤词": has_list(filter_words),
        "MCP调用开始时间": EMPTY,
        "MCP调用结束时间": EMPTY,
        "MCP查询开始时间": str(arg0.get("startTimeStr") or EMPTY),
        "MCP查询结束时间": str(arg0.get("endTimeStr") or EMPTY),
        "MCP返回声量": EMPTY,
        "MCP返回互动量": EMPTY,
        "MCP返回正面声量": EMPTY,
        "MCP返回中性声量": EMPTY,
        "MCP返回负面声量": EMPTY,
        "MCP返回NSR": EMPTY,
        "MCP原始返回字段摘要": EMPTY,
        "备注": EMPTY,
    }


def call_and_record(
    *,
    caller: McpToolCaller,
    tool_name: str,
    arguments: dict[str, Any],
    row: dict[str, str],
    request_records: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, str]:
    row["MCP调用开始时间"] = now_iso()
    try:
        payload = caller.call_tool(tool_name, arguments)
        row["MCP调用结束时间"] = now_iso()
        row.update(response_summary(tool_name, payload))
        request_records.append(
            {
                "module": row["模块"],
                "period_type": row["数据周期类型"],
                "brand": row["品牌"],
                "platform": row["平台/站点"],
                "tool_name": tool_name,
                "arguments": redact(arguments),
                "response_summary": {key: row.get(key, EMPTY) for key in CSV_COLUMNS if key.startswith("MCP返回") or key == "MCP原始返回字段摘要"},
            }
        )
    except Exception as exc:
        row["MCP调用结束时间"] = now_iso()
        row["备注"] = f"调用失败：{exc}"
        warnings.append(
            {
                "module": row["模块"],
                "period_type": row["数据周期类型"],
                "brand": row["品牌"],
                "platform": row["平台/站点"],
                "tool_name": tool_name,
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
        )
    return row


def block_1_1_rows(
    *,
    caller: McpToolCaller,
    raw_config: dict[str, Any],
    report_period: str,
    compare_period: str,
    request_records: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    notes: list[str] = []
    query_config = normalize_block_1_1_config(raw_config, notes)
    rows: list[dict[str, str]] = []
    for brand in [query_config["brand"], *query_config["competitors"]]:
        for period_type, start_date, end_date in (
            ("current", query_config["start_date"], query_config["end_date"]),
            ("compare", query_config["compare_start_date"], query_config["compare_end_date"]),
        ):
            for tool_name in ("getVolumeInteractionTrend", "getNsrTrend"):
                arg0 = build_block_1_1_arg0(query_config, brand, start_date, end_date)
                row = base_row(
                    module="block_1_1",
                    period_type=period_type,
                    report_period=report_period,
                    compare_period=compare_period,
                    brand=brand,
                    platform="总体",
                    tool_name=tool_name,
                    arg0=arg0,
                    raw_config=raw_config,
                )
                rows.append(
                    call_and_record(
                        caller=caller,
                        tool_name=tool_name,
                        arguments={"arg0": arg0},
                        row=row,
                        request_records=request_records,
                        warnings=warnings,
                    )
                )
    return rows


def block_1_2_rows(
    *,
    caller: McpToolCaller,
    raw_config: dict[str, Any],
    report_period: str,
    compare_period: str,
    request_records: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    notes: list[str] = []
    query_config = normalize_block_1_2_config(raw_config, notes)
    brand_pool = build_brand_pool(query_config)
    rows: list[dict[str, str]] = []
    for platform in query_config["platforms"]:
        for brand in brand_pool:
            for period_type, start_date, end_date in (
                ("current", query_config["start_date"], query_config["end_date"]),
                ("compare", query_config["compare_start_date"], query_config["compare_end_date"]),
            ):
                for tool_name in ("getVolumeInteractionTrend", "getNsrTrend"):
                    arg0 = build_block_1_2_arg0(query_config, platform, brand, start_date, end_date)
                    row = base_row(
                        module="block_1_2",
                        period_type=period_type,
                        report_period=report_period,
                        compare_period=compare_period,
                        brand=brand,
                        platform=platform,
                        tool_name=tool_name,
                        arg0=arg0,
                        raw_config=raw_config,
                    )
                    rows.append(
                        call_and_record(
                            caller=caller,
                            tool_name=tool_name,
                            arguments={"arg0": arg0},
                            row=row,
                            request_records=request_records,
                            warnings=warnings,
                        )
                    )
    return rows


def split_base_row(
    *,
    query_config: dict[str, Any],
    raw_config: dict[str, Any],
    brand: str,
    period_type: str,
    start_date: str,
    end_date: str,
    request_mode: str,
    data_sources: list[str],
    check_type: str,
) -> dict[str, str]:
    return {
        "审计子模块": "1.1 dataSource拆分核对" if request_mode != "sum_of_single" else "1.1 dataSource单独加总",
        "核对类型": check_type,
        "模块": "block_1_1",
        "数据周期类型": period_type,
        "品牌": brand,
        "展示品牌": brand,
        "MCP查询品牌": get_mcp_query_brand_for_config(brand, raw_config),
        "dataSource请求方式": request_mode,
        "dataSource请求值": list_text(data_sources),
        "MCP调用开始时间": EMPTY,
        "MCP调用结束时间": EMPTY,
        "MCP查询开始时间": start_date,
        "MCP查询结束时间": end_date,
        "关键词原文": str(raw_config.get("keywords_raw") or "").strip() or EMPTY,
        "传入MCP的关键词": list_text(query_config.get("keywords")),
        "过滤词原文": str(raw_config.get("filter_words_raw") or "").strip() or EMPTY,
        "传入MCP的过滤词": list_text(query_config.get("filter_words")),
        "声量类型": "全量 PGC / UGC / BGC",
        "MCP返回声量": EMPTY,
        "MCP返回互动量": EMPTY,
        "MCP返回NSR": EMPTY,
        "combined_query_volume": EMPTY,
        "sum_single_datasource_volume": EMPTY,
        "声量差异": EMPTY,
        "声量差异率": EMPTY,
        "combined_query_interaction": EMPTY,
        "sum_single_datasource_interaction": EMPTY,
        "互动量差异": EMPTY,
        "互动量差异率": EMPTY,
        "备注": EMPTY,
    }


def build_split_arg0(
    query_config: dict[str, Any],
    brand: str,
    start_date: str,
    end_date: str,
    data_sources: list[str],
) -> dict[str, Any]:
    arg0 = build_block_1_1_arg0(query_config, brand, start_date, end_date)
    arg0["dataSource"] = data_sources
    return arg0


def call_summary_fetch(
    *,
    caller: McpToolCaller,
    query_config: dict[str, Any],
    raw_config: dict[str, Any],
    brand: str,
    period_type: str,
    start_date: str,
    end_date: str,
    request_mode: str,
    data_sources: list[str],
    check_type: str,
    request_records: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, str]:
    row = split_base_row(
        query_config=query_config,
        raw_config=raw_config,
        brand=brand,
        period_type=period_type,
        start_date=start_date,
        end_date=end_date,
        request_mode=request_mode,
        data_sources=data_sources,
        check_type=check_type,
    )
    arg0 = build_split_arg0(query_config, brand, start_date, end_date, data_sources)
    call_started = now_iso()
    row["MCP调用开始时间"] = call_started
    try:
        volume_payload = caller.call_tool("getVolumeInteractionTrend", {"arg0": arg0})
        nsr_payload = caller.call_tool("getNsrTrend", {"arg0": arg0})
        row["MCP调用结束时间"] = now_iso()
        row["MCP返回声量"] = format_number(metric_sum(volume_payload, VOLUME_KEYS))
        row["MCP返回互动量"] = format_number(metric_sum(volume_payload, INTERACTION_KEYS))
        row["MCP返回NSR"] = format_number(metric_average(nsr_payload, NSR_KEYS))
        row["备注"] = HIGH_RISK_DATA_SOURCE_NOTES.get(data_sources[0], EMPTY) if len(data_sources) == 1 else EMPTY
        request_records.append(
            {
                "module": "block_1_1",
                "audit_submodule": row["审计子模块"],
                "check_type": check_type,
                "period_type": period_type,
                "brand": brand,
                "data_sources": data_sources,
                "arguments": redact({"arg0": arg0}),
                "response_summary": {
                    "volume": row["MCP返回声量"],
                    "interaction": row["MCP返回互动量"],
                    "nsr": row["MCP返回NSR"],
                },
            }
        )
    except Exception as exc:
        row["MCP调用结束时间"] = now_iso()
        row["备注"] = f"调用失败：{exc}"
        warnings.append(
            {
                "module": "block_1_1",
                "audit_submodule": row["审计子模块"],
                "check_type": check_type,
                "period_type": period_type,
                "brand": brand,
                "data_sources": data_sources,
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
        )
    return row


def add_sum_row(
    *,
    query_config: dict[str, Any],
    raw_config: dict[str, Any],
    brand: str,
    period_type: str,
    start_date: str,
    end_date: str,
    combined_row: dict[str, str],
    single_rows: list[dict[str, str]],
) -> dict[str, str]:
    row = split_base_row(
        query_config=query_config,
        raw_config=raw_config,
        brand=brand,
        period_type=period_type,
        start_date=start_date,
        end_date=end_date,
        request_mode="sum_of_single",
        data_sources=SPLIT_DATA_SOURCES,
        check_type="dataSource单独加总",
    )
    combined_volume = row_number(combined_row, "MCP返回声量")
    combined_interaction = row_number(combined_row, "MCP返回互动量")
    sum_volume = sum(value for value in (row_number(item, "MCP返回声量") for item in single_rows) if value is not None)
    sum_interaction = sum(value for value in (row_number(item, "MCP返回互动量") for item in single_rows) if value is not None)
    volume_diff = sum_volume - combined_volume if combined_volume is not None else None
    interaction_diff = sum_interaction - combined_interaction if combined_interaction is not None else None
    row["MCP返回声量"] = format_number(sum_volume)
    row["MCP返回互动量"] = format_number(sum_interaction)
    row["combined_query_volume"] = format_number(combined_volume)
    row["sum_single_datasource_volume"] = format_number(sum_volume)
    row["声量差异"] = format_number(volume_diff)
    row["声量差异率"] = format_rate(safe_divide(volume_diff, combined_volume))
    row["combined_query_interaction"] = format_number(combined_interaction)
    row["sum_single_datasource_interaction"] = format_number(sum_interaction)
    row["互动量差异"] = format_number(interaction_diff)
    row["互动量差异率"] = format_rate(safe_divide(interaction_diff, combined_interaction))
    if volume_diff and volume_diff > 0:
        row["备注"] = "单独 dataSource 加总大于总体合并查询，可能存在跨 dataSource 交叉覆盖，或 MCP 合并查询存在去重逻辑。"
    return row


def block_1_1_datasource_split_rows(
    *,
    caller: McpToolCaller,
    raw_config: dict[str, Any],
    request_records: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    notes: list[str] = []
    query_config = normalize_block_1_1_config(raw_config, notes)
    rows: list[dict[str, str]] = []
    brands = [query_config["brand"], *query_config["competitors"]]
    for brand in brands:
        for period_type, start_date, end_date in (
            ("current", query_config["start_date"], query_config["end_date"]),
            ("compare", query_config["compare_start_date"], query_config["compare_end_date"]),
        ):
            combined_row = call_summary_fetch(
                caller=caller,
                query_config=query_config,
                raw_config=raw_config,
                brand=brand,
                period_type=period_type,
                start_date=start_date,
                end_date=end_date,
                request_mode="combined",
                data_sources=SPLIT_DATA_SOURCES,
                check_type="总体合并查询",
                request_records=request_records,
                warnings=warnings,
            )
            rows.append(combined_row)
            single_rows: list[dict[str, str]] = []
            for data_source in SPLIT_DATA_SOURCES:
                single_row = call_summary_fetch(
                    caller=caller,
                    query_config=query_config,
                    raw_config=raw_config,
                    brand=brand,
                    period_type=period_type,
                    start_date=start_date,
                    end_date=end_date,
                    request_mode="single",
                    data_sources=[data_source],
                    check_type="dataSource单独查询",
                    request_records=request_records,
                    warnings=warnings,
                )
                single_rows.append(single_row)
                rows.append(single_row)
            rows.append(
                add_sum_row(
                    query_config=query_config,
                    raw_config=raw_config,
                    brand=brand,
                    period_type=period_type,
                    start_date=start_date,
                    end_date=end_date,
                    combined_row=combined_row,
                    single_rows=single_rows,
                )
            )
    return rows


def platform_mapping_rows(raw_config: dict[str, Any]) -> list[dict[str, str]]:
    notes: list[str] = []
    query_config = normalize_block_1_2_config(raw_config, notes)
    rows: list[dict[str, str]] = []
    for platform in query_config["platforms"]:
        mapping = query_config["platform_mappings"][platform]
        data_sources = mapping.get("data_sources", [])
        extra_params = mapping.get("extra_params") or {}
        special_metric = mapping.get("special_soe_metric")
        note = "按当前项目 platform_mappings 配置传入 MCP。"
        if special_metric == "love_like":
            note += " SOE 使用特殊互动口径 love_like。"
        rows.append(
            {
                "审计子模块": "1.2 平台映射说明",
                "平台": platform,
                "MCP dataSource": list_text(data_sources),
                "额外平台参数": json.dumps(extra_params, ensure_ascii=False, sort_keys=True) if extra_params else EMPTY,
                "映射说明": note,
                "特殊口径": str(special_metric or EMPTY),
            }
        )
    return rows


def flatten_schema_keys(schema: Any, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, value in properties.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                keys.append(path)
                keys.extend(flatten_schema_keys(value, path))
        for key in ("items", "anyOf", "oneOf", "allOf"):
            if key in schema:
                keys.extend(flatten_schema_keys(schema[key], prefix))
    elif isinstance(schema, list):
        for item in schema:
            keys.extend(flatten_schema_keys(item, prefix))
    return keys


def mcp_tools_by_name(caller: McpToolCaller, warnings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    try:
        result = caller.client.request("tools/list")
    except Exception as exc:
        warnings.append(
            {
                "module": "platform_detail_schema",
                "message": "tools/list 调用失败，1.2 平台细分口径核对将只测试 dataSource 候选值。",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            }
        )
        return {}
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        warnings.append(
            {
                "module": "platform_detail_schema",
                "message": "tools/list 未返回 tools 数组，1.2 平台细分口径核对将只测试 dataSource 候选值。",
            }
        )
        return {}
    return {str(tool.get("name")): tool for tool in tools if isinstance(tool, dict) and tool.get("name")}


def discover_extra_platform_field(tools_by_name: dict[str, dict[str, Any]]) -> str | None:
    tool_names = ("getVolumeInteractionTrend", "getNsrTrend")
    schema_keys_by_tool: list[set[str]] = []
    for tool_name in tool_names:
        tool = tools_by_name.get(tool_name)
        if not isinstance(tool, dict):
            return None
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        schema_keys_by_tool.append(set(flatten_schema_keys(schema)))

    for field in EXTRA_PLATFORM_FIELD_CANDIDATES:
        supported_by_all = all(
            field in keys or f"arg0.{field}" in keys or any(key.endswith(f".{field}") for key in keys)
            for keys in schema_keys_by_tool
        )
        if supported_by_all:
            return field
    return None


def build_platform_detail_arg0(
    *,
    query_config: dict[str, Any],
    raw_config: dict[str, Any],
    display_brand: str,
    start_date: str,
    end_date: str,
    data_source: str,
    extra_params: dict[str, str],
) -> dict[str, Any]:
    arg0: dict[str, Any] = {
        "analysisObject": {"brand": get_mcp_query_brand_for_config(display_brand, raw_config)},
        "startTimeStr": start_date,
        "endTimeStr": end_date,
        "dataSource": [data_source],
        "keywords": query_config["keywords"],
        "filterWords": query_config["filter_words"],
        "statisticBy": "day",
    }
    arg0.update(extra_params)
    return arg0


def platform_detail_candidate_policy(target_platform: str, data_source: str, extra_params: dict[str, str]) -> dict[str, str]:
    if target_platform == "微信视频号":
        if data_source == "微信视频号":
            return {
                "候选口径类型": "优先候选",
                "是否推荐正式使用": "是",
                "不推荐原因": EMPTY,
            }
        if data_source == "短视频" and extra_params:
            return {
                "候选口径类型": "优先候选",
                "是否推荐正式使用": "是",
                "不推荐原因": EMPTY,
            }
        if data_source == "短视频":
            return {
                "候选口径类型": "大类对照",
                "是否推荐正式使用": "否",
                "不推荐原因": "短视频包含抖音、快手、微信视频号及其他来源，口径过宽。",
            }
        if data_source == "微信":
            return {
                "候选口径类型": "旧错误映射",
                "是否推荐正式使用": "否",
                "不推荐原因": "微信大类可能包含公众号、微信文章等，不等于微信视频号。",
            }
    if target_platform == "抖音" and data_source == "抖音app":
        return {
            "候选口径类型": "优先候选",
            "是否推荐正式使用": "是",
            "不推荐原因": EMPTY,
        }
    if data_source == "短视频":
        return {
            "候选口径类型": "大类对照",
            "是否推荐正式使用": "否",
            "不推荐原因": "短视频为大类口径，仅用于对照。",
        }
    return {
        "候选口径类型": "候选对照",
        "是否推荐正式使用": "待人工确认",
        "不推荐原因": EMPTY,
    }


def platform_detail_base_row(
    *,
    query_config: dict[str, Any],
    raw_config: dict[str, Any],
    display_brand: str,
    target_platform: str,
    candidate_name: str,
    data_source: str,
    extra_params: dict[str, str],
    period_type: str,
    start_date: str,
    end_date: str,
) -> dict[str, str]:
    return {
        "审计子模块": PLATFORM_DETAIL_AUDIT_SUBMODULE,
        "展示品牌": display_brand,
        "MCP查询品牌": get_mcp_query_brand_for_config(display_brand, raw_config),
        "目标平台": target_platform,
        "候选口径名称": candidate_name,
        **platform_detail_candidate_policy(target_platform, data_source, extra_params),
        "MCP工具名": "getVolumeInteractionTrend + getNsrTrend",
        "dataSource请求值": data_source,
        "额外平台参数": json.dumps(extra_params, ensure_ascii=False, sort_keys=True) if extra_params else EMPTY,
        "数据周期类型": period_type,
        "MCP查询开始时间": start_date,
        "MCP查询结束时间": end_date,
        "关键词原文": str(raw_config.get("keywords_raw") or "").strip() or EMPTY,
        "传入MCP的关键词": list_text(query_config.get("keywords")),
        "过滤词原文": str(raw_config.get("filter_words_raw") or "").strip() or EMPTY,
        "传入MCP的过滤词": list_text(query_config.get("filter_words")),
        "是否调用成功": "否",
        "MCP返回声量": EMPTY,
        "MCP返回互动量": EMPTY,
        "MCP返回NSR": EMPTY,
        "失败原因": EMPTY,
        "备注": EMPTY,
        "系统声量": EMPTY,
        "系统互动量": EMPTY,
        "MCP-系统声量差值": EMPTY,
        "MCP-系统互动量差值": EMPTY,
        "是否接近系统": EMPTY,
    }


def platform_detail_candidate_rows(
    *,
    caller: McpToolCaller,
    raw_config: dict[str, Any],
    request_records: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    notes: list[str] = []
    query_config = normalize_block_1_2_config(raw_config, notes)
    display_brand = query_config["brand"]
    tools_by_name = mcp_tools_by_name(caller, warnings)
    extra_field = discover_extra_platform_field(tools_by_name)
    periods = (
        ("current", query_config["start_date"], query_config["end_date"]),
        ("compare", query_config["compare_start_date"], query_config["compare_end_date"]),
    )
    rows: list[dict[str, str]] = []

    for target_platform, data_sources in PLATFORM_DETAIL_CANDIDATES.items():
        candidates: list[tuple[str, str, dict[str, str]]] = [
            (f"dataSource={data_source}", data_source, {}) for data_source in data_sources
        ]
        if extra_field:
            insert_at = 1 if target_platform == "微信视频号" else len(candidates)
            candidates.insert(
                insert_at,
                (
                    f"dataSource=短视频 + {extra_field}={target_platform}",
                    "短视频",
                    {extra_field: target_platform},
                ),
            )

        for candidate_name, data_source, extra_params in candidates:
            for period_type, start_date, end_date in periods:
                row = platform_detail_base_row(
                    query_config=query_config,
                    raw_config=raw_config,
                    display_brand=display_brand,
                    target_platform=target_platform,
                    candidate_name=candidate_name,
                    data_source=data_source,
                    extra_params=extra_params,
                    period_type=period_type,
                    start_date=start_date,
                    end_date=end_date,
                )
                arg0 = build_platform_detail_arg0(
                    query_config=query_config,
                    raw_config=raw_config,
                    display_brand=display_brand,
                    start_date=start_date,
                    end_date=end_date,
                    data_source=data_source,
                    extra_params=extra_params,
                )
                errors: list[str] = []
                volume_payload: Any = None
                nsr_payload: Any = None
                try:
                    volume_payload = caller.call_tool("getVolumeInteractionTrend", {"arg0": arg0})
                    row["MCP返回声量"] = format_number(metric_sum(volume_payload, VOLUME_KEYS))
                    row["MCP返回互动量"] = format_number(metric_sum(volume_payload, INTERACTION_KEYS))
                except Exception as exc:
                    errors.append(f"getVolumeInteractionTrend: {exc}")
                try:
                    nsr_payload = caller.call_tool("getNsrTrend", {"arg0": arg0})
                    row["MCP返回NSR"] = format_number(metric_average(nsr_payload, NSR_KEYS))
                except Exception as exc:
                    errors.append(f"getNsrTrend: {exc}")

                if errors:
                    row["是否调用成功"] = "否"
                    row["失败原因"] = "；".join(errors)
                    warnings.append(
                        {
                            "module": "block_1_2_platform_detail",
                            "audit_submodule": PLATFORM_DETAIL_AUDIT_SUBMODULE,
                            "target_platform": target_platform,
                            "candidate_name": candidate_name,
                            "period_type": period_type,
                            "data_source": data_source,
                            "extra_params": extra_params,
                            "message": row["失败原因"],
                        }
                    )
                else:
                    row["是否调用成功"] = "是"
                    note = HIGH_RISK_DATA_SOURCE_NOTES.get(data_source, EMPTY)
                    if extra_field and extra_params:
                        note = f"检测到 MCP schema 支持额外平台字段 {extra_field}，本候选已传入该字段。"
                    row["备注"] = note

                request_records.append(
                    {
                        "module": "block_1_2_platform_detail",
                        "audit_submodule": PLATFORM_DETAIL_AUDIT_SUBMODULE,
                        "target_platform": target_platform,
                        "candidate_name": candidate_name,
                        "period_type": period_type,
                        "tool_names": ["getVolumeInteractionTrend", "getNsrTrend"],
                        "arguments": redact({"arg0": arg0}),
                        "success": row["是否调用成功"],
                        "response_summary": {
                            "volume": row["MCP返回声量"],
                            "interaction": row["MCP返回互动量"],
                            "nsr": row["MCP返回NSR"],
                            "volume_response_fields": summarize_fields(volume_payload) if volume_payload is not None else EMPTY,
                            "nsr_response_fields": summarize_fields(nsr_payload) if nsr_payload is not None else EMPTY,
                        },
                    }
                )
                rows.append(row)
    return rows


def private_like_probe_rows(
    *,
    caller: McpToolCaller,
    raw_config: dict[str, Any],
    warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    try:
        probe = run_private_like_probe(caller, raw_config, post_count=5)
    except Exception as exc:
        warnings.append(
            {
                "module": "private_like_cnt_probe",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
        )
        return [
            {
                "审计子模块": "微信视频号 private_like_cnt 探测",
                "展示品牌": str(raw_config.get("brand") or EMPTY),
                "MCP查询品牌": get_mcp_query_brand_for_config(str(raw_config.get("brand") or ""), raw_config),
                "平台": "微信视频号",
                "dataSource": "微信视频号",
                "MCP工具名": "private_like_cnt_probe",
                "是否支持 private_like_cnt": "否",
                "private_like_cnt 返回值": EMPTY,
                "普通互动量返回值": EMPTY,
                "字段来源": EMPTY,
                "字段路径": EMPTY,
                "备注": f"探测失败：{exc}",
            }
        ], {"summary": {"status": "probe_failed", "message": str(exc)}}

    target = probe.get("target") if isinstance(probe.get("target"), dict) else {}
    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
    base = {
        "审计子模块": "微信视频号 private_like_cnt 探测",
        "展示品牌": str(target.get("display_brand") or raw_config.get("brand") or EMPTY),
        "MCP查询品牌": str(target.get("mcp_query_brand") or EMPTY),
        "平台": str(target.get("platform") or "微信视频号"),
        "dataSource": list_text(target.get("dataSource")) if isinstance(target.get("dataSource"), list) else str(target.get("dataSource") or EMPTY),
    }
    rows: list[dict[str, str]] = []
    for item in probe.get("aggregate_probe", []):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                **base,
                "MCP工具名": str(item.get("tool_name") or EMPTY),
                "是否支持 private_like_cnt": "是" if item.get("returned_private_like") else "否",
                "private_like_cnt 返回值": format_number(coerce_number(item.get("private_like_value"))),
                "普通互动量返回值": format_number(coerce_number(item.get("interaction_value"))),
                "字段来源": str(item.get("field_source") or EMPTY),
                "字段路径": list_text(item.get("matched_paths")),
                "备注": str(item.get("error") or EMPTY),
            }
        )

    posts_probe = probe.get("posts_probe") if isinstance(probe.get("posts_probe"), dict) else {}
    private_values = posts_probe.get("sample_private_like_values") if isinstance(posts_probe.get("sample_private_like_values"), list) else []
    interaction_values = posts_probe.get("sample_interaction_values") if isinstance(posts_probe.get("sample_interaction_values"), list) else []
    rows.append(
        {
            **base,
            "MCP工具名": "getPosts",
            "是否支持 private_like_cnt": "是" if posts_probe.get("returned_private_like") else "否",
            "private_like_cnt 返回值": list_text([format_number(coerce_number(value)) for value in private_values]),
            "普通互动量返回值": list_text([format_number(coerce_number(value)) for value in interaction_values]),
            "字段来源": str(posts_probe.get("field_source") or EMPTY),
            "字段路径": list_text(posts_probe.get("matched_paths")),
            "备注": str(posts_probe.get("error") or summary.get("message") or EMPTY),
        }
    )
    rows.append(
        {
            **base,
            "MCP工具名": "能力结论",
            "是否支持 private_like_cnt": "是" if summary.get("aggregate_supported") else "否",
            "private_like_cnt 返回值": EMPTY,
            "普通互动量返回值": EMPTY,
            "字段来源": str(summary.get("status") or EMPTY),
            "字段路径": EMPTY,
            "备注": str(summary.get("message") or EMPTY),
        }
    )
    if not summary.get("aggregate_supported"):
        warnings.append(
            {
                "module": "private_like_cnt_probe",
                "message": summary.get("message") or "MCP 当前未返回 private_like_cnt 聚合值，微信视频号 SOE 无法按爱心赞口径复现。",
                "status": summary.get("status"),
            }
        )
    return rows, probe


def daily_trend_empty_row(
    *,
    brand: str,
    data_source_label: str,
    period_type: str,
    call_start: str,
    call_end: str,
    query_start: str,
    query_end: str,
    remark: str,
) -> dict[str, str]:
    return {
        "审计子模块": "MCP 按日趋势拆分核对",
        "品牌": brand,
        "展示品牌": brand,
        "MCP查询品牌": get_mcp_query_brand_for_config(brand, None),
        "dataSource": data_source_label,
        "周期类型": period_type,
        "MCP调用开始时间": call_start or EMPTY,
        "MCP调用结束时间": call_end or EMPTY,
        "MCP查询开始时间": query_start,
        "MCP查询结束时间": query_end,
        "日期": EMPTY,
        "MCP返回当日声量": EMPTY,
        "MCP返回当日互动量": EMPTY,
        "MCP返回当日NSR": EMPTY,
        "MCP原始趋势字段名": EMPTY,
        "日声量加总": EMPTY,
        "日互动量加总": EMPTY,
        "MCP聚合声量": EMPTY,
        "MCP聚合互动量": EMPTY,
        "日加总是否等于MCP总量": EMPTY,
        "系统声量": EMPTY,
        "系统互动量": EMPTY,
        "MCP-系统声量差值": EMPTY,
        "MCP-系统互动量差值": EMPTY,
        "备注": remark,
    }


def nsr_by_date(payload: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for record in extract_records(payload):
        date_value = first_record_date(record)
        if not date_value:
            continue
        result[date_value] = format_number(first_metric(record, NSR_KEYS))
    return result


def build_daily_rows_from_payloads(
    *,
    brand: str,
    data_source_label: str,
    period_type: str,
    call_start: str,
    call_end: str,
    query_start: str,
    query_end: str,
    volume_payload: Any,
    nsr_payload: Any,
    warnings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    records = extract_records(volume_payload)
    records_with_date = [record for record in records if first_record_date(record)]
    if not records_with_date:
        return [
            daily_trend_empty_row(
                brand=brand,
                data_source_label=data_source_label,
                period_type=period_type,
                call_start=call_start,
                call_end=call_end,
                query_start=query_start,
                query_end=query_end,
                remark="当前 MCP response 未返回按日趋势明细，无法进行逐日核对。",
            )
        ]

    nsr_lookup = nsr_by_date(nsr_payload)
    aggregate_volume = metric_sum(volume_payload, VOLUME_KEYS)
    aggregate_interaction = metric_sum(volume_payload, INTERACTION_KEYS)
    daily_volume_sum = sum(value for value in (first_metric(record, VOLUME_KEYS) for record in records_with_date) if value is not None)
    daily_interaction_sum = sum(
        value for value in (first_metric(record, INTERACTION_KEYS) for record in records_with_date) if value is not None
    )
    volume_equal = numbers_equal(daily_volume_sum, aggregate_volume)
    interaction_equal = numbers_equal(daily_interaction_sum, aggregate_interaction)
    total_equal_text = "是" if volume_equal and interaction_equal else "否"
    if not volume_equal or not interaction_equal:
        warnings.append(
            {
                "module": "daily_trend",
                "brand": brand,
                "data_source": data_source_label,
                "period_type": period_type,
                "message": "每日趋势加总与 MCP 聚合返回值不一致",
                "daily_volume_sum": daily_volume_sum,
                "aggregate_volume": aggregate_volume,
                "daily_interaction_sum": daily_interaction_sum,
                "aggregate_interaction": aggregate_interaction,
            }
        )

    field_names = trend_field_names(records_with_date)
    rows: list[dict[str, str]] = []
    for record in records_with_date:
        date_value = first_record_date(record) or EMPTY
        rows.append(
            {
                "审计子模块": "MCP 按日趋势拆分核对",
                "品牌": brand,
                "展示品牌": brand,
                "MCP查询品牌": get_mcp_query_brand_for_config(brand, None),
                "dataSource": data_source_label,
                "周期类型": period_type,
                "MCP调用开始时间": call_start,
                "MCP调用结束时间": call_end,
                "MCP查询开始时间": query_start,
                "MCP查询结束时间": query_end,
                "日期": date_value,
                "MCP返回当日声量": format_number(first_metric(record, VOLUME_KEYS)),
                "MCP返回当日互动量": format_number(first_metric(record, INTERACTION_KEYS)),
                "MCP返回当日NSR": nsr_lookup.get(date_value, EMPTY),
                "MCP原始趋势字段名": field_names,
                "日声量加总": format_number(daily_volume_sum),
                "日互动量加总": format_number(daily_interaction_sum),
                "MCP聚合声量": format_number(aggregate_volume),
                "MCP聚合互动量": format_number(aggregate_interaction),
                "日加总是否等于MCP总量": total_equal_text,
                "系统声量": EMPTY,
                "系统互动量": EMPTY,
                "MCP-系统声量差值": EMPTY,
                "MCP-系统互动量差值": EMPTY,
                "备注": EMPTY,
            }
        )
    return rows


def daily_trend_rows(
    *,
    caller: McpToolCaller,
    raw_config: dict[str, Any],
    request_records: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    notes: list[str] = []
    query_config = normalize_block_1_1_config(raw_config, notes)
    brand = query_config["brand"]
    period_type = "current"
    start_date = query_config["start_date"]
    end_date = query_config["end_date"]
    data_source_sets = [("总体合并查询", SPLIT_DATA_SOURCES), *[(item, [item]) for item in SPLIT_DATA_SOURCES]]
    rows: list[dict[str, str]] = []
    for label, data_sources in data_source_sets:
        arg0 = build_split_arg0(query_config, brand, start_date, end_date, data_sources)
        call_start = now_iso()
        try:
            volume_payload = caller.call_tool("getVolumeInteractionTrend", {"arg0": arg0})
            nsr_payload = caller.call_tool("getNsrTrend", {"arg0": arg0})
            call_end = now_iso()
            rows.extend(
                build_daily_rows_from_payloads(
                    brand=brand,
                    data_source_label=label,
                    period_type=period_type,
                    call_start=call_start,
                    call_end=call_end,
                    query_start=start_date,
                    query_end=end_date,
                    volume_payload=volume_payload,
                    nsr_payload=nsr_payload,
                    warnings=warnings,
                )
            )
            request_records.append(
                {
                    "module": "daily_trend",
                    "audit_submodule": "MCP 按日趋势拆分核对",
                    "period_type": period_type,
                    "brand": brand,
                    "data_sources": data_sources,
                    "arguments": redact({"arg0": arg0}),
                    "response_summary": {
                        "volume": format_number(metric_sum(volume_payload, VOLUME_KEYS)),
                        "interaction": format_number(metric_sum(volume_payload, INTERACTION_KEYS)),
                        "records": len(extract_records(volume_payload)),
                    },
                }
            )
        except Exception as exc:
            call_end = now_iso()
            rows.append(
                daily_trend_empty_row(
                    brand=brand,
                    data_source_label=label,
                    period_type=period_type,
                    call_start=call_start,
                    call_end=call_end,
                    query_start=start_date,
                    query_end=end_date,
                    remark=f"调用失败：{exc}",
                )
            )
            warnings.append(
                {
                    "module": "daily_trend",
                    "brand": brand,
                    "data_source": label,
                    "period_type": period_type,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )
    return rows


def render_config(raw_config: dict[str, Any], report_period: str, compare_period: str) -> str:
    competitors = list_text(raw_config.get("competitors"))
    keywords = list_text(raw_config.get("keywords"))
    filter_words = list_text(raw_config.get("filter_words"))
    items = [
        ("本品品牌", str(raw_config.get("brand") or EMPTY)),
        ("对标品牌", competitors),
        ("监测周期", report_period),
        ("同比周期", compare_period),
        ("关键词原文", str(raw_config.get("keywords_raw") or "").strip() or EMPTY),
        ("传入 MCP 的关键词", keywords),
        ("过滤词原文", str(raw_config.get("filter_words_raw") or "").strip() or EMPTY),
        ("传入 MCP 的过滤词", filter_words),
    ]
    return "<dl class=\"config-grid\">" + "".join(
        f"<dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd>" for label, value in items
    ) + "</dl>"


def render_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = []
    for row in rows:
        cells = []
        for column in columns:
            class_name = "num" if column in RIGHT_ALIGN_COLUMNS else ""
            cells.append(f'<td class="{class_name}">{html.escape(str(row.get(column) or EMPTY))}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    if not body:
        body.append(f'<tr><td class="empty" colspan="{len(columns)}">暂无数据</td></tr>')
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def render_html(payload: dict[str, Any]) -> str:
    rows = payload["rows"]
    rows_1_1 = [row for row in rows if row.get("审计子模块") == "1.1 MCP总体取数"]
    split_rows = payload["datasource_split_rows"]
    trend_rows = payload["daily_trend_rows"]
    rows_1_2 = [row for row in rows if row.get("审计子模块") == "1.2 MCP平台取数"]
    mapping_rows = payload["platform_mapping_rows"]
    platform_detail_rows = payload["platform_detail_rows"]
    private_like_rows = payload["private_like_probe_rows"]
    trend_notice = ""
    if trend_rows and all(row.get("日期") == EMPTY for row in trend_rows):
        trend_notice = '<p class="notice">当前 MCP response 未返回按日趋势明细，无法进行逐日核对。</p>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>表格 MCP 取数核对</title>
  <style>
    body {{
      margin: 0;
      padding: 28px;
      background: #fff;
      color: #1f2937;
      font-family: Arial, "Microsoft YaHei", "PingFang SC", sans-serif;
      line-height: 1.55;
    }}
    main {{ max-width: 1560px; margin: 0 auto; }}
    h1 {{ margin: 0 0 14px; font-size: 24px; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; }}
    .config-grid {{ display: grid; grid-template-columns: 160px minmax(0, 1fr); border: 1px solid #d9dee7; }}
    .config-grid dt, .config-grid dd {{ margin: 0; padding: 9px 12px; border-bottom: 1px solid #e5e7eb; }}
    .config-grid dt {{ background: #f3f5f8; font-weight: 700; color: #111827; }}
    .config-grid dd {{ border-left: 1px solid #e5e7eb; word-break: break-word; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid #d9dee7; }}
    table {{ width: 100%; min-width: 2200px; border-collapse: collapse; background: #fff; }}
    .mapping-table table {{ min-width: 780px; }}
    .platform-detail-table table {{ min-width: 2600px; }}
    .private-like-table table {{ min-width: 1500px; }}
    th, td {{ border: 1px solid #d9dee7; padding: 9px 10px; font-size: 12px; vertical-align: top; }}
    th {{ background: #f3f5f8; color: #111827; font-weight: 700; white-space: nowrap; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    td.empty {{ text-align: center; color: #6b7280; }}
    .notice {{ padding: 10px 12px; border: 1px solid #facc15; background: #fefce8; color: #854d0e; }}
    .notes {{ margin-top: 28px; padding-top: 16px; border-top: 1px solid #e5e7eb; }}
  </style>
</head>
<body>
  <main>
    <h1>表格 MCP 取数核对</h1>
    <h2>当前配置</h2>
    {render_config(payload["config"], payload["report_period"], payload["compare_period"])}

    <h2>1.1 品牌整体表现 MCP 取数</h2>
    <div class="table-wrap">{render_table(rows_1_1, MAIN_COLUMNS)}</div>

    <h2>1.1 品牌整体表现 dataSource 拆分核对</h2>
    <div class="table-wrap">{render_table(split_rows, SPLIT_COLUMNS)}</div>

    <h2>MCP 按日趋势拆分核对</h2>
    {trend_notice}
    <div class="table-wrap">{render_table(trend_rows, TREND_COLUMNS)}</div>

    <h2>1.2 品牌整体分平台表现 MCP 取数</h2>
    <div class="table-wrap">{render_table(rows_1_2, MAIN_COLUMNS)}</div>

    <h2>1.2 平台细分口径核对</h2>
    <p class="notice">本模块用于验证 1.2 分平台表现中“抖音”和“微信视频号”的 MCP 查询口径。本模块不会修改正式报告映射，仅用于人工判断哪个 MCP 参数与系统页面最接近。若多个候选口径成功返回数据，请将候选结果与系统页面的“抖音 / 微信视频号”单平台数据对比，最接近者即为应采用口径。</p>
    <div class="table-wrap platform-detail-table">{render_table(platform_detail_rows, PLATFORM_DETAIL_COLUMNS)}</div>

    <h2>微信视频号 private_like_cnt 探测</h2>
    <p class="notice">本模块只探测 MCP 是否返回 private_like_cnt / 爱心赞相关字段；未确认聚合接口支持前，不用普通互动量替代爱心赞口径。</p>
    <div class="table-wrap private-like-table">{render_table(private_like_rows, PRIVATE_LIKE_COLUMNS)}</div>

    <h2>1.2 平台映射说明</h2>
    <div class="table-wrap mapping-table">{render_table(mapping_rows, MAPPING_COLUMNS)}</div>

    <section class="notes">
      <h2>数据口径提示</h2>
      <ul>
        <li>短视频可能包含抖音、快手、微信视频号及其他短视频来源。</li>
        <li>微信可能包含微信公众号、微信文章，也可能与微信视频号口径存在交叉。</li>
        <li>视频可能包含哔哩哔哩及其他视频站点。</li>
        <li>问答可能包含知乎及其他问答站点。</li>
      </ul>
      <h2>数据说明</h2>
      <ul>
        <li>本页只展示表格 1 / 表格 2 调用 MCP 的请求参数和返回摘要。</li>
        <li>不调用 LLM。</li>
        <li>不生成正式报告。</li>
        <li>不包含第三部分原帖。</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    raw_config = load_query_config(Path(args.query_config_file))
    report_period = period_text(raw_config.get("start_date"), raw_config.get("end_date"))
    compare_period = period_text(raw_config.get("compare_start_date"), raw_config.get("compare_end_date"))

    server_url = os.getenv("MCP_SERVER_URL")
    if not server_url:
        raise McpError("MCP_SERVER_URL is required for table MCP fetch audit")
    if not os.getenv("MCP_AUTHORIZATION"):
        raise McpError("MCP_AUTHORIZATION is required for table MCP fetch audit")
    parsed = urllib.parse.urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise McpError("MCP_SERVER_URL must be an absolute http(s) URL")

    client = McpHttpClient(server_url, load_headers())
    caller = McpToolCaller(client)
    caller.initialize()

    warnings: list[dict[str, Any]] = []
    request_records: list[dict[str, Any]] = []
    rows = [
        *block_1_1_rows(
            caller=caller,
            raw_config=raw_config,
            report_period=report_period,
            compare_period=compare_period,
            request_records=request_records,
            warnings=warnings,
        ),
        *block_1_2_rows(
            caller=caller,
            raw_config=raw_config,
            report_period=report_period,
            compare_period=compare_period,
            request_records=request_records,
            warnings=warnings,
        ),
    ]
    datasource_split_rows = block_1_1_datasource_split_rows(
        caller=caller,
        raw_config=raw_config,
        request_records=request_records,
        warnings=warnings,
    )
    trend_rows = daily_trend_rows(
        caller=caller,
        raw_config=raw_config,
        request_records=request_records,
        warnings=warnings,
    )
    platform_detail_rows = platform_detail_candidate_rows(
        caller=caller,
        raw_config=raw_config,
        request_records=request_records,
        warnings=warnings,
    )
    private_like_rows, private_like_probe = private_like_probe_rows(
        caller=caller,
        raw_config=raw_config,
        warnings=warnings,
    )
    mapping_rows = platform_mapping_rows(raw_config)
    csv_rows = [*rows, *datasource_split_rows, *trend_rows, *platform_detail_rows, *private_like_rows, *mapping_rows]

    payload = {
        "generated_at": now_iso(),
        "config": redact(raw_config),
        "report_period": report_period,
        "compare_period": compare_period,
        "rows": rows,
        "datasource_split_rows": datasource_split_rows,
        "daily_trend_rows": trend_rows,
        "platform_detail_rows": platform_detail_rows,
        "private_like_probe_rows": private_like_rows,
        "private_like_probe": redact(private_like_probe),
        "platform_mapping_rows": mapping_rows,
        "requests": request_records,
        "warnings": redact(warnings),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "table_mcp_fetch_audit.html").write_text(render_html(payload), encoding="utf-8")
    write_csv(output_dir / "table_mcp_fetch_audit.csv", csv_rows)
    (output_dir / "table_mcp_fetch_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"audit html written to: {output_dir / 'table_mcp_fetch_audit.html'}")
    print(f"audit csv written to: {output_dir / 'table_mcp_fetch_audit.csv'}")
    print(f"audit json written to: {output_dir / 'table_mcp_fetch_audit.json'}")
    print(
        json.dumps(
            {
                "mcp_fetch_rows": len(rows),
                "datasource_split_rows": len(datasource_split_rows),
                "daily_trend_rows": len(trend_rows),
                "platform_detail_rows": len(platform_detail_rows),
                "private_like_probe_rows": len(private_like_rows),
                "platform_mapping_rows": len(mapping_rows),
                "csv_rows": len(csv_rows),
                "warnings": len(warnings),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    started = time.perf_counter()
    try:
        raise SystemExit(main())
    finally:
        print(f"elapsed_seconds={time.perf_counter() - started:.3f}", file=sys.stderr)
