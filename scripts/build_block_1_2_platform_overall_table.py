#!/usr/bin/env python3
"""Build Block 1.2 platform-level brand overall performance table.

This script calls only getVolumeInteractionTrend and getNsrTrend. It writes the
standalone JSON/HTML assets for report block 1.2 and does not fetch posts.
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


BLOCK_ID = "block_1_2_platform_overall_table"
BLOCK_NAME = "品牌整体分平台表现"
BLOCKS_DIR = Path("outputs") / "blocks"
JSON_OUTPUT = BLOCKS_DIR / f"{BLOCK_ID}.json"
HTML_OUTPUT = BLOCKS_DIR / f"{BLOCK_ID}.html"
DEFAULT_BENCHMARK_BRAND = "美的"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build report block 1.2 platform overall table.")
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


def normalize_platform_mappings(raw_value: Any, platforms: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_value, dict):
        raise ValueError("query_config.platform_mappings must be a JSON object")

    mappings: dict[str, dict[str, Any]] = {}
    for platform in platforms:
        item = raw_value.get(platform)
        if not isinstance(item, dict):
            raise ValueError(f"query_config.platform_mappings.{platform} must be a JSON object")
        data_sources = ensure_string_list(item.get("data_sources"), f"platform_mappings.{platform}.data_sources")
        if not data_sources:
            raise ValueError(f"query_config.platform_mappings.{platform}.data_sources is required")
        special_soe_metric = item.get("special_soe_metric")
        if special_soe_metric not in (None, "love_like"):
            raise ValueError(f"query_config.platform_mappings.{platform}.special_soe_metric must be null or love_like")
        mappings[platform] = {
            "data_sources": data_sources,
            "special_soe_metric": special_soe_metric,
        }
    return mappings


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

    platforms = ensure_string_list(raw_config.get("platforms"), "platforms")
    if not platforms:
        raise ValueError("query_config.platforms is required for block 1.2")

    benchmark_brand = raw_config.get("benchmark_brand")
    if not isinstance(benchmark_brand, str) or not benchmark_brand:
        benchmark_brand = DEFAULT_BENCHMARK_BRAND
        notes.append(f"benchmark_brand 未配置，默认使用 {DEFAULT_BENCHMARK_BRAND}。")

    return {
        "brand": brand,
        "competitors": ensure_string_list(raw_config.get("competitors"), "competitors"),
        "benchmark_brand": benchmark_brand,
        "start_date": start_date,
        "end_date": end_date,
        "compare_start_date": compare_start_date,
        "compare_end_date": compare_end_date,
        "platforms": platforms,
        "platform_mappings": normalize_platform_mappings(raw_config.get("platform_mappings"), platforms),
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


def build_brand_pool(query_config: dict[str, Any]) -> list[str]:
    brands: list[str] = []
    for brand in [query_config["brand"], *query_config["competitors"]]:
        if brand and brand not in brands:
            brands.append(brand)
    if query_config["benchmark_brand"] not in brands:
        brands.append(query_config["benchmark_brand"])
    return brands


def build_arg0(
    query_config: dict[str, Any],
    platform: str,
    brand: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    mapping = query_config["platform_mappings"][platform]
    return {
        "analysisObject": {"brand": brand},
        "startTimeStr": start_date,
        "endTimeStr": end_date,
        "dataSource": mapping["data_sources"],
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
        for key in ("data", "list", "rows", "result", "trend"):
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


def average_metric(payload: Any, keys: tuple[str, ...]) -> float | None:
    scalar = extract_scalar_metric(payload, keys)
    if scalar is not None:
        return scalar

    records = extract_records(payload)
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
    platform: str,
    brand: str,
    start_date: str,
    end_date: str,
) -> Any:
    return caller.call_tool(tool_name, {"arg0": build_arg0(query_config, platform, brand, start_date, end_date)})


def collect_platform_brand_metrics(
    caller: McpToolCaller,
    query_config: dict[str, Any],
    platform: str,
    brand: str,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "brand": brand,
        "current_volume": 0.0,
        "current_interaction": 0.0,
        "current_nsr": None,
        "compare_volume": 0.0,
        "compare_interaction": 0.0,
        "compare_nsr": None,
    }
    periods = [
        ("current", query_config["start_date"], query_config["end_date"]),
        ("compare", query_config["compare_start_date"], query_config["compare_end_date"]),
    ]

    for period_key, start_date, end_date in periods:
        try:
            payload = call_tool(caller, "getVolumeInteractionTrend", query_config, platform, brand, start_date, end_date)
            records = extract_records(payload)
            if not records:
                warnings.append(
                    {
                        "platform": platform,
                        "brand": brand,
                        "tool": "getVolumeInteractionTrend",
                        "period": period_key,
                        "message": "trend data is empty; volume and interaction treated as 0",
                    }
                )
            metrics[f"{period_key}_volume"] = sum_metric(records, ("volume", "volumeCnt", "cnt")) or 0.0
            metrics[f"{period_key}_interaction"] = (
                sum_metric(records, ("interaction", "interactionCnt", "titanInteractionCnt")) or 0.0
            )
        except Exception as exc:
            warnings.append(
                {
                    "platform": platform,
                    "brand": brand,
                    "tool": "getVolumeInteractionTrend",
                    "period": period_key,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )

        try:
            payload = call_tool(caller, "getNsrTrend", query_config, platform, brand, start_date, end_date)
            nsr = average_metric(payload, ("nsr", "NSR", "value"))
            if nsr is None:
                warnings.append(
                    {
                        "platform": platform,
                        "brand": brand,
                        "tool": "getNsrTrend",
                        "period": period_key,
                        "message": "NSR data is empty",
                    }
                )
            metrics[f"{period_key}_nsr"] = nsr
        except Exception as exc:
            warnings.append(
                {
                    "platform": platform,
                    "brand": brand,
                    "tool": "getNsrTrend",
                    "period": period_key,
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )

    return metrics


def add_mapping_risk_warnings(query_config: dict[str, Any], warnings: list[dict[str, Any]]) -> None:
    source_to_platforms: dict[tuple[str, ...], list[str]] = {}
    for platform in query_config["platforms"]:
        sources = tuple(query_config["platform_mappings"][platform]["data_sources"])
        source_to_platforms.setdefault(sources, []).append(platform)

    for sources, platforms in source_to_platforms.items():
        if len(platforms) > 1:
            warnings.append(
                {
                    "platforms": platforms,
                    "data_sources": list(sources),
                    "message": "多个平台使用相同 MCP dataSource，当前结果存在平台区分口径风险。",
                }
            )


def build_platform_row(
    query_config: dict[str, Any],
    platform: str,
    metrics_by_brand: dict[str, dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    target_brand = query_config["brand"]
    benchmark_brand = query_config["benchmark_brand"]
    target_metrics = metrics_by_brand.get(target_brand, {})
    benchmark_metrics = metrics_by_brand.get(benchmark_brand, {})
    special_soe_metric = query_config["platform_mappings"][platform]["special_soe_metric"]

    current_total_volume = sum(item["current_volume"] for item in metrics_by_brand.values())
    compare_total_volume = sum(item["compare_volume"] for item in metrics_by_brand.values())
    current_total_interaction = sum(item["current_interaction"] for item in metrics_by_brand.values())
    compare_total_interaction = sum(item["compare_interaction"] for item in metrics_by_brand.values())

    target_sov = safe_divide(target_metrics.get("current_volume"), current_total_volume)
    compare_target_sov = safe_divide(target_metrics.get("compare_volume"), compare_total_volume)
    benchmark_sov = safe_divide(benchmark_metrics.get("current_volume"), current_total_volume)

    if special_soe_metric == "love_like":
        target_current_love_like = target_metrics.get("current_interaction")
        target_compare_love_like = target_metrics.get("compare_interaction")
        benchmark_current_love_like = benchmark_metrics.get("current_interaction")
        soe = target_current_love_like
        soe_yoy = safe_divide(
            subtract(target_current_love_like, target_compare_love_like),
            target_compare_love_like,
        )
        soe_vs_benchmark = safe_divide(target_current_love_like, benchmark_current_love_like)
        soe_display_type = "love_like"
        if target_compare_love_like == 0:
            warnings.append(
                {
                    "platform": platform,
                    "metric": "soe_yoy",
                    "message": "视频号对比周期爱心赞为 0，SOE 同比输出 null。",
                }
            )
        if benchmark_current_love_like == 0:
            warnings.append(
                {
                    "platform": platform,
                    "metric": "soe_vs_benchmark",
                    "benchmark_brand": benchmark_brand,
                    "message": "视频号控比品牌当前周期爱心赞为 0，SOE 控比MD输出 null。",
                }
            )
    else:
        target_soe = safe_divide(target_metrics.get("current_interaction"), current_total_interaction)
        compare_target_soe = safe_divide(target_metrics.get("compare_interaction"), compare_total_interaction)
        benchmark_soe = safe_divide(benchmark_metrics.get("current_interaction"), current_total_interaction)
        soe = target_soe
        soe_yoy = subtract(target_soe, compare_target_soe)
        soe_vs_benchmark = safe_divide(target_soe, benchmark_soe)
        soe_display_type = "share"

    return {
        "platform": platform,
        "sov": target_sov,
        "sov_yoy": subtract(target_sov, compare_target_sov),
        "sov_vs_benchmark": safe_divide(target_sov, benchmark_sov),
        "soe": soe,
        "soe_yoy": soe_yoy,
        "soe_vs_benchmark": soe_vs_benchmark,
        "soe_display_type": soe_display_type,
        "nsr": target_metrics.get("current_nsr"),
        "nsr_yoy": subtract(target_metrics.get("current_nsr"), target_metrics.get("compare_nsr")),
    }


def collect_rows(
    caller: McpToolCaller,
    query_config: dict[str, Any],
    brand_pool: list[str],
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for platform in query_config["platforms"]:
        metrics_by_brand = {
            brand: collect_platform_brand_metrics(caller, query_config, platform, brand, warnings)
            for brand in brand_pool
        }
        rows.append(build_platform_row(query_config, platform, metrics_by_brand, warnings))
    return rows


def format_percent(value: float | None) -> str:
    if value is None:
        return "——"
    return f"{value * 100:.2f}%"


def format_integer_percent(value: float | None) -> str:
    if value is None:
        return "——"
    return f"{value * 100:.0f}%"


def format_integer(value: float | None) -> str:
    if value is None:
        return "——"
    return f"{value:.0f}"


def format_soe(row: dict[str, Any]) -> str:
    if row["soe_display_type"] == "love_like":
        return f'{format_integer(row["soe"])}<br><span class="sub">（爱心赞）</span>'
    return format_percent(row["soe"])


def format_soe_vs_benchmark(row: dict[str, Any]) -> str:
    if row["soe_display_type"] == "love_like":
        return f'{format_integer_percent(row["soe_vs_benchmark"])}<br><span class="sub">（爱心赞）</span>'
    return format_integer_percent(row["soe_vs_benchmark"])


def render_html(block: dict[str, Any], show_notes: bool = False) -> str:
    rows_html = []
    for row in block["rows"]:
        cells = [
            html.escape(str(row["platform"])),
            format_percent(row["sov"]),
            format_percent(row["sov_yoy"]),
            format_integer_percent(row["sov_vs_benchmark"]),
            format_soe(row),
            format_percent(row["soe_yoy"]),
            format_soe_vs_benchmark(row),
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
  <title>1.2品牌整体分平台表现</title>
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
      text-align: left;
      width: 14%;
    }}
    .sub {{
      color: #5b6675;
      font-size: 12px;
    }}{notes_style}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>1.2品牌整体分平台表现</h1>
    <table>
      <thead>
        <tr>
          <th>平台</th>
          <th>SOV</th>
          <th>同比</th>
          <th>控比MD</th>
          <th>SOE</th>
          <th>同比</th>
          <th>控比MD</th>
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
        brand_pool = build_brand_pool(query_config)
        add_mapping_risk_warnings(query_config, warnings)

        server_url = os.getenv("MCP_SERVER_URL")
        if not server_url:
            raise McpError("MCP_SERVER_URL is required")
        parsed = urllib.parse.urlparse(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McpError("MCP_SERVER_URL must be an absolute http(s) URL")

        client = McpHttpClient(server_url, load_headers())
        caller = McpToolCaller(client)
        caller.initialize()

        block = {
            "block_id": BLOCK_ID,
            "block_name": BLOCK_NAME,
            "period": {"start_date": query_config["start_date"], "end_date": query_config["end_date"]},
            "compare_period": {
                "start_date": query_config["compare_start_date"],
                "end_date": query_config["compare_end_date"],
            },
            "target_brand": query_config["brand"],
            "benchmark_brand": query_config["benchmark_brand"],
            "brand_pool": brand_pool,
            "rows": collect_rows(caller, query_config, brand_pool, warnings),
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
            "target_brand": "",
            "benchmark_brand": "",
            "brand_pool": [],
            "rows": [],
            "notes": notes,
            "warnings": warnings,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        write_json(JSON_OUTPUT, block)
        show_notes = bool(locals().get("args") and args.show_notes)
        write_html(HTML_OUTPUT, render_html(block, show_notes=show_notes))
        print(f"failed to build block 1.2: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
