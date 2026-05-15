#!/usr/bin/env python3
"""Build the LLM input package for Insight 2: competitor benchmark.

This script only reads previously generated block JSON files. It does not call
MCP, does not call an LLM, does not read posts, and does not generate HTML.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


BLOCK_1_1_INPUT = Path("outputs") / "blocks" / "block_1_1_brand_overall_table.json"
BLOCK_1_2_INPUT = Path("outputs") / "blocks" / "block_1_2_platform_overall_table.json"
OUTPUT_PATH = Path("outputs") / "insights" / "insight_2_competitor_benchmark_input.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Insight 2 LLM input package from block JSON outputs.")
    parser.add_argument("--block-1-1-file", default=str(BLOCK_1_1_INPUT), help="Path to block 1.1 JSON.")
    parser.add_argument("--block-1-2-file", default=str(BLOCK_1_2_INPUT), help="Path to block 1.2 JSON.")
    parser.add_argument("--output-file", default=str(OUTPUT_PATH), help="Path for the generated insight input JSON.")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Required input file does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Input file is not valid JSON: {path}; {exc}") from exc


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


def format_percent(value: Any) -> str | None:
    number = coerce_number(value)
    if number is None:
        return None
    return f"{number * 100:.2f}%"


def format_change_percent(value: Any) -> str | None:
    number = coerce_number(value)
    if number is None:
        return None
    if number > 0:
        return f"增长{number * 100:.2f}%"
    if number < 0:
        return f"下滑{abs(number) * 100:.2f}%"
    return "持平0.00%"


def format_integer_percent(value: Any) -> str | None:
    number = coerce_number(value)
    if number is None:
        return None
    return f"{number * 100:.0f}%"


def format_metric(row: dict[str, Any], key: str, *, change: bool = False) -> str | None:
    return format_change_percent(row.get(key)) if change else format_percent(row.get(key))


def format_platform_soe(row: dict[str, Any]) -> str | None:
    if row.get("soe_display_type") == "love_like":
        number = coerce_number(row.get("soe"))
        return None if number is None else f"{number:.0f}（爱心赞）"
    return format_percent(row.get("soe"))


def inherit_source_warnings(source_name: str, block: dict[str, Any], warnings: list[dict[str, Any]]) -> None:
    source_warnings = block.get("warnings")
    if not isinstance(source_warnings, list):
        return
    for warning in source_warnings:
        if isinstance(warning, dict):
            warnings.append({"source": source_name, **warning})


def valid_rows(rows: Any, source_name: str, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        warnings.append({"source": source_name, "message": "rows is missing or not a list"})
        return []
    return [row for row in rows if isinstance(row, dict)]


def build_overall_brand_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for row in rows:
        metrics.append(
            {
                "brand": row.get("brand"),
                "sov": format_metric(row, "sov"),
                "sov_yoy": format_metric(row, "sov_yoy", change=True),
                "soe": format_metric(row, "soe"),
                "soe_yoy": format_metric(row, "soe_yoy", change=True),
                "nsr": format_metric(row, "nsr"),
                "nsr_yoy": format_metric(row, "nsr_yoy", change=True),
            }
        )
    return metrics


def rank_rows(rows: list[dict[str, Any]], metric_key: str) -> list[dict[str, Any]]:
    sortable = [row for row in rows if coerce_number(row.get(metric_key)) is not None]
    sortable.sort(key=lambda row: coerce_number(row.get(metric_key)) or 0.0, reverse=True)
    return [
        {
            "rank": index,
            "brand": row.get("brand"),
            "value": format_percent(row.get(metric_key)),
            "raw": row.get(metric_key),
        }
        for index, row in enumerate(sortable, start=1)
    ]


def target_position(rank: list[dict[str, Any]], target_brand: str) -> int | None:
    for item in rank:
        if item.get("brand") == target_brand:
            return item.get("rank")
    return None


def build_target_vs_competitors(rows: list[dict[str, Any]], target_brand: str, warnings: list[dict[str, Any]]) -> dict[str, list[str]]:
    target_row = next((row for row in rows if row.get("brand") == target_brand), None)
    if target_row is None:
        warnings.append({"source": "block_1_1", "target_brand": target_brand, "message": "target brand row is missing"})
        return {
            "sov_higher_than": [],
            "sov_lower_than": [],
            "soe_higher_than": [],
            "soe_lower_than": [],
            "nsr_higher_than": [],
            "nsr_lower_than": [],
        }

    result: dict[str, list[str]] = {}
    for metric in ("sov", "soe", "nsr"):
        target_value = coerce_number(target_row.get(metric))
        higher_than: list[str] = []
        lower_than: list[str] = []
        if target_value is not None:
            for row in rows:
                brand = row.get("brand")
                competitor_value = coerce_number(row.get(metric))
                if brand == target_brand or competitor_value is None:
                    continue
                if target_value > competitor_value:
                    higher_than.append(str(brand))
                elif target_value < competitor_value:
                    lower_than.append(str(brand))
        result[f"{metric}_higher_than"] = higher_than
        result[f"{metric}_lower_than"] = lower_than
    return result


def build_rank_summary(rows: list[dict[str, Any]], target_brand: str) -> dict[str, Any]:
    sov_rank = rank_rows(rows, "sov")
    soe_rank = rank_rows(rows, "soe")
    nsr_rank = rank_rows(rows, "nsr")
    return {
        "sov_rank": sov_rank,
        "soe_rank": soe_rank,
        "nsr_rank": nsr_rank,
        "target_brand_position": {
            "sov_rank": target_position(sov_rank, target_brand),
            "soe_rank": target_position(soe_rank, target_brand),
            "nsr_rank": target_position(nsr_rank, target_brand),
        },
    }


def platform_item(row: dict[str, Any], metric_key: str, value_key: str | None = None) -> dict[str, Any]:
    key = value_key or metric_key
    return {
        "platform": row.get("platform"),
        "value": row.get(key),
        "display": format_integer_percent(row.get(key)) if key.endswith("_vs_benchmark") else format_percent(row.get(key)),
        "soe_display_type": row.get("soe_display_type"),
        "metric": metric_key,
    }


def underperform_platforms(rows: list[dict[str, Any]], key: str, metric_name: str) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        value = coerce_number(row.get(key))
        if value is not None and value < 1:
            result.append(platform_item(row, metric_name, key))
    result.sort(key=lambda item: coerce_number(item["value"]) or 0.0)
    return result


def outperform_platforms(rows: list[dict[str, Any]], key: str, metric_name: str) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        value = coerce_number(row.get(key))
        if value is not None and value > 1:
            result.append(platform_item(row, metric_name, key))
    result.sort(key=lambda item: coerce_number(item["value"]) or 0.0, reverse=True)
    return result


def nsr_risk_platforms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    nsr_rows = [row for row in rows if coerce_number(row.get("nsr")) is not None]
    nsr_rows.sort(key=lambda row: coerce_number(row.get("nsr")) or 0.0)
    for row in nsr_rows[:2]:
        platform = row.get("platform")
        if isinstance(platform, str):
            candidates[platform] = {
                "platform": platform,
                "value": row.get("nsr"),
                "display": format_percent(row.get("nsr")),
                "soe_display_type": row.get("soe_display_type"),
                "reason": "NSR较低",
            }

    for row in rows:
        nsr_yoy = coerce_number(row.get("nsr_yoy"))
        platform = row.get("platform")
        if nsr_yoy is not None and nsr_yoy < 0 and isinstance(platform, str):
            candidates.setdefault(
                platform,
                {
                    "platform": platform,
                    "value": row.get("nsr_yoy"),
                    "display": format_change_percent(row.get("nsr_yoy")),
                    "soe_display_type": row.get("soe_display_type"),
                    "reason": "NSR同比下滑",
                },
            )
    return list(candidates.values())


def special_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if row.get("soe_display_type") == "love_like":
            result.append(
                {
                    "platform": row.get("platform"),
                    "metric": "SOE",
                    "display_type": "love_like",
                    "description": "该平台 SOE 使用爱心赞原始值口径。",
                    "value": row.get("soe"),
                    "display": format_platform_soe(row),
                }
            )
    return result


def build_platform_benchmark_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sov_underperform_platforms": underperform_platforms(rows, "sov_vs_benchmark", "SOV控比MD"),
        "soe_underperform_platforms": underperform_platforms(rows, "soe_vs_benchmark", "SOE控比MD"),
        "soe_outperform_platforms": outperform_platforms(rows, "soe_vs_benchmark", "SOE控比MD"),
        "nsr_risk_platforms": nsr_risk_platforms(rows),
        "special_metrics": special_metrics(rows),
    }


def build_package(block_1_1: dict[str, Any], block_1_2: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    inherit_source_warnings("block_1_1", block_1_1, warnings)
    inherit_source_warnings("block_1_2", block_1_2, warnings)

    brand_rows = valid_rows(block_1_1.get("rows"), "block_1_1", warnings)
    platform_rows = valid_rows(block_1_2.get("rows"), "block_1_2", warnings)
    target_brand = block_1_1.get("target_brand") or block_1_2.get("target_brand") or ""
    benchmark_brand = block_1_2.get("benchmark_brand") or ""
    brand_pool = block_1_2.get("brand_pool")
    if not isinstance(brand_pool, list):
        brand_pool = [row.get("brand") for row in brand_rows if row.get("brand")]

    return {
        "block_id": "insight_2_competitor_benchmark",
        "title": "竞品数据对标",
        "target_brand": target_brand,
        "benchmark_brand": benchmark_brand,
        "period": block_1_1.get("period") or block_1_2.get("period") or {},
        "brand_pool": brand_pool,
        "search_performance_available": False,
        "overall_brand_metrics": build_overall_brand_metrics(brand_rows),
        "rank_summary": build_rank_summary(brand_rows, target_brand),
        "target_vs_competitors": build_target_vs_competitors(brand_rows, target_brand, warnings),
        "platform_benchmark_summary": build_platform_benchmark_summary(platform_rows),
        "warnings": warnings,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        block_1_1 = read_json(Path(args.block_1_1_file))
        block_1_2 = read_json(Path(args.block_1_2_file))
        package = build_package(block_1_1, block_1_2)
        write_json(Path(args.output_file), package)
        print(f"insight input written to: {args.output_file}")
        print(
            json.dumps(
                {
                    "brands": len(package["overall_brand_metrics"]),
                    "platform_sov_underperform": len(package["platform_benchmark_summary"]["sov_underperform_platforms"]),
                    "warnings": len(package["warnings"]),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(f"failed to build insight 2 input: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
