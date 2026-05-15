#!/usr/bin/env python3
"""Build the LLM input package for Insight 3: competitor weekly dynamic.

This script calls only getPosts for competitor brands and configured platforms.
It does not call an LLM, does not generate HTML, and does not generate final
report copy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
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


OUTPUT_PATH = Path("outputs") / "insights" / "insight_3_competitor_weekly_dynamic_input.json"
DEBUG_SAMPLE_OUTPUT = Path("outputs") / "debug" / "insight_3_raw_post_samples.json"
DEBUG_PROFILE_OUTPUT = Path("outputs") / "debug" / "insight_3_raw_field_profile.json"
DEFAULT_POSTS_PER_BRAND_PLATFORM = 30
DEFAULT_SORT = "titanInteractionCnt"
CONTENT_PREVIEW_LIMIT = 500
ASR_PREVIEW_LIMIT = 300
OCR_PREVIEW_LIMIT = 300
VOLUME_TYPES = ("PGC", "BGC", "UGC")
AUTHOR_TYPE_FIELD_KEYWORDS = {
    "author",
    "creator",
    "account",
    "user",
    "source",
    "type",
    "category",
    "contenttype",
    "authortype",
    "creatortype",
    "accounttype",
    "sourcetype",
    "volumetype",
    "mediatype",
    "identity",
    "作者",
    "账号",
    "用户",
    "类型",
    "声量类型",
    "作者类型",
    "账号类型",
    "内容类型",
    "来源类型",
    "身份",
}
CREATOR_LEVEL_FIELD_KEYWORDS = {
    "level",
    "tier",
    "grade",
    "rank",
    "influencer",
    "kol",
    "koc",
    "fan",
    "fans",
    "follower",
    "followers",
    "creatorlevel",
    "influencerlevel",
    "authorlevel",
    "kollevel",
    "达人量级",
    "量级",
    "等级",
    "级别",
    "粉丝",
    "粉丝量",
    "达人",
    "头部",
    "腰部",
    "中部",
    "尾部",
}
AUTHOR_TYPE_VALUE_KEYWORDS = {"PGC", "BGC", "UGC", "KOL", "KOC", "官方", "品牌", "达人", "媒体", "用户"}
CREATOR_LEVEL_VALUE_KEYWORDS = {"头部", "腰部", "中部", "尾部", "达人", "KOL", "KOC"}
PROFILE_KEYWORDS = [
    "author",
    "creator",
    "type",
    "level",
    "tier",
    "kol",
    "koc",
    "ugc",
    "pgc",
    "bgc",
    "达人",
    "作者",
    "账号",
    "声量",
    "类型",
    "量级",
]
OUTPUT_FIELD_PARAM_KEYWORDS = {
    "fields",
    "outputfields",
    "selectedfields",
    "columns",
    "returnfields",
    "includefields",
}
FILTER_PARAM_KEYWORDS = {
    "authortype",
    "creatortype",
    "accounttype",
    "volumetype",
    "creatorlevel",
    "influencerlevel",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Insight 3 competitor weekly dynamic input package.")
    parser.add_argument("--query-config-file", default="configs/query_config.local.json", help="Path to query_config JSON file.")
    parser.add_argument("--query-config-json", help="Inline query_config JSON object.")
    parser.add_argument("--output-file", default=str(OUTPUT_PATH), help="Path for the generated insight input JSON.")
    parser.add_argument("--debug-raw-fields", action="store_true", help="Write raw getPosts samples and field profile debug files.")
    return parser.parse_args()


def read_query_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.query_config_json:
        raw = json.loads(args.query_config_json)
    else:
        path = Path(args.query_config_file)
        if not path.exists():
            raise FileNotFoundError(f"query_config file does not exist: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("query_config must be a JSON object")
    return raw


def normalize_platform_mappings(raw_value: Any, platforms: list[str], warnings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_value, dict):
        raise ValueError("query_config.platform_mappings must be a JSON object")

    mappings: dict[str, dict[str, Any]] = {}
    for platform in platforms:
        item = raw_value.get(platform)
        if not isinstance(item, dict):
            warnings.append({"platform": platform, "message": "platform mapping is missing; platform skipped"})
            continue
        data_sources = ensure_string_list(item.get("data_sources"), f"platform_mappings.{platform}.data_sources")
        if not data_sources:
            warnings.append({"platform": platform, "message": "platform mapping data_sources is empty; platform skipped"})
            continue
        mappings[platform] = {"data_sources": data_sources}
        if "微信视频号" in platform and data_sources != ["微信视频号"]:
            warnings.append(
                {
                    "platform": platform,
                    "data_sources": data_sources,
                    "message": "微信视频号使用当前 MCP dataSource 映射，存在口径风险。",
                }
            )
        if platform in {"B站", "微信视频号"} and data_sources == ["视频"]:
            warnings.append(
                {
                    "platform": platform,
                    "data_sources": data_sources,
                    "message": "该平台与其他视频平台可能共用 MCP dataSource，存在平台区分口径风险。",
                }
            )
    return mappings


def normalize_query_config(raw: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    start_date = raw.get("start_date")
    end_date = raw.get("end_date")
    if not isinstance(start_date, str) or not start_date:
        raise ValueError("query_config.start_date is required")
    if not isinstance(end_date, str) or not end_date:
        raise ValueError("query_config.end_date is required")

    competitors = ensure_string_list(raw.get("competitors"), "competitors")
    if len(competitors) != 3:
        warnings.append({"field": "competitors", "count": len(competitors), "message": "competitors count is not 3"})

    platforms = ensure_string_list(raw.get("insight_3_platforms"), "insight_3_platforms")
    if not platforms:
        platforms = ensure_string_list(raw.get("platforms"), "platforms")
    if not platforms:
        raise ValueError("query_config.platforms or query_config.insight_3_platforms is required")
    if len(platforms) != 5:
        warnings.append({"field": "platforms", "count": len(platforms), "message": "platforms count is not 5"})

    insight_3 = raw.get("insight_3")
    if not isinstance(insight_3, dict):
        insight_3 = {}
    posts_per_group = insight_3.get("posts_per_brand_platform", DEFAULT_POSTS_PER_BRAND_PLATFORM)
    if not isinstance(posts_per_group, int) or posts_per_group <= 0:
        posts_per_group = DEFAULT_POSTS_PER_BRAND_PLATFORM
        warnings.append({"field": "insight_3.posts_per_brand_platform", "message": "invalid value; using default 30"})
    sort = insight_3.get("sort", DEFAULT_SORT)
    if not isinstance(sort, str) or not sort:
        sort = DEFAULT_SORT

    overrides = insight_3.get("competitor_query_overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    common_filter_words = ensure_string_list(insight_3.get("common_filter_words"), "insight_3.common_filter_words")

    return {
        "brand": raw.get("brand"),
        "competitors": competitors,
        "start_date": start_date,
        "end_date": end_date,
        "platforms": platforms,
        "platform_mappings": normalize_platform_mappings(raw.get("platform_mappings"), platforms, warnings),
        "posts_per_brand_platform": posts_per_group,
        "sort": sort,
        "competitor_query_overrides": overrides,
        "common_filter_words": common_filter_words,
    }


def query_keywords(query_config: dict[str, Any], competitor: str) -> list[str]:
    override = query_config["competitor_query_overrides"].get(competitor)
    if isinstance(override, dict):
        return ensure_string_list(override.get("keywords"), f"competitor_query_overrides.{competitor}.keywords")
    return []


def query_filter_words(query_config: dict[str, Any], competitor: str) -> list[str]:
    override = query_config["competitor_query_overrides"].get(competitor)
    if isinstance(override, dict) and "filter_words" in override:
        return ensure_string_list(override.get("filter_words"), f"competitor_query_overrides.{competitor}.filter_words")
    return list(query_config["common_filter_words"])


def build_arg0(query_config: dict[str, Any], competitor: str, platform: str, volume_type: str | None = None) -> dict[str, Any]:
    arg0 = {
        "sort": query_config["sort"],
        "count": query_config["posts_per_brand_platform"],
        "analysisObject": {"brand": competitor},
        "startTimeStr": query_config["start_date"],
        "endTimeStr": query_config["end_date"],
        "dataSource": query_config["platform_mappings"][platform]["data_sources"],
        "keywords": query_keywords(query_config, competitor),
        "filterWords": query_filter_words(query_config, competitor),
    }
    if volume_type is not None:
        arg0["volumeType"] = [volume_type]
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


def compact_sample_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = clean_text(value)
    return truncate_text(text, 120)


def key_matches_keywords(key: str, keywords: set[str]) -> bool:
    lowered = key.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def value_matches_keywords(values: list[str], keywords: set[str]) -> bool:
    pattern = re.compile("|".join(re.escape(keyword) for keyword in keywords), re.IGNORECASE)
    return any(pattern.search(value) for value in values)


def profile_raw_posts(raw_posts: list[dict[str, Any]]) -> dict[str, Any]:
    key_presence: dict[str, dict[str, Any]] = {}
    candidate_keys_by_keyword: dict[str, list[str]] = {keyword: [] for keyword in PROFILE_KEYWORDS}

    for post in raw_posts:
        for key, value in post.items():
            stats = key_presence.setdefault(key, {"count": 0, "non_null_count": 0, "sample_values": []})
            stats["count"] += 1
            if value not in (None, ""):
                stats["non_null_count"] += 1
                sample = compact_sample_value(value)
                if sample and sample not in stats["sample_values"] and len(stats["sample_values"]) < 5:
                    stats["sample_values"].append(sample)

    all_keys = sorted(key_presence)
    for keyword in PROFILE_KEYWORDS:
        lowered_keyword = keyword.lower()
        candidate_keys_by_keyword[keyword] = [
            key for key in all_keys if lowered_keyword in key.lower()
        ]

    candidate_author_type_keys = []
    candidate_creator_level_keys = []
    for key in all_keys:
        sample_values = key_presence[key]["sample_values"]
        if key_matches_keywords(key, AUTHOR_TYPE_FIELD_KEYWORDS) or value_matches_keywords(sample_values, AUTHOR_TYPE_VALUE_KEYWORDS):
            candidate_author_type_keys.append(key)
        if key_matches_keywords(key, CREATOR_LEVEL_FIELD_KEYWORDS) or value_matches_keywords(sample_values, CREATOR_LEVEL_VALUE_KEYWORDS):
            candidate_creator_level_keys.append(key)

    return {
        "total_raw_posts": len(raw_posts),
        "all_keys": all_keys,
        "key_presence": key_presence,
        "candidate_author_type_keys": sorted(set(candidate_author_type_keys)),
        "candidate_creator_level_keys": sorted(set(candidate_creator_level_keys)),
        "candidate_keys_by_keyword": candidate_keys_by_keyword,
    }


def flatten_schema_keys(schema: Any, prefix: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, value in properties.items():
                path = f"{prefix}.{key}" if prefix else key
                keys.append(path)
                keys.extend(flatten_schema_keys(value, path))
        for key in ("items", "additionalProperties"):
            if key in schema:
                keys.extend(flatten_schema_keys(schema[key], prefix))
    return keys


def getposts_schema_summary(tools_path: Path = Path("outputs") / "mcp_tools.json") -> dict[str, Any]:
    summary = {
        "tool_found": False,
        "input_schema_keys": [],
        "possible_output_field_params": [],
        "possible_filter_params": [],
    }
    if not tools_path.exists():
        summary["message"] = f"{tools_path} does not exist"
        return summary

    try:
        payload = json.loads(tools_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        summary["message"] = f"{tools_path} is not valid JSON: {exc}"
        return summary

    tools = payload
    if isinstance(payload, dict):
        for key in ("tools", "data", "result"):
            if isinstance(payload.get(key), list):
                tools = payload[key]
                break
    if not isinstance(tools, list):
        summary["message"] = "mcp_tools payload does not contain a tools list"
        return summary

    tool = next((item for item in tools if isinstance(item, dict) and item.get("name") == "getPosts"), None)
    if tool is None:
        return summary

    summary["tool_found"] = True
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    keys = sorted(set(flatten_schema_keys(schema)))
    summary["input_schema_keys"] = keys
    for key in keys:
        normalized = key.split(".")[-1].lower()
        if normalized in OUTPUT_FIELD_PARAM_KEYWORDS:
            summary["possible_output_field_params"].append(key)
        if normalized in FILTER_PARAM_KEYWORDS:
            summary["possible_filter_params"].append(key)
    return summary


def write_debug_raw_fields(debug_context: dict[str, Any]) -> None:
    samples = {"samples": debug_context["samples"]}
    profile = profile_raw_posts(debug_context["raw_posts"])
    profile["getPosts_schema_summary"] = getposts_schema_summary()
    write_json(DEBUG_SAMPLE_OUTPUT, samples)
    write_json(DEBUG_PROFILE_OUTPUT, profile)


def first_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record and record.get(key) not in (None, ""):
            return record.get(key)
    return None


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


def truncate_text(value: str, limit: int) -> str:
    value = clean_text(value)
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3].rstrip() + "..."


def build_content_preview(record: dict[str, Any], content: str) -> str:
    parts: list[str] = []
    if content:
        parts.append(content)

    if len(" ".join(parts)) < CONTENT_PREVIEW_LIMIT:
        asr = truncate_text(clean_text(record.get("audioAsrContent")), ASR_PREVIEW_LIMIT)
        if asr:
            parts.append(f"ASR: {asr}")

    if len(" ".join(parts)) < CONTENT_PREVIEW_LIMIT:
        ocr = truncate_text(clean_text(record.get("coverOcrContent")), OCR_PREVIEW_LIMIT)
        if ocr:
            parts.append(f"OCR: {ocr}")

    return truncate_text("\n".join(parts), CONTENT_PREVIEW_LIMIT)


def normalize_post(
    record: dict[str, Any],
    *,
    rank: int,
    competitor: str,
    platform: str,
    data_sources: list[str],
    author_type: str | None = None,
) -> dict[str, Any]:
    title = clean_text(first_value(record, ("title", "postTitle", "标题")))
    content = clean_text(first_value(record, ("content", "postContent", "正文")))
    interaction_count = coerce_int(first_value(record, ("interactionCnt", "interaction", "互动量")))
    return {
        "rank": rank,
        "competitor_brand": competitor,
        "platform": platform,
        "mcp_data_sources": data_sources,
        "publish_time": clean_text(first_value(record, ("publishTime", "publish_time", "发布时间"))),
        "post_title": title,
        "post_content": content,
        "content_preview": build_content_preview(record, content),
        "author": clean_text(first_value(record, ("author", "authorName", "作者"))),
        "author_type": author_type or first_value(record, ("authorType", "author_type", "creatorType", "contentAuthorType")),
        "creator_level": first_value(record, ("creatorLevel", "influencerLevel", "达人量级", "authorLevel")),
        "interaction_count": interaction_count,
        "like_count": coerce_int(first_value(record, ("likeCnt", "like_count", "点赞量"))),
        "comment_count": coerce_int(first_value(record, ("reviewCnt", "comment_count", "评论量"))),
        "repost_count": coerce_int(first_value(record, ("repostsCnt", "repost_count", "转发量"))),
        "collection_count": coerce_int(first_value(record, ("collectionCnt", "collection_count", "收藏量"))),
        "url": clean_text(first_value(record, ("url", "postUrl", "链接"))),
    }


def call_get_posts(
    caller: McpToolCaller,
    query_config: dict[str, Any],
    competitor: str,
    platform: str,
    volume_type: str,
) -> list[dict[str, Any]]:
    payload = caller.call_tool("getPosts", {"arg0": build_arg0(query_config, competitor, platform, volume_type)})
    posts = extract_posts(payload)
    posts.sort(key=lambda item: coerce_int(first_value(item, ("interactionCnt", "interaction", "互动量"))), reverse=True)
    return posts[: query_config["posts_per_brand_platform"]]


def post_dedupe_key(post: dict[str, Any]) -> tuple[str, str]:
    url = clean_text(post.get("url"))
    if url:
        return ("url", url)
    fallback = "|".join(
        [
            clean_text(post.get("author")),
            clean_text(post.get("publish_time")),
            clean_text(post.get("post_content")) or clean_text(post.get("content_preview")),
        ]
    )
    return ("fallback", fallback)


def completeness_score(post: dict[str, Any]) -> int:
    return sum(1 for value in post.values() if value not in (None, "", [], {}))


def merge_posts(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    existing_interaction = coerce_int(existing.get("interaction_count"))
    candidate_interaction = coerce_int(candidate.get("interaction_count"))
    if candidate_interaction > existing_interaction:
        winner = dict(candidate)
        if not winner.get("author_type"):
            winner["author_type"] = existing.get("author_type")
        return winner
    if candidate_interaction == existing_interaction and completeness_score(candidate) > completeness_score(existing):
        winner = dict(candidate)
        if not winner.get("author_type"):
            winner["author_type"] = existing.get("author_type")
        return winner
    return existing


def finalize_posts(posts: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for post in posts:
        key = post_dedupe_key(post)
        if key[1] == "":
            key = ("object", str(id(post)))
        if key not in deduped:
            deduped[key] = post
        else:
            deduped[key] = merge_posts(deduped[key], post)

    final_posts = sorted(deduped.values(), key=lambda item: coerce_int(item.get("interaction_count")), reverse=True)[:limit]
    for index, post in enumerate(final_posts, start=1):
        post["rank"] = index
    return final_posts


def author_type_counts(posts: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"PGC": 0, "BGC": 0, "UGC": 0, "unknown": 0}
    for post in posts:
        author_type = post.get("author_type")
        if author_type in counts:
            counts[author_type] += 1
        else:
            counts["unknown"] += 1
    return counts


def collect_platform_posts(
    caller: McpToolCaller,
    query_config: dict[str, Any],
    competitor: str,
    platform: str,
    warnings: list[dict[str, Any]],
    debug_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data_sources = query_config["platform_mappings"][platform]["data_sources"]
    posts: list[dict[str, Any]] = []
    raw_counts_by_author_type: dict[str, int] = {}
    for volume_type in VOLUME_TYPES:
        try:
            raw_posts = call_get_posts(caller, query_config, competitor, platform, volume_type)
            raw_counts_by_author_type[volume_type] = len(raw_posts)
            if debug_context is not None:
                debug_context["raw_posts"].extend(raw_posts)
                for sample_rank, raw_post in enumerate(raw_posts[:2], start=1):
                    debug_context["samples"].append(
                        {
                            "competitor_brand": competitor,
                            "platform": platform,
                            "mcp_data_sources": data_sources,
                            "volume_type": volume_type,
                            "sample_rank": sample_rank,
                            "raw_keys": list(raw_post.keys()),
                            "raw_post": raw_post,
                        }
                    )
            posts.extend(
                normalize_post(
                    record,
                    rank=0,
                    competitor=competitor,
                    platform=platform,
                    data_sources=data_sources,
                    author_type=volume_type,
                )
                for record in raw_posts
            )
        except Exception as exc:
            raw_counts_by_author_type[volume_type] = 0
            warnings.append(
                {
                    "competitor_brand": competitor,
                    "platform": platform,
                    "volume_type": volume_type,
                    "tool": "getPosts",
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                }
            )

    posts = finalize_posts(posts, query_config["posts_per_brand_platform"])
    if len(posts) < query_config["posts_per_brand_platform"]:
        warnings.append(
            {
                "competitor_brand": competitor,
                "platform": platform,
                "expected_count": query_config["posts_per_brand_platform"],
                "actual_count": len(posts),
                "message": "merged volumeType posts fewer than requested",
            }
        )
    if not posts and all(count == 0 for count in raw_counts_by_author_type.values()):
        warnings.append(
            {
                "competitor_brand": competitor,
                "platform": platform,
                "message": "all volumeType buckets returned empty or failed",
            }
        )

    return {
        "platform": platform,
        "mcp_data_sources": data_sources,
        "post_count": len(posts),
        "raw_counts_by_author_type": raw_counts_by_author_type,
        "author_type_counts": author_type_counts(posts),
        "posts": posts,
    }


def build_package(
    caller: McpToolCaller,
    query_config: dict[str, Any],
    warnings: list[dict[str, Any]],
    debug_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    brand_groups = []
    total_actual_posts = 0
    global_author_type_stats = {"PGC": 0, "BGC": 0, "UGC": 0, "unknown": 0}
    for competitor in query_config["competitors"]:
        platform_groups = []
        for platform in query_config["platforms"]:
            if platform not in query_config["platform_mappings"]:
                continue
            platform_group = collect_platform_posts(caller, query_config, competitor, platform, warnings, debug_context)
            total_actual_posts += platform_group["post_count"]
            for key, value in platform_group["author_type_counts"].items():
                global_author_type_stats[key] = global_author_type_stats.get(key, 0) + value
            platform_groups.append(platform_group)
        brand_groups.append({"competitor_brand": competitor, "platform_groups": platform_groups})

    total_expected_posts = (
        len(query_config["competitors"])
        * len([platform for platform in query_config["platforms"] if platform in query_config["platform_mappings"]])
        * query_config["posts_per_brand_platform"]
    )
    return {
        "block_id": "insight_3_competitor_weekly_dynamic",
        "title": "竞品本周动态",
        "period": {
            "start_date": query_config["start_date"],
            "end_date": query_config["end_date"],
        },
        "competitors": query_config["competitors"],
        "platforms": query_config["platforms"],
        "posts_per_brand_platform": query_config["posts_per_brand_platform"],
        "total_expected_posts": total_expected_posts,
        "total_actual_posts": total_actual_posts,
        "author_type_stats": global_author_type_stats,
        "creator_level_available": False,
        "field_notes": {
            "author_type": "author_type 由 getPosts volumeType=PGC/BGC/UGC 分桶调用补全，不从作者名、内容或互动量推断。",
            "creator_level": "若 MCP 未返回达人量级，则为 null，不得强行推断。",
            "platform_mapping": "平台展示名与 MCP dataSource 可能存在映射口径差异。",
        },
        "brand_groups": brand_groups,
        "warnings": warnings,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    warnings: list[dict[str, Any]] = []
    args = parse_args()
    output_path = Path(args.output_file)
    debug_context: dict[str, Any] | None = {"samples": [], "raw_posts": []} if args.debug_raw_fields else None
    try:
        raw_config = read_query_config(args)
        query_config = normalize_query_config(raw_config, warnings)
        warnings.append(
            {
                "stage": "build_insight_3_input",
                "message": "creator_level is currently unavailable from MCP getPosts and remains null.",
            }
        )

        server_url = os.getenv("MCP_SERVER_URL")
        if not server_url:
            raise McpError("MCP_SERVER_URL is required")
        parsed = urllib.parse.urlparse(server_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise McpError("MCP_SERVER_URL must be an absolute http(s) URL")

        client = McpHttpClient(server_url, load_headers())
        caller = McpToolCaller(client)
        caller.initialize()
        package = build_package(caller, query_config, warnings, debug_context)
        write_json(output_path, package)
        if debug_context is not None:
            write_debug_raw_fields(debug_context)
        print(f"insight input written to: {output_path}")
        print(
            json.dumps(
                {
                    "brands": len(package["brand_groups"]),
                    "total_actual_posts": package["total_actual_posts"],
                    "warnings": len(package["warnings"]),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        warnings.append({"stage": "build_insight_3_input", "error_type": exc.__class__.__name__, "message": str(exc)})
        fallback = {
            "block_id": "insight_3_competitor_weekly_dynamic",
            "title": "竞品本周动态",
            "period": {},
            "competitors": [],
            "platforms": [],
            "posts_per_brand_platform": DEFAULT_POSTS_PER_BRAND_PLATFORM,
            "total_expected_posts": 0,
            "total_actual_posts": 0,
            "author_type_stats": {"PGC": 0, "BGC": 0, "UGC": 0, "unknown": 0},
            "creator_level_available": False,
            "field_notes": {
                "author_type": "author_type 由 getPosts volumeType=PGC/BGC/UGC 分桶调用补全，不从作者名、内容或互动量推断。",
                "creator_level": "若 MCP 未返回达人量级，则为 null，不得强行推断。",
                "platform_mapping": "平台展示名与 MCP dataSource 可能存在映射口径差异。",
            },
            "brand_groups": [],
            "warnings": warnings,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        write_json(output_path, fallback)
        if debug_context is not None:
            write_debug_raw_fields(debug_context)
        print(f"failed to build insight 3 input: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
