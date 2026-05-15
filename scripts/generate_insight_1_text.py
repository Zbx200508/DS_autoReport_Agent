#!/usr/bin/env python3
"""Generate Insight 1 text with Volcengine Ark Responses API.

This script reads a prepared local input package and an external prompt file,
then asks the LLM to generate only the Hisense weekly dynamic section. It does
not call MCP, does not read posts, does not use tools, and does not generate
HTML.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


INPUT_PATH = Path("outputs") / "insights" / "insight_1_hisense_weekly_dynamic_input.json"
PROMPT_PATH = Path("prompts") / "insight_1_hisense_weekly_dynamic_prompt.txt"
OUTPUT_PATH = Path("outputs") / "insights" / "insight_1_hisense_weekly_dynamic.json"
PROMPT_PLACEHOLDER = "{{INPUT_JSON}}"
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ARK_MODEL = "deepseek-v3-2-251201"
SECTION_ID = "insight_1_hisense_weekly_dynamic"
SECTION_TITLE = "【海信本周动态】"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Insight 1 copy JSON via Volcengine Ark.")
    parser.add_argument("--input-file", default=str(INPUT_PATH), help="Path to Insight 1 input JSON.")
    parser.add_argument("--prompt-file", default=str(PROMPT_PATH), help="Path to external prompt txt file.")
    parser.add_argument("--output-file", default=str(OUTPUT_PATH), help="Path for generated insight JSON.")
    return parser.parse_args()


def base_output(warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "section_id": SECTION_ID,
        "section_title": SECTION_TITLE,
        "content": "",
        "used_data_summary": {
            "overall_metrics_used": False,
            "platform_metrics_used": False,
            "auto_highlights_used": False,
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


def normalize_generated_output(parsed: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    content = parsed.get("content")
    if not isinstance(content, str):
        content = ""
        warnings.append({"stage": "validate_llm_response", "message": "content is missing or not a string"})
    elif not content.strip():
        warnings.append({"stage": "validate_llm_response", "message": "content is empty"})

    return {
        "section_id": parsed.get("section_id") or SECTION_ID,
        "section_title": parsed.get("section_title") or SECTION_TITLE,
        "content": content,
        "used_data_summary": {
            "overall_metrics_used": bool(content.strip()),
            "platform_metrics_used": bool(content.strip()),
            "auto_highlights_used": bool(content.strip()),
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
        output = failure_output("parse_llm_response", "LLM response is not valid JSON", raw_response=response_text)
        output["warnings"] = parse_warnings or output["warnings"]
        write_json(output_path, output)
        print("failed to parse Ark LLM response as JSON", file=sys.stderr)
        return 0

    output = normalize_generated_output(parsed, parse_warnings)
    write_json(output_path, output)
    print(f"insight text written to: {output_path}")
    print(json.dumps({"warnings": len(output["warnings"]), "content_empty": not bool(output["content"].strip())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
