#!/usr/bin/env python3
"""Build the LLM input package for Insight 1: Hisense weekly dynamic.

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
OUTPUT_PATH = Path("outputs") / "insights" / "insight_1_hisense_weekly_dynamic_input.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Insight 1 LLM input package from block JSON outputs.")
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


def format_integer(value: Any) -> str | None:
    number = coerce_number(value)
    if number is None:
        return None
    return f"{number:.0f}"


def metric_value(raw_value: Any, display: str | None = None) -> dict[str, Any]:
    return {
        "raw": raw_value,
        "display": display if display is not None else format_percent(raw_value),
    }


def find_target_row(block_1_1: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    target_brand = block_1_1.get("target_brand")
    rows = block_1_1.get("rows")
    if not isinstance(rows, list):
        warnings.append({"source": "block_1_1", "message": "rows is missing or not a list"})
        return None

    for row in rows:
        if isinstance(row, dict) and row.get("brand") == target_brand:
            return row

    warnings.append(
        {
            "source": "block_1_1",
            "target_brand": target_brand,
            "message": "target brand row is missing",
        }
    )
    return None


def build_overall_metrics(target_row: dict[str, Any] | None) -> dict[str, Any]:
    if target_row is None:
        return {}
    return {
        "brand": target_row.get("brand"),
        "sov": metric_value(target_row.get("sov")),
        "sov_yoy": metric_value(target_row.get("sov_yoy")),
        "soe": metric_value(target_row.get("soe")),
        "soe_yoy": metric_value(target_row.get("soe_yoy")),
        "nsr": metric_value(target_row.get("nsr")),
        "nsr_yoy": metric_value(target_row.get("nsr_yoy")),
    }


def build_platform_metric(row: dict[str, Any]) -> dict[str, Any]:
    soe_display_type = row.get("soe_display_type") or "share"
    if soe_display_type == "love_like":
        soe = metric_value(row.get("soe"), format_integer(row.get("soe")))
    else:
        soe = metric_value(row.get("soe"))

    return {
        "platform": row.get("platform"),
        "sov": metric_value(row.get("sov")),
        "sov_yoy": metric_value(row.get("sov_yoy")),
        "soe": soe,
        "soe_yoy": metric_value(row.get("soe_yoy")),
        "soe_display_type": soe_display_type,
        "nsr": metric_value(row.get("nsr")),
        "nsr_yoy": metric_value(row.get("nsr_yoy")),
    }


def valid_metric_rows(platform_metrics: list[dict[str, Any]], metric_key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in platform_metrics:
        metric = item.get(metric_key)
        if isinstance(metric, dict) and coerce_number(metric.get("raw")) is not None:
            rows.append(item)
    return rows


def highlight_item(item: dict[str, Any], metric_key: str) -> dict[str, Any]:
    metric = item[metric_key]
    return {
        "platform": item.get("platform"),
        "value": metric.get("raw"),
        "display": metric.get("display"),
        "soe_display_type": item.get("soe_display_type"),
    }


def top_platforms(platform_metrics: list[dict[str, Any]], metric_key: str, *, reverse: bool) -> list[dict[str, Any]]:
    rows = valid_metric_rows(platform_metrics, metric_key)
    rows.sort(key=lambda item: coerce_number(item[metric_key]["raw"]) or 0.0, reverse=reverse)
    return [highlight_item(item, metric_key) for item in rows[:2]]


def nsr_risk_platforms(platform_metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}

    for item in top_platforms(platform_metrics, "nsr", reverse=False):
        platform = item.get("platform")
        if isinstance(platform, str):
            candidates[platform] = item | {"reason": "NSR最低"}

    for item in valid_metric_rows(platform_metrics, "nsr_yoy"):
        nsr_yoy = coerce_number(item["nsr_yoy"]["raw"])
        platform = item.get("platform")
        if nsr_yoy is not None and nsr_yoy < 0 and isinstance(platform, str):
            candidates.setdefault(
                platform,
                {
                    "platform": platform,
                    "value": item["nsr_yoy"]["raw"],
                    "display": item["nsr_yoy"]["display"],
                    "soe_display_type": item.get("soe_display_type"),
                    "reason": "NSR同比为负",
                },
            )

    return list(candidates.values())


def special_metrics(platform_metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in platform_metrics:
        if item.get("soe_display_type") == "love_like":
            result.append(
                {
                    "platform": item.get("platform"),
                    "metric": "SOE",
                    "display_type": "love_like",
                    "description": "该平台 SOE 使用爱心赞原始值口径。",
                    "value": item.get("soe", {}).get("raw"),
                    "display": item.get("soe", {}).get("display"),
                }
            )
    return result


def build_auto_highlights(platform_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sov_decline_platforms": top_platforms(platform_metrics, "sov_yoy", reverse=False),
        "sov_growth_platforms": top_platforms(platform_metrics, "sov_yoy", reverse=True),
        "soe_growth_platforms": top_platforms(platform_metrics, "soe_yoy", reverse=True),
        "soe_decline_platforms": top_platforms(platform_metrics, "soe_yoy", reverse=False),
        "nsr_risk_platforms": nsr_risk_platforms(platform_metrics),
        "special_metrics": special_metrics(platform_metrics),
    }


def inherit_source_warnings(source_name: str, block: dict[str, Any], warnings: list[dict[str, Any]]) -> None:
    source_warnings = block.get("warnings")
    if not isinstance(source_warnings, list):
        return
    for warning in source_warnings:
        if isinstance(warning, dict):
            warnings.append({"source": source_name, **warning})


def build_package(block_1_1: dict[str, Any], block_1_2: dict[str, Any]) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    inherit_source_warnings("block_1_1", block_1_1, warnings)
    inherit_source_warnings("block_1_2", block_1_2, warnings)

    target_row = find_target_row(block_1_1, warnings)
    platform_rows = block_1_2.get("rows") if isinstance(block_1_2.get("rows"), list) else []
    if not isinstance(block_1_2.get("rows"), list):
        warnings.append({"source": "block_1_2", "message": "rows is missing or not a list"})

    platform_metrics = [build_platform_metric(row) for row in platform_rows if isinstance(row, dict)]
    target_brand = block_1_1.get("target_brand") or block_1_2.get("target_brand") or "海信"

    return {
        "block_id": "insight_1_hisense_weekly_dynamic",
        "title": "海信本周动态",
        "target_brand": target_brand,
        "period": block_1_1.get("period") or block_1_2.get("period") or {},
        "overall_metrics": build_overall_metrics(target_row),
        "platform_metrics": platform_metrics,
        "auto_highlights": build_auto_highlights(platform_metrics),
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
                    "platforms": len(package["platform_metrics"]),
                    "warnings": len(package["warnings"]),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(f"failed to build insight input: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
