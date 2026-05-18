"""Build backend query_config files from the local workbench UI payload."""

from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from brand_mapping import DATA_QUERY_VERSION, get_mcp_query_brand, mcp_brand_mapping_for


DEFAULT_OUTPUT_PATH = Path("configs") / "query_config.ui.json"
BLOCK_1_1_DATA_SOURCES = ["新闻", "微博", "微信", "小红书", "短视频", "视频", "论坛", "问答"]
DEFAULT_PLATFORMS = ["抖音", "小红书", "微信视频号", "B站", "知乎"]
SUPPORTED_BRANDS = {"海信"}
SUPPORTED_COMPETITORS = {"美的", "海尔", "TCL"}
DEFAULT_PLATFORM_MAPPINGS = {
    "抖音": {"data_sources": ["抖音app"], "special_soe_metric": None},
    "小红书": {"data_sources": ["小红书"], "special_soe_metric": None},
    "微信视频号": {"data_sources": ["微信视频号"], "special_soe_metric": "love_like"},
    "B站": {"data_sources": ["视频"], "special_soe_metric": None},
    "知乎": {"data_sources": ["问答"], "special_soe_metric": None},
}


class UiConfigPayload(BaseModel):
    template: str | None = "品牌监测周报"
    brand: str = "海信"
    competitors: list[str] = Field(default_factory=lambda: ["美的", "海尔", "TCL"])
    start_date: str = "2025-01-01"
    end_date: str = "2025-01-07"
    compare_start_date: str = "2024-01-01"
    compare_end_date: str = "2024-01-07"
    keywords_raw: str = ""
    filter_words_raw: str = ""


def default_ui_config() -> dict[str, Any]:
    return {
        "template": "品牌监测周报",
        "brand": "海信",
        "competitors": ["美的", "海尔", "TCL"],
        "start_date": "2025-01-01",
        "end_date": "2025-01-07",
        "compare_start_date": "2024-01-01",
        "compare_end_date": "2024-01-07",
        "keywords_raw": "",
        "filter_words_raw": "",
        "fixed_scopes": {
            "block_1_1": ["八个站点全选"],
            "block_1_2": DEFAULT_PLATFORMS,
            "insight_3": DEFAULT_PLATFORMS,
        },
    }


def split_top_level_or(expr: str) -> list[str]:
    """Split an expression on top-level | while preserving nested parentheses."""
    if not expr or not expr.strip():
        return []

    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in expr:
        if char in "（(":
            depth += 1
            current.append(char)
        elif char in "）)":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == "|" and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(char)

    last = "".join(current).strip()
    if last:
        parts.append(last)
    return parts


def clean_competitors(values: list[str]) -> list[str]:
    seen: set[str] = set()
    competitors: list[str] = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            competitors.append(item)
    return competitors


def add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # Keep leap-day periods valid by falling back to Feb 28.
        return value.replace(year=value.year + years, day=28)


def build_period(start_date: str, end_date: str) -> dict[str, str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date cannot be earlier than start_date")
    compare_start = add_years(start, -1)
    compare_end = add_years(end, -1)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "compare_start_date": compare_start.isoformat(),
        "compare_end_date": compare_end.isoformat(),
    }


def build_query_config(payload: UiConfigPayload) -> dict[str, Any]:
    brand = payload.brand.strip() or "海信"
    if brand not in SUPPORTED_BRANDS:
        raise ValueError("本品品牌当前仅支持：海信")
    competitors = clean_competitors(payload.competitors)
    if not competitors:
        raise ValueError("请至少选择一个对标品牌")
    if any(competitor not in SUPPORTED_COMPETITORS for competitor in competitors):
        raise ValueError("对标品牌包含不支持的品牌")
    keywords = split_top_level_or(payload.keywords_raw)
    filter_words = split_top_level_or(payload.filter_words_raw)
    benchmark_brand = competitors[0] if competitors else "美的"
    period = build_period(payload.start_date, payload.end_date)
    configured_brands = [brand, *competitors]

    return {
        "brand": brand,
        "brand_display_name": brand,
        "mcp_brand": get_mcp_query_brand(brand),
        "brand_query_name": get_mcp_query_brand(brand),
        "mcp_brand_mapping": mcp_brand_mapping_for(configured_brands),
        "data_query_version": DATA_QUERY_VERSION,
        "competitors": competitors,
        "benchmark_brand": benchmark_brand,
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "compare_start_date": period["compare_start_date"],
        "compare_end_date": period["compare_end_date"],
        "data_sources": BLOCK_1_1_DATA_SOURCES,
        "platforms": DEFAULT_PLATFORMS,
        "platform_mappings": deepcopy(DEFAULT_PLATFORM_MAPPINGS),
        "keywords": keywords,
        "filter_words": filter_words,
        "keywords_raw": payload.keywords_raw,
        "filter_words_raw": payload.filter_words_raw,
        "insight_3": {
            "posts_per_brand_platform": 30,
            "sort": "titanInteractionCnt",
            "common_filter_words": filter_words,
            "competitor_query_overrides": {
                competitor: {"keywords": [], "filter_words": []}
                for competitor in competitors
            },
        },
    }


