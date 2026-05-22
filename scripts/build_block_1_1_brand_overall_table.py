#!/usr/bin/env python3
"""Build Block 1.1 brand overall performance table.

This script calls MCP data tools directly and writes only the JSON/HTML assets
for the first report table. It does not call an LLM and does not fetch posts.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_report_data_package import (
    McpError,
    McpHttpClient,
    McpToolCaller,
    ensure_string_list,
    load_headers,
    redact,
)
from brand_mapping import get_mcp_query_brand_for_config


BLOCK_ID = "block_1_1_brand_overall_table"
BLOCK_NAME = "品牌整体表现"
BLOCKS_DIR = Path("outputs") / "blocks"
JSON_OUTPUT = BLOCKS_DIR / f"{BLOCK_ID}.json"
HTML_OUTPUT = BLOCKS_DIR / f"{BLOCK_ID}.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build report block 1.1 brand overall table.")
    parser.add_argument("--query-config-file", help="Path to query_config JSON file.")
    parser.add_argument("--query-config-json", help="Inline query_config JSON object.")
    parser.add_argument("--show-notes", action="store_true", help="Show calculation notes below the HTML table.")
    return parser.parse_args()


def previous_year_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year - 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year - 1, day=28).isoformat()


def normalize_query_config(raw_config: dict[str, Any], notes: list[str]) -> dict[str, Any]:
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

    compare_start_date = raw_config.get("compare_start_date")
    compare_end_date = raw_config.get("compare_end_date")
    if not isinstance(compare_start_date, str) or not compare_start_date:
        compare_start_date = previous_year_date(start_date)
        notes.append("compare_start_date 未配置，默认使用当前周期开始日期的上一年同日。")
    if not isinstance(compare_end_date, str) or not compare_end_date:
        compare_end_date = previous_year_date(end_date)
        notes.append("compare_end_date 未配置，默认使用当前周期结束日期的上一年同日。")

    data_sources = ensure_string_list(raw_config.get("data_sources"), "data_sources")
    if not data_sources:
        raise ValueError("query_config.data_sources is required for block 1.1")

    return {
        "brand": brand,
        "competitors": ensure_string_list(raw_config.get("competitors"), "competitors"),
        "start_date": start_date,
        "end_date": end_date,
        "compare_start_date": compare_start_date,
        "compare_end_date": compare_end_date,
        "data_sources": data_sources,
        "keywords": ensure_string_list(raw_config.get("keywords"), "keywords"),
        "filter_words": ensure_string_list(raw_config.get("filter_words"), "filter_words"),
    }


def load_query_config(args: argparse.Namespace, notes: list[str]) -> dict[str, Any]:
    if args.query_config_json:
        return normalize_query_config(json.loads(args.query_config_json), notes)
    if args.query_config_file:
        return normalize_query_config(json.loads(Path(args.query_config_file).read_text(encoding="utf-8")), notes)

    env_json = os.getenv("QUERY_CONFIG_JSON")
    if env_json:
        return normalize_query_config(json.loads(env_json), notes)

    env_file = os.getenv("QUERY_CONFIG_FILE")
    if env_file:
        return normalize_query_config(json.loads(Path(env_file).read_text(encoding="utf-8")), notes)

    raise ValueError("Provide --query-config-file, --query-config-json, QUERY_CONFIG_FILE, or QUERY_CONFIG_JSON")


def build_arg0(query_config: dict[str, Any], brand: str, start_date: str, end_date: str) -> dict[str, Any]:
    return {
        "analysisObject": {"brand": get_mcp_query_brand_for_config(brand, query_config)},
        "startTimeStr": start_date,
        "endTimeStr": end_date,
        "dataSource": query_config["data_sources"],
        "keywords": query_config["keywords"],
        "filterWords": query_config["filter_words"],
        "statisticBy": "day",
    }


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


def sum_metric(records: list[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for record in records:
        for key in keys:
            value = coerce_number(record.get(key))
            if value is not None:
                values.append(value)
                break
    if not values:
        return None
    return sum(values)


def average_metric(records: list[dict[str, Any]], keys: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for record in records:
        for key in keys:
            value = coerce_number(record.get(key))
            if value is not None:
                values.append(value)
                break
    if not values:
        return None
    return sum(values) / len(values)


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def call_tool(
    caller: McpToolCaller,
    tool_name: str,
    query_config: dict[str, Any],
    brand: str,
    start_date: str,
    end_date: str,
) -> Any:
    return caller.call_tool(tool_name, {"arg0": build_arg0(query_config, brand, start_date, end_date)})


def collect_brand_metrics(
    caller: McpToolCaller,
    query_config: dict[str, Any],
    brand: str,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "brand": brand,
        "current_volume": None,
        "current_interaction": None,
        "current_nsr": None,
        "compare_volume": None,
        "compare_interaction": None,
        "compare_nsr": None,
    }
    periods = [
        ("current", query_config["start_date"], query_config["end_date"]),
        ("compare", query_config["compare_start_date"], query_config["compare_end_date"]),
    ]

    for period_key, start_date, end_date in periods:
        try:
            payload = call_tool(caller, "getVolumeInteractionTrend", query_config, brand, start_date, end_date)
            records = extract_records(payload)
            if not records:
                warnings.append({"brand": brand, "tool": "getVolumeInteractionTrend", "period": period_key, "message": "trend data is empty"})
            metrics[f"{period_key}_volume"] = sum_metric(records, ("volume", "volumeCnt", "cnt"))
            metrics[f"{period_key}_interaction"] = sum_metric(records, ("interaction", "interactionCnt", "titanInteractionCnt"))
        except Exception as exc:
            warnings.append(
                {
                    "brand": brand,
                    "tool": "getVolumeInteractionTrend",
                    "period": period_key,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )

        try:
            payload = call_tool(caller, "getNsrTrend", query_config, brand, start_date, end_date)
            records = extract_records(payload)
            if not records:
                warnings.append({"brand": brand, "tool": "getNsrTrend", "period": period_key, "message": "trend data is empty"})
            metrics[f"{period_key}_nsr"] = average_metric(records, ("nsr", "NSR", "value"))
        except Exception as exc:
            warnings.append(
                {
                    "brand": brand,
                    "tool": "getNsrTrend",
                    "period": period_key,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )

    return metrics


def build_rows(query_config: dict[str, Any], brand_metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current_total_volume = sum(item["current_volume"] for item in brand_metrics if item["current_volume"] is not None)
    compare_total_volume = sum(item["compare_volume"] for item in brand_metrics if item["compare_volume"] is not None)
    current_total_interaction = sum(item["current_interaction"] for item in brand_metrics if item["current_interaction"] is not None)
    compare_total_interaction = sum(item["compare_interaction"] for item in brand_metrics if item["compare_interaction"] is not None)

    target_row_values: dict[str, float | None] = {}
    rows: list[dict[str, Any]] = []
    for item in brand_metrics:
        sov = safe_divide(item["current_volume"], current_total_volume)
        compare_sov = safe_divide(item["compare_volume"], compare_total_volume)
        soe = safe_divide(item["current_interaction"], current_total_interaction)
        compare_soe = safe_divide(item["compare_interaction"], compare_total_interaction)
        row = {
            "brand": item["brand"],
            "total_volume": item["current_volume"],
            "total_interaction": item["current_interaction"],
            "nsr": item["current_nsr"],
            "compare_total_volume": item["compare_volume"],
            "compare_total_interaction": item["compare_interaction"],
            "compare_nsr": item["compare_nsr"],
            "sov": sov,
            "sov_yoy": subtract(sov, compare_sov),
            "sov_vs_target": None,
            "soe": soe,
            "soe_yoy": subtract(soe, compare_soe),
            "soe_vs_target": None,
            "nsr_yoy": subtract(item["current_nsr"], item["compare_nsr"]),
        }
        if item["brand"] == query_config["brand"]:
            target_row_values = {"sov": sov, "soe": soe}
        rows.append(row)

    for row in rows:
        if row["brand"] == query_config["brand"]:
            continue
        row["sov_vs_target"] = safe_divide(target_row_values.get("sov"), row["sov"])
        row["soe_vs_target"] = safe_divide(target_row_values.get("soe"), row["soe"])

    return rows


def format_percent(value: float | None) -> str:
    if value is None:
        return "——"
    return f"{value * 100:.2f}%"


def format_integer_percent(value: float | None) -> str:
    if value is None:
        return "——"
    return f"{value * 100:.0f}%"


def render_html(block: dict[str, Any], show_notes: bool = False) -> str:
    rows_html = []
    for row in block["rows"]:
        cells = [
            html.escape(str(row["brand"])),
            format_percent(row["sov"]),
            format_percent(row["sov_yoy"]),
            format_integer_percent(row["sov_vs_target"]),
            format_percent(row["soe"]),
            format_percent(row["soe_yoy"]),
            format_integer_percent(row["soe_vs_target"]),
            format_percent(row["nsr"]),
            format_percent(row["nsr_yoy"]),
        ]
        rows_html.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")

    notes_html = ""
    notes_style = ""
    if show_notes and block["notes"]:
        notes_style = """
    .notes {
      margin-top: 12px;
      color: #5b6675;
      font-size: 13px;
      line-height: 1.5;
    }
    .notes p {
      margin: 4px 0;
    }"""
        notes_html = '<div class="notes">' + "".join(f"<p>{html.escape(note)}</p>" for note in block["notes"]) + "</div>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>1.1品牌整体表现</title>
  <style>
    body {{
      margin: 0;
      padding: 32px;
      background: #ffffff;
      color: #1f2933;
      font-family: Arial, "Microsoft YaHei", sans-serif;
    }}
    .wrap {{
      max-width: 1120px;
      margin: 0 auto;
    }}
    h1 {{
      margin: 0 0 16px;
      font-size: 22px;
      font-weight: 700;
      line-height: 1.35;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      background: #ffffff;
      border: 1px solid #d9dee7;
    }}
    th, td {{
      border: 1px solid #d9dee7;
      padding: 11px 12px;
      text-align: center;
      font-size: 14px;
      line-height: 1.35;
      word-break: keep-all;
    }}
    th {{
      background: #f3f5f8;
      font-weight: 700;
    }}
    td:first-child, th:first-child {{
      text-align: center;
      vertical-align: middle;
      width: 14%;
    }}{notes_style}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>1.1品牌整体表现</h1>
    <table>
      <thead>
        <tr>
          <th>品牌</th>
          <th>SOV</th>
          <th>同比</th>
          <th>控比竞品</th>
          <th>SOE</th>
          <th>同比</th>
          <th>控比竞品</th>
          <th>NSR</th>
          <th>同比</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>
    {notes_html}
  </main>
</body>
</html>
"""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_html(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    notes: list[str] = []
    warnings: list[dict[str, Any]] = []
    try:
        args = parse_args()
        query_config = load_query_config(args, notes)
        server_url = os.getenv("MCP_SERVER_URL")
        if not server_url:
            raise McpError("MCP_SERVER_URL is required")
        parsed = urllib.parse.urlparse(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McpError("MCP_SERVER_URL must be an absolute http(s) URL")

        client = McpHttpClient(server_url, load_headers())
        caller = McpToolCaller(client)
        caller.initialize()

        brands = [query_config["brand"], *query_config["competitors"]]
        brand_metrics = [collect_brand_metrics(caller, query_config, brand, warnings) for brand in brands]
        block = {
            "block_id": BLOCK_ID,
            "block_name": BLOCK_NAME,
            "period": {"start_date": query_config["start_date"], "end_date": query_config["end_date"]},
            "compare_period": {
                "start_date": query_config["compare_start_date"],
                "end_date": query_config["compare_end_date"],
            },
            "calculation_scope": "selected_brand_pool",
            "target_brand": query_config["brand"],
            "rows": build_rows(query_config, brand_metrics),
            "notes": notes,
            "warnings": warnings,
        }
        write_json(JSON_OUTPUT, block)
        write_html(HTML_OUTPUT, render_html(block, show_notes=args.show_notes))
        print(f"block json written to: {JSON_OUTPUT}")
        print(f"block html written to: {HTML_OUTPUT}")
        print(json.dumps({"rows": len(block["rows"]), "warnings": len(warnings)}, ensure_ascii=False))
        return 0
    except Exception as exc:
        warnings.append({"stage": BLOCK_ID, "error_type": exc.__class__.__name__, "message": str(exc)})
        block = {
            "block_id": BLOCK_ID,
            "block_name": BLOCK_NAME,
            "period": {},
            "compare_period": {},
            "calculation_scope": "selected_brand_pool",
            "target_brand": "",
            "rows": [],
            "notes": notes,
            "warnings": warnings,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        write_json(JSON_OUTPUT, block)
        show_notes = bool(locals().get("args") and args.show_notes)
        write_html(HTML_OUTPUT, render_html(block, show_notes=show_notes))
        print(f"failed to build block 1.1: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
