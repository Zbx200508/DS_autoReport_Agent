#!/usr/bin/env python3
"""Generate Insight 3 text with per-competitor Ark Responses API calls.

The prepared Insight 3 input can be large, so this script compacts one
competitor at a time, calls the LLM once per competitor, then merges the
summaries into a single Insight 3 JSON output. It does not call MCP, does not
use tools, and does not generate HTML.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


INPUT_PATH = Path("outputs") / "insights" / "insight_3_competitor_weekly_dynamic_input.json"
PROMPT_PATH = Path("prompts") / "insight_3_competitor_weekly_dynamic_prompt.txt"
OUTPUT_PATH = Path("outputs") / "insights" / "insight_3_competitor_weekly_dynamic.json"
DEBUG_COMPACT_INPUT_OUTPUT = Path("outputs") / "debug" / "insight_3_compact_input.json"
DEBUG_FINAL_PROMPT_OUTPUT = Path("outputs") / "debug" / "insight_3_final_prompt_debug.json"
DEBUG_COMPACT_PROFILE_OUTPUT = Path("outputs") / "debug" / "insight_3_compact_input_profile.json"
PROMPT_PLACEHOLDER = "{{INPUT_JSON}}"
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ARK_MODEL = "deepseek-v3-2-251201"
SECTION_ID = "insight_3_competitor_weekly_dynamic"
SECTION_TITLE = "【竞品本周动态】"
CONTENT_PREVIEW_LIMIT = 180
CONFIRMATION_RESPONSES = {
    "好的",
    "可以",
    "明白",
    "收到",
    "没问题",
    "ok",
    "OK",
    "Okay",
    "okay",
}
FORBIDDEN_CREATOR_LEVEL_TERMS = ("头部达人", "中部达人", "尾部达人", "腰部达人")
FORBIDDEN_SECTION_TITLES = ("【海信本周动态】", "【竞品数据对标】", "【竞品本周动态】")
PROCESS_TERMS = ("根据数据包", "根据帖子列表", "根据输入", "输入显示")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Insight 3 copy JSON via Volcengine Ark.")
    parser.add_argument("--input-file", default=str(INPUT_PATH), help="Path to Insight 3 input JSON.")
    parser.add_argument("--prompt-file", default=str(PROMPT_PATH), help="Path to external prompt txt file.")
    parser.add_argument("--output-file", default=str(OUTPUT_PATH), help="Path for generated insight JSON.")
    parser.add_argument(
        "--debug-dump-input",
        action="store_true",
        help="Dump per-competitor compact inputs and prompt debug files without calling LLM.",
    )
    return parser.parse_args()


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Input JSON file does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Input JSON file is not valid JSON: {path}; {exc}") from exc


def read_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Prompt file does not exist: {path}")
    prompt = path.read_text(encoding="utf-8")
    if PROMPT_PLACEHOLDER not in prompt:
        raise ValueError(f"Prompt file must contain placeholder {PROMPT_PLACEHOLDER}")
    return prompt


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def truncate_text(value: Any, limit: int = CONTENT_PREVIEW_LIMIT) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def compact_post(post: dict[str, Any], platform: str | None) -> dict[str, Any]:
    return {
        "rank": post.get("rank"),
        "platform": post.get("platform") or platform,
        "post_title": post.get("post_title"),
        "content_preview": truncate_text(post.get("content_preview")),
        "author": post.get("author"),
        "author_type": post.get("author_type"),
        "creator_level": post.get("creator_level"),
        "interaction_count": post.get("interaction_count"),
    }


def count_author_types(posts: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"PGC": 0, "BGC": 0, "UGC": 0, "unknown": 0}
    for post in posts:
        author_type = post.get("author_type")
        if author_type in counts:
            counts[author_type] += 1
        else:
            counts["unknown"] += 1
    return counts


def all_posts_from_compact(compact_input: dict[str, Any]) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    for platform_group in compact_input.get("platform_groups", []):
        if not isinstance(platform_group, dict):
            continue
        for post in platform_group.get("posts", []):
            if isinstance(post, dict):
                posts.append(post)
    return posts


def build_competitor_compact_input(input_data: dict[str, Any], brand_group: dict[str, Any]) -> dict[str, Any]:
    platform_groups = []
    for platform_group in brand_group.get("platform_groups", []):
        if not isinstance(platform_group, dict):
            continue
        platform = platform_group.get("platform")
        posts = [
            compact_post(post, platform)
            for post in platform_group.get("posts", [])
            if isinstance(post, dict)
        ]
        platform_groups.append(
            {
                "platform": platform,
                "post_count": len(posts),
                "author_type_counts": platform_group.get("author_type_counts") or count_author_types(posts),
                "posts": posts,
            }
        )

    platform_names = [
        group.get("platform")
        for group in platform_groups
        if isinstance(group.get("platform"), str) and group.get("platform")
    ]
    configured_platforms = input_data.get("platforms") if isinstance(input_data.get("platforms"), list) else []

    return {
        "block_id": input_data.get("block_id") or SECTION_ID,
        "title": input_data.get("title") or "竞品本周动态",
        "period": input_data.get("period") or {},
        "competitor_brand": brand_group.get("competitor_brand"),
        "platforms": platform_names or configured_platforms,
        "creator_level_available": bool(input_data.get("creator_level_available")),
        "platform_groups": platform_groups,
        "warnings": input_data.get("warnings", []),
    }


def build_competitor_inputs(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    competitor_inputs = []
    for brand_group in input_data.get("brand_groups", []):
        if isinstance(brand_group, dict):
            competitor_inputs.append(build_competitor_compact_input(input_data, brand_group))
    return competitor_inputs


def build_final_prompt(prompt_template: str, compact_input: dict[str, Any]) -> str:
    input_json = json.dumps(compact_input, ensure_ascii=False, indent=2)
    return prompt_template.replace(PROMPT_PLACEHOLDER, input_json)


def preview_text(value: Any, limit: int = 100) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def build_compact_input_profile(competitor_inputs: list[dict[str, Any]]) -> dict[str, Any]:
    brand_profiles = []
    total_posts = 0
    empty_content_preview_count = 0
    all_posts: list[dict[str, Any]] = []

    for compact_input in competitor_inputs:
        platform_profiles = []
        brand_posts = all_posts_from_compact(compact_input)
        total_posts += len(brand_posts)
        all_posts.extend(brand_posts)

        for platform_group in compact_input.get("platform_groups", []):
            if not isinstance(platform_group, dict):
                continue
            posts = [post for post in platform_group.get("posts", []) if isinstance(post, dict)]
            empty_count = sum(1 for post in posts if not post.get("content_preview"))
            empty_content_preview_count += empty_count
            platform_profiles.append(
                {
                    "platform": platform_group.get("platform"),
                    "post_count": len(posts),
                    "author_type_counts": count_author_types(posts),
                    "empty_content_preview_count": empty_count,
                    "top3_posts": [
                        {
                            "rank": post.get("rank"),
                            "author": post.get("author"),
                            "author_type": post.get("author_type"),
                            "interaction_count": post.get("interaction_count"),
                            "content_preview": preview_text(post.get("content_preview"), 100),
                        }
                        for post in posts[:3]
                    ],
                }
            )

        brand_profiles.append(
            {
                "competitor_brand": compact_input.get("competitor_brand"),
                "platform_count": len(platform_profiles),
                "post_count": len(brand_posts),
                "platforms": platform_profiles,
            }
        )

    return {
        "mode": "per_competitor",
        "brand_groups": brand_profiles,
        "total_posts": total_posts,
        "empty_content_preview_count": empty_content_preview_count,
        "author_type_counts": count_author_types(all_posts),
        "content_preview_max_chars": CONTENT_PREVIEW_LIMIT,
    }


def build_final_prompt_debug(
    *,
    prompt_file: str,
    prompt_template: str,
    competitor_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    competitor_stats = []
    total_posts = 0
    for compact_input in competitor_inputs:
        final_prompt = build_final_prompt(prompt_template, compact_input)
        compact_json = json.dumps(compact_input, ensure_ascii=False, indent=2)
        posts = all_posts_from_compact(compact_input)
        total_posts += len(posts)
        competitor_stats.append(
            {
                "competitor_brand": compact_input.get("competitor_brand"),
                "placeholder_remaining_after_replace": PROMPT_PLACEHOLDER in final_prompt,
                "final_prompt_char_count": len(final_prompt),
                "compact_input_char_count": len(compact_json),
                "platform_count": len(compact_input.get("platform_groups", [])),
                "post_count": len(posts),
                "content_preview_max_chars": CONTENT_PREVIEW_LIMIT,
                "content_preview_empty_count": sum(1 for post in posts if not post.get("content_preview")),
                "author_type_counts": count_author_types(posts),
                "final_prompt_head": final_prompt[:1000],
                "final_prompt_tail": final_prompt[-1000:],
            }
        )

    return {
        "mode": "per_competitor",
        "prompt_file": prompt_file,
        "placeholder_found": PROMPT_PLACEHOLDER in prompt_template,
        "competitor_count": len(competitor_inputs),
        "total_posts": total_posts,
        "competitor_prompt_stats": competitor_stats,
    }


def dump_debug_inputs(input_data: dict[str, Any], prompt_template: str, prompt_file: str) -> None:
    competitor_inputs = build_competitor_inputs(input_data)
    debug_compact_input = {
        "mode": "per_competitor",
        "competitor_inputs": competitor_inputs,
    }
    write_json(DEBUG_COMPACT_INPUT_OUTPUT, debug_compact_input)
    write_json(
        DEBUG_FINAL_PROMPT_OUTPUT,
        build_final_prompt_debug(
            prompt_file=prompt_file,
            prompt_template=prompt_template,
            competitor_inputs=competitor_inputs,
        ),
    )
    write_json(DEBUG_COMPACT_PROFILE_OUTPUT, build_compact_input_profile(competitor_inputs))


def extract_response_text(response: Any) -> str:
    if hasattr(response, "output_text") and response.output_text:
        return response.output_text
    try:
        chunks = []
        for item in response.output:
            if getattr(item, "type", None) == "message":
                for content in item.content:
                    if getattr(content, "type", None) in ("output_text", "text"):
                        chunks.append(content.text)
        if chunks:
            return "\n".join(chunks)
    except Exception:
        pass
    return str(response)


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def is_confirmation_response(text: str) -> bool:
    normalized = strip_code_fence(text).strip().strip("。.!！ \n\r\t")
    return normalized in CONFIRMATION_RESPONSES


def parse_single_competitor_response(
    response_text: str,
    expected_brand: str,
) -> tuple[dict[str, str], list[dict[str, Any]], bool]:
    warnings: list[dict[str, Any]] = []
    cleaned = strip_code_fence(response_text)
    if not cleaned:
        warnings.append(
            {
                "stage": "parse_llm_response",
                "competitor_brand": expected_brand,
                "message": "LLM response is empty",
            }
        )
        return {"competitor_brand": expected_brand, "summary": ""}, warnings, False

    if is_confirmation_response(cleaned):
        warnings.append(
            {
                "stage": "parse_llm_response",
                "competitor_brand": expected_brand,
                "message": "LLM returned a confirmation response instead of a summary",
            }
        )
        return {"competitor_brand": expected_brand, "summary": ""}, warnings, False

    parsed: dict[str, Any] | None = None
    try:
        loaded = json.loads(cleaned)
        if isinstance(loaded, dict):
            parsed = loaded
        else:
            warnings.append(
                {
                    "stage": "parse_llm_response",
                    "competitor_brand": expected_brand,
                    "message": "LLM response JSON is not an object",
                }
            )
    except json.JSONDecodeError:
        parsed = extract_first_json_object(cleaned)
        if parsed is not None:
            warnings.append(
                {
                    "stage": "parse_llm_response",
                    "competitor_brand": expected_brand,
                    "message": "Extracted first JSON object from non-JSON response text",
                }
            )

    if parsed is None:
        warnings.append(
            {
                "stage": "parse_llm_response",
                "competitor_brand": expected_brand,
                "message": "LLM response was plain text and has been wrapped into summary",
            }
        )
        return {"competitor_brand": expected_brand, "summary": cleaned}, warnings, True

    brand = parsed.get("competitor_brand") or expected_brand
    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        warnings.append(
            {
                "stage": "validate_llm_response",
                "competitor_brand": expected_brand,
                "message": "summary is empty",
            }
        )
        summary = ""

    if brand != expected_brand:
        warnings.append(
            {
                "stage": "validate_llm_response",
                "competitor_brand": expected_brand,
                "message": f"response competitor_brand is {brand}, expected {expected_brand}",
            }
        )

    return {"competitor_brand": expected_brand, "summary": summary.strip()}, warnings, bool(summary.strip())


def validate_summary(summary: str, brand: str, warnings: list[dict[str, Any]]) -> None:
    for term in FORBIDDEN_CREATOR_LEVEL_TERMS:
        if term in summary:
            warnings.append(
                {
                    "stage": "validate_llm_response",
                    "competitor_brand": brand,
                    "message": f"summary contains unsupported creator_level term: {term}",
                }
            )
    for title in FORBIDDEN_SECTION_TITLES:
        if title in summary:
            warnings.append(
                {
                    "stage": "validate_llm_response",
                    "competitor_brand": brand,
                    "message": f"summary contains forbidden section title: {title}",
                }
            )
    for term in PROCESS_TERMS:
        if term in summary:
            warnings.append(
                {
                    "stage": "validate_llm_response",
                    "competitor_brand": brand,
                    "message": f"summary contains process wording: {term}",
                }
            )


def used_data_summary(content_items: list[dict[str, Any]]) -> dict[str, bool]:
    has_summary = bool(content_items) and all(item.get("summary") for item in content_items)
    return {
        "brand_groups_used": has_summary,
        "platform_groups_used": has_summary,
        "posts_used": has_summary,
        "author_type_used": has_summary,
        "creator_level_used": False,
    }


def base_output(content_items: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "section_id": SECTION_ID,
        "section_title": SECTION_TITLE,
        "content": content_items,
        "used_data_summary": used_data_summary(content_items),
        "warnings": warnings,
    }


def call_ark_responses(final_prompt: str) -> str:
    api_key = os.getenv("ARK_API_KEY")
    if not api_key:
        raise RuntimeError("ARK_API_KEY is required")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Python package 'openai' is required. Install dependencies from requirements.txt.") from exc

    client = OpenAI(base_url=ARK_BASE_URL, api_key=api_key)
    response = client.responses.create(
        model=ARK_MODEL,
        input=[{"role": "user", "content": final_prompt}],
    )
    return extract_response_text(response)


def empty_content_from_inputs(competitor_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "rank": index,
            "competitor_brand": compact_input.get("competitor_brand") or "",
            "summary": "",
        }
        for index, compact_input in enumerate(competitor_inputs, start=1)
    ]


def generate_per_competitor(
    competitor_inputs: list[dict[str, Any]],
    prompt_template: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    content_items: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for index, compact_input in enumerate(competitor_inputs, start=1):
        brand = compact_input.get("competitor_brand") or ""
        result = {"competitor_brand": brand, "summary": ""}

        try:
            final_prompt = build_final_prompt(prompt_template, compact_input)
            response_text = call_ark_responses(final_prompt)
        except Exception as exc:
            warnings.append(
                {
                    "stage": "call_llm",
                    "competitor_brand": brand,
                    "message": str(exc),
                }
            )
        else:
            result, parse_warnings, _ = parse_single_competitor_response(response_text, brand)
            warnings.extend(parse_warnings)
            validate_summary(result.get("summary", ""), brand, warnings)

        if not result.get("summary"):
            warnings.append(
                {
                    "stage": "validate_llm_response",
                    "competitor_brand": brand,
                    "message": "summary is empty",
                }
            )

        content_items.append(
            {
                "rank": index,
                "competitor_brand": brand,
                "summary": result.get("summary", ""),
            }
        )

    return content_items, warnings


def failure_output(stage: str, message: str) -> dict[str, Any]:
    return base_output([], [{"stage": stage, "message": message}])


def main() -> int:
    args = parse_args()
    output_path = Path(args.output_file)

    try:
        input_data = read_json(Path(args.input_file))
        prompt_template = read_prompt(Path(args.prompt_file))
        competitor_inputs = build_competitor_inputs(input_data)
        if not competitor_inputs:
            raise ValueError("Insight 3 input has no brand_groups to process")
    except Exception as exc:
        write_json(output_path, failure_output("prepare_prompt", str(exc)))
        print(f"failed to prepare LLM request: {exc}", file=sys.stderr)
        return 1

    if args.debug_dump_input:
        dump_debug_inputs(input_data, prompt_template, args.prompt_file)
        print(f"debug compact input written to: {DEBUG_COMPACT_INPUT_OUTPUT}")
        print(f"debug final prompt summary written to: {DEBUG_FINAL_PROMPT_OUTPUT}")
        print(f"debug compact input profile written to: {DEBUG_COMPACT_PROFILE_OUTPUT}")
        return 0

    if not os.getenv("ARK_API_KEY"):
        warnings = [{"stage": "call_llm", "message": "ARK_API_KEY is required"}]
        output = base_output(empty_content_from_inputs(competitor_inputs), warnings)
        write_json(output_path, output)
        print("failed to call Ark LLM: ARK_API_KEY is required", file=sys.stderr)
        return 1

    content_items, warnings = generate_per_competitor(competitor_inputs, prompt_template)
    output = base_output(content_items, warnings)
    write_json(output_path, output)
    print(f"insight text written to: {output_path}")
    print(json.dumps({"items": len(content_items), "warnings": len(warnings)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