def add_brand_query_fields(config: dict[str, Any]) -> dict[str, Any]:
    brand = str(config.get("brand") or "").strip()
    competitors = clean_competitors([str(item) for item in config.get("competitors", [])])
    configured_brands = [brand, *competitors]
    config["brand_display_name"] = brand
    config["mcp_brand"] = get_mcp_query_brand(brand)
    config["brand_query_name"] = get_mcp_query_brand(brand)
    config["mcp_brand_mapping"] = mcp_brand_mapping_for(configured_brands)
    config["data_query_version"] = DATA_QUERY_VERSION
    return config


def upgrade_platform_mappings(config: dict[str, Any]) -> dict[str, Any]:
    mappings = deepcopy(config.get("platform_mappings")) if isinstance(config.get("platform_mappings"), dict) else {}
    for platform, default_mapping in DEFAULT_PLATFORM_MAPPINGS.items():
        current = mappings.get(platform)
        if not isinstance(current, dict):
            mappings[platform] = deepcopy(default_mapping)
            continue

        if platform == "微信视频号":
            current_sources = current.get("data_sources")
            if current_sources is None:
                current_sources = current.get("dataSource")
            if current_sources == ["微信"] or current_sources == "微信":
                current["data_sources"] = ["微信视频号"]
                current.pop("dataSource", None)
            elif "data_sources" not in current and "dataSource" in current:
                current["data_sources"] = current.pop("dataSource")
            current.setdefault("special_soe_metric", "love_like")
        else:
            current.setdefault("data_sources", deepcopy(default_mapping["data_sources"]))
            current.setdefault("special_soe_metric", default_mapping["special_soe_metric"])
        mappings[platform] = current

    config["platform_mappings"] = mappings
    config["platforms"] = list(DEFAULT_PLATFORMS)
    return config


def normalized_config_for_hash(config: dict[str, Any]) -> dict[str, Any]:
    competitors = sorted(str(item).strip() for item in config.get("competitors", []) if str(item).strip())
    return {
        "brand": str(config.get("brand", "")).strip(),
        "mcp_brand": str(config.get("mcp_brand", "")).strip(),
        "mcp_brand_mapping": config.get("mcp_brand_mapping", {}),
        "data_query_version": str(config.get("data_query_version", "")).strip(),
        "platform_mappings": config.get("platform_mappings", {}),
        "competitors": competitors,
        "start_date": config.get("start_date", ""),
        "end_date": config.get("end_date", ""),
        "compare_start_date": config.get("compare_start_date", ""),
        "compare_end_date": config.get("compare_end_date", ""),
        "keywords_raw": str(config.get("keywords_raw", "")).strip(),
        "filter_words_raw": str(config.get("filter_words_raw", "")).strip(),
    }


def config_hash(config: dict[str, Any]) -> str:
    normalized = normalized_config_for_hash(config)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def ensure_query_config_version(config: dict[str, Any]) -> dict[str, Any]:
    upgraded = dict(config)
    add_brand_query_fields(upgraded)
    upgrade_platform_mappings(upgraded)
    upgraded["config_hash"] = config_hash(upgraded)
    return upgraded


def save_query_config(payload: UiConfigPayload, output_path: Path = DEFAULT_OUTPUT_PATH) -> dict[str, Any]:
    try:
        config = build_query_config(payload)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    hash_value = config_hash(config)
    config["config_hash"] = hash_value
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "success": True,
        "query_config_file": str(output_path).replace("\\", "/"),
        "config_hash": hash_value,
        "query_config_preview": config,
    }
