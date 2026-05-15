#!/usr/bin/env python3
"""Generate Insight 2 text with Volcengine Ark Responses API.

This script reads a prepared local input package and an external prompt file,
then asks the LLM to generate only the competitor benchmark section. It does
not call MCP, does not read posts, does not use tools, and does not generate
HTML.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


INPUT_PATH = Path("outputs") / "insights" / "insight_2_competitor_benchmark_input.json"
PROMPT_PATH = Path("prompts") / "insight_2_competitor_benchmark_prompt.txt"
OUTPUT_PATH = Path("outputs") / "insights" / "insight_2_competitor_benchmark.json"
PROMPT_PLACEHOLDER = "{{INPUT_JSON}}"
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ARK_MODEL = "deepseek-v3-2-251201"
SECTION_ID = "insight_2_competitor_benchmark"
SECTION_TITLE = "【竞品数据对标】"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Insight 2 copy JSON via Volcengine Ark.")
    parser.add_argument("--input-file", default=str(INPUT_PATH), help="Path to Insight 2 input JSON.")
    parser.add_argument("--prompt-file", default=str(PROMPT_PATH), help="Path to external prompt txt file.")
    parser.add_argument("--output-file", default=str(OUTPUT_PATH), help="Path for generated insight JSON.")
    return parser.parse_args()


def base_output(warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "section_id": SECTION_ID,
        "section_title": SECTION_TITLE,
        "content": {
            "communication_performance": "",
            "reputation_performance": "",
        },
        "used_data_summary": {
            "overall_brand_metrics_used": False,
            "rank_summary_used": False,
            "target_vs_competitors_used": False,
            "platform_benchmark_summary_used": False,
        },
        "warnings": warnings or [],
    }


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


def build_final_prompt(prompt_template: str, input_data: Any) -> str:
    input_json = json.dumps(input_data, ensure_ascii=False, indent=2)
    return prompt_template.replace(PROMPT_PLACEHOLDER, input_json)


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


def parse_llm_json(response_text: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    if not response_text.strip():
        return None, [{"stage": "parse_llm_response", "message": "LLM response is empty"}]

    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, dict):
            return parsed, warnings
        warnings.append({"stage": "parse_llm_response", "message": "LLM response JSON is not an object"})
    except json.JSONDecodeError:
        extracted = extract_first_json_object(response_text)
        if extracted is not None:
            warnings.append({"stage": "parse_llm_response", "message": "Extracted first JSON object from non-JSON response text"})
            return extracted, warnings
        warnings.append({"stage": "parse_llm_response", "message": "LLM response is not valid JSON"})

    return None, warnings


def clean_plain_text_response(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if cleaned.startswith(SECTION_TITLE):
        cleaned = cleaned[len(SECTION_TITLE) :].strip()
    return cleaned


def split_plain_text_response(text: str) -> tuple[str, str, list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    cleaned = clean_plain_text_response(text)
    if not cleaned:
        warnings.append({"stage": "parse_llm_response", "message": "LLM response is empty"})
        return "", "", warnings

    comm_match = re.search(r"1[\.．、]\s*传播表现[:：]", cleaned)
    rep_match = re.search(r"2[\.．、]\s*口碑表现[:：]", cleaned)
    if comm_match and rep_match and comm_match.start() < rep_match.start():
        communication = cleaned[comm_match.start() : rep_match.start()].strip()
        reputation = cleaned[rep_match.start() :].strip()
        warnings.append({"stage": "parse_llm_response", "message": "LLM response was plain text and has been wrapped into JSON"})
        return communication, reputation, warnings

    warnings.append({"stage": "parse_llm_response", "message": "Unable to split plain text response into communication and reputation parts"})
    return cleaned, "", warnings


def normalize_content(content: Any, warnings: list[dict[str, Any]]) -> dict[str, str]:
    if isinstance(content, dict):
        communication = content.get("communication_performance")
        reputation = content.get("reputation_performance")
    elif isinstance(content, str):
        communication, reputation, split_warnings = split_plain_text_response(content)
        warnings.extend(split_warnings)
    else:
        communication = ""
        reputation = ""
        warnings.append({"stage": "validate_llm_response", "message": "content is missing or not an object"})

    if not isinstance(communication, str):
        communication = ""
        warnings.append({"stage": "validate_llm_response", "message": "communication_performance is missing or not a string"})
    if not isinstance(reputation, str):
        reputation = ""
        warnings.append({"stage": "validate_llm_response", "message": "reputation_performance is missing or not a string"})

    communication = communication.strip()
    reputation = reputation.strip()
    if communication and not communication.startswith("1.传播表现："):
        communication = re.sub(r"^1[\.．、]\s*传播表现[:：]\s*", "", communication)
        communication = f"1.传播表现：{communication.strip()}"
    if reputation and not reputation.startswith("2.口碑表现："):
        reputation = re.sub(r"^2[\.．、]\s*口碑表现[:：]\s*", "", reputation)
        reputation = f"2.口碑表现：{reputation.strip()}"

    if not communication:
        warnings.append({"stage": "validate_llm_response", "message": "communication_performance is empty"})
    if not reputation:
        warnings.append({"stage": "validate_llm_response", "message": "reputation_performance is empty"})

    return {
        "communication_performance": communication,
        "reputation_performance": reputation,
    }


def normalize_generated_output(parsed: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    content = normalize_content(parsed.get("content"), warnings)
    content_ready = bool(content["communication_performance"] and content["reputation_performance"])
    return {
        "section_id": parsed.get("section_id") or SECTION_ID,
        "section_title": parsed.get("section_title") or SECTION_TITLE,
        "content": content,
        "used_data_summary": {
            "overall_brand_metrics_used": content_ready,
            "rank_summary_used": content_ready,
            "target_vs_competitors_used": content_ready,
            "platform_benchmark_summary_used": content_ready,
        },
        "warnings": warnings,
    }


def wrap_plain_text_output(response_text: str, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    communication, reputation, split_warnings = split_plain_text_response(response_text)
    warnings.extend(split_warnings)
    content = normalize_content(
        {
            "communication_performance": communication,
            "reputation_performance": reputation,
        },
        warnings,
    )
    content_ready = bool(content["communication_performance"] and content["reputation_performance"])
    return {
        "section_id": SECTION_ID,
        "section_title": SECTION_TITLE,
        "content": content,
        "used_data_summary": {
            "overall_brand_metrics_used": content_ready,
            "rank_summary_used": content_ready,
            "target_vs_competitors_used": content_ready,
            "platform_benchmark_summary_used": content_ready,
        },
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
        input=[
            {
                "role": "user",
                "content": final_prompt,
            }
        ],
    )
    return extract_response_text(response)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def failure_output(stage: str, message: str, raw_response: str | None = None) -> dict[str, Any]:
    output = base_output([{"stage": stage, "message": message}])
    if raw_response is not None:
        output["raw_response"] = raw_response
    return output


def main() -> int:
    args = parse_args()
    output_path = Path(args.output_file)

    try:
        input_data = read_json(Path(args.input_file))
        prompt_template = read_prompt(Path(args.prompt_file))
        final_prompt = build_final_prompt(prompt_template, input_data)
    except Exception as exc:
        write_json(output_path, failure_output("prepare_prompt", str(exc)))
        print(f"failed to prepare LLM request: {exc}", file=sys.stderr)
        return 1

    try:
        response_text = call_ark_responses(final_prompt)
    except Exception as exc:
        write_json(output_path, failure_output("call_llm", str(exc)))
        print(f"failed to call Ark LLM: {exc}", file=sys.stderr)
        return 1

    parsed, parse_warnings = parse_llm_json(response_text)
    if parsed is None:
        output = wrap_plain_text_output(response_text, parse_warnings)
        if response_text:
            output["raw_response"] = response_text
        write_json(output_path, output)
        print(f"insight text written to: {output_path}")
        print(json.dumps({"warnings": len(output["warnings"]), "wrapped_plain_text": True}, ensure_ascii=False))
        return 0

    output = normalize_generated_output(parsed, parse_warnings)
    write_json(output_path, output)
    print(f"insight text written to: {output_path}")
    print(
        json.dumps(
            {
                "warnings": len(output["warnings"]),
                "communication_empty": not bool(output["content"]["communication_performance"]),
                "reputation_empty": not bool(output["content"]["reputation_performance"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
