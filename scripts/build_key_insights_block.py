#!/usr/bin/env python3
"""Merge generated insight JSON files into one key insights HTML block.

This script only reads existing insight outputs and renders a standalone HTML
module. It does not call MCP, does not call an LLM, and does not modify the
source insight JSON files.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any


INSIGHT_1_PATH = Path("outputs") / "insights" / "insight_1_hisense_weekly_dynamic.json"
INSIGHT_2_PATH = Path("outputs") / "insights" / "insight_2_competitor_benchmark.json"
INSIGHT_3_PATH = Path("outputs") / "insights" / "insight_3_competitor_weekly_dynamic.json"
OUTPUT_JSON_PATH = Path("outputs") / "blocks" / "key_insights_block.json"
OUTPUT_HTML_PATH = Path("outputs") / "blocks" / "key_insights_block.html"

BLOCK_ID = "key_insights_block"
BLOCK_TITLE = "本期重点洞察"

SECTION_CONFIGS = [
    {
        "key": "insight_1",
        "path": INSIGHT_1_PATH,
        "section_id": "insight_1_hisense_weekly_dynamic",
        "section_title": "【海信本周动态】",
    },
    {
        "key": "insight_2",
        "path": INSIGHT_2_PATH,
        "section_id": "insight_2_competitor_benchmark",
        "section_title": "【竞品数据对标】",
    },
    {
        "key": "insight_3",
        "path": INSIGHT_3_PATH,
        "section_id": "insight_3_competitor_weekly_dynamic",
        "section_title": "【竞品本周动态】",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the merged key insights HTML block.")
    parser.add_argument("--insight-1-file", default=str(INSIGHT_1_PATH), help="Path to Insight 1 JSON.")
    parser.add_argument("--insight-2-file", default=str(INSIGHT_2_PATH), help="Path to Insight 2 JSON.")
    parser.add_argument("--insight-3-file", default=str(INSIGHT_3_PATH), help="Path to Insight 3 JSON.")
    parser.add_argument("--output-json", default=str(OUTPUT_JSON_PATH), help="Path for merged block JSON.")
    parser.add_argument("--output-html", default=str(OUTPUT_HTML_PATH), help="Path for merged block HTML.")
    return parser.parse_args()


def path_for_config(config: dict[str, Any], args: argparse.Namespace) -> Path:
    if config["key"] == "insight_1":
        return Path(args.insight_1_file)
    if config["key"] == "insight_2":
        return Path(args.insight_2_file)
    if config["key"] == "insight_3":
        return Path(args.insight_3_file)
    return Path(config["path"])


def read_json_file(path: Path, section_id: str, warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not path.exists():
        warnings.append(
            {
                "stage": "read_input",
                "section_id": section_id,
                "message": f"Insight JSON file does not exist: {path}",
            }
        )
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(
            {
                "stage": "read_input",
                "section_id": section_id,
                "message": f"Failed to read or parse Insight JSON: {path}; {exc}",
            }
        )
        return None
    if not isinstance(data, dict):
        warnings.append(
            {
                "stage": "read_input",
                "section_id": section_id,
                "message": f"Insight JSON root is not an object: {path}",
            }
        )
        return None
    return data


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def remove_repeated_titles(text: str, titles: list[str]) -> str:
    cleaned = text.strip()
    changed = True
    while changed:
        changed = False
        for title in titles:
            if title and cleaned.startswith(title):
                cleaned = cleaned[len(title) :].strip()
                changed = True
    return cleaned


def normalize_insight_1(
    data: dict[str, Any],
    section_title: str,
    warnings: list[dict[str, Any]],
) -> str:
    content = clean_text(data.get("content"))
    source_title = clean_text(data.get("section_title"))
    content = remove_repeated_titles(content, [section_title, source_title])
    if not content:
        warnings.append(
            {
                "stage": "normalize_content",
                "section_id": "insight_1_hisense_weekly_dynamic",
                "message": "content is empty",
            }
        )
    return content


def normalize_insight_2(data: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, str]:
    content = data.get("content")
    if not isinstance(content, dict):
        warnings.append(
            {
                "stage": "normalize_content",
                "section_id": "insight_2_competitor_benchmark",
                "message": "content is missing or not an object",
            }
        )
        return {}

    normalized: dict[str, str] = {}
    for key in ("communication_performance", "reputation_performance"):
        value = clean_text(content.get(key))
        if value:
            normalized[key] = value
        else:
            warnings.append(
                {
                    "stage": "normalize_content",
                    "section_id": "insight_2_competitor_benchmark",
                    "message": f"{key} is missing or empty",
                }
            )
    return normalized


def has_competitor_prefix(summary: str, rank: int, brand: str) -> bool:
    if not summary:
        return False
    escaped_brand = re.escape(brand)
    pattern = rf"^\s*{rank}\s*[\.\u3002、．]\s*{escaped_brand}\s*[:：]"
    return re.match(pattern, summary) is not None


def strip_leading_section_title(summary: str) -> str:
    return remove_repeated_titles(summary, ["【竞品本周动态】"])


def normalize_insight_3(data: dict[str, Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = data.get("content")
    if not isinstance(content, list):
        warnings.append(
            {
                "stage": "normalize_content",
                "section_id": "insight_3_competitor_weekly_dynamic",
                "message": "content is missing or not an array",
            }
        )
        return []

    normalized = []
    for index, item in enumerate(content, start=1):
        if not isinstance(item, dict):
            warnings.append(
                {
                    "stage": "normalize_content",
                    "section_id": "insight_3_competitor_weekly_dynamic",
                    "message": f"content[{index}] is not an object",
                }
            )
            continue

        rank = item.get("rank") if isinstance(item.get("rank"), int) else index
        brand = clean_text(item.get("competitor_brand"))
        summary = strip_leading_section_title(clean_text(item.get("summary")))
        if not summary:
            warnings.append(
                {
                    "stage": "normalize_content",
                    "section_id": "insight_3_competitor_weekly_dynamic",
                    "message": f"summary is empty for content[{index}]",
                }
            )
            continue
        if not brand:
            warnings.append(
                {
                    "stage": "normalize_content",
                    "section_id": "insight_3_competitor_weekly_dynamic",
                    "message": f"competitor_brand is empty for content[{index}]",
                }
            )

        display_text = summary
        if brand and not has_competitor_prefix(summary, rank, brand):
            display_text = f"{rank}.{brand}：{summary}"

        normalized.append(
            {
                "rank": rank,
                "competitor_brand": brand,
                "summary": summary,
                "display_text": display_text,
            }
        )

    if not normalized:
        warnings.append(
            {
                "stage": "normalize_content",
                "section_id": "insight_3_competitor_weekly_dynamic",
                "message": "content is empty",
            }
        )
    return normalized


def build_sections(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []

    for config in SECTION_CONFIGS:
        path = path_for_config(config, args)
        data = read_json_file(path, config["section_id"], warnings)
        if data is None:
            continue

        section_id = config["section_id"]
        section_title = config["section_title"]
        if config["key"] == "insight_1":
            content = normalize_insight_1(data, section_title, warnings)
        elif config["key"] == "insight_2":
            content = normalize_insight_2(data, warnings)
        else:
            content = normalize_insight_3(data, warnings)

        has_content = bool(content)
        if has_content:
            sections.append(
                {
                    "section_id": section_id,
                    "section_title": section_title,
                    "content": content,
                }
            )

    return sections, warnings


def block_json(sections: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    json_sections = []
    for section in sections:
        content = section["content"]
        if section["section_id"] == "insight_3_competitor_weekly_dynamic" and isinstance(content, list):
            content = [
                {
                    "rank": item.get("rank"),
                    "competitor_brand": item.get("competitor_brand"),
                    "summary": item.get("summary"),
                }
                for item in content
            ]
        json_sections.append(
            {
                "section_id": section["section_id"],
                "section_title": section["section_title"],
                "content": content,
            }
        )
    return {
        "block_id": BLOCK_ID,
        "block_title": BLOCK_TITLE,
        "sections": json_sections,
        "warnings": warnings,
    }


def escape_text(value: Any) -> str:
    return html.escape(clean_text(value), quote=True)


def render_paragraph(text: str, css_class: str | None = None) -> str:
    class_attr = f' class="{css_class}"' if css_class else ""
    return f"    <p{class_attr}>{escape_text(text)}</p>"


def render_section(section: dict[str, Any]) -> str:
    lines = ['  <div class="insight-section">']
    lines.append(render_paragraph(section["section_title"], "section-title"))
    content = section["content"]

    if isinstance(content, str):
        if content:
            lines.append(render_paragraph(content))
    elif isinstance(content, dict):
        for key in ("communication_performance", "reputation_performance"):
            value = clean_text(content.get(key))
            if value:
                lines.append(render_paragraph(value))
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            display_text = clean_text(item.get("display_text"))
            if not display_text:
                rank = item.get("rank")
                brand = clean_text(item.get("competitor_brand"))
                summary = clean_text(item.get("summary"))
                display_text = f"{rank}.{brand}：{summary}" if rank and brand else summary
            if display_text:
                lines.append(render_paragraph(display_text))

    lines.append("  </div>")
    return "\n".join(lines)


def render_html(sections: list[dict[str, Any]]) -> str:
    rendered_sections = "\n\n".join(render_section(section) for section in sections)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(BLOCK_TITLE)}</title>
  <style>
    :root {{
      color-scheme: light;
      --insight-bg: #fff7df;
      --insight-border: #ead9ad;
      --insight-title: #2b2618;
      --insight-text: #3a3324;
      --insight-muted: #7a6a43;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      padding: 32px;
      background: #ffffff;
      color: var(--insight-text);
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif;
    }}

    .key-insights-block {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 24px 28px 26px;
      background: var(--insight-bg);
      border: 1px solid var(--insight-border);
      border-radius: 10px;
      box-shadow: 0 1px 0 rgba(122, 106, 67, 0.06);
    }}

    .key-insights-block h2 {{
      margin: 0 0 18px;
      color: var(--insight-title);
      font-size: 22px;
      line-height: 1.35;
      font-weight: 700;
      letter-spacing: 0;
    }}

    .insight-section {{
      margin-top: 18px;
    }}

    .insight-section:first-of-type {{
      margin-top: 0;
    }}

    .insight-section p {{
      margin: 8px 0 0;
      font-size: 15px;
      line-height: 1.86;
      letter-spacing: 0;
    }}

    .insight-section .section-title {{
      margin-top: 0;
      color: var(--insight-title);
      font-weight: 700;
      line-height: 1.6;
    }}

    @media (max-width: 640px) {{
      body {{
        padding: 16px;
      }}

      .key-insights-block {{
        padding: 18px 18px 20px;
        border-radius: 8px;
      }}

      .key-insights-block h2 {{
        font-size: 20px;
      }}

      .insight-section p {{
        font-size: 14px;
        line-height: 1.78;
      }}
    }}
  </style>
</head>
<body>
<section class="key-insights-block">
  <h2>{html.escape(BLOCK_TITLE)}</h2>

{rendered_sections}
</section>
</body>
</html>
"""


def write_outputs(args: argparse.Namespace, block: dict[str, Any], sections: list[dict[str, Any]]) -> None:
    output_json = Path(args.output_json)
    output_html = Path(args.output_html)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(block, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_html.write_text(render_html(sections), encoding="utf-8")


def main() -> int:
    args = parse_args()
    sections, warnings = build_sections(args)
    block = block_json(sections, warnings)
    write_outputs(args, block, sections)

    print(f"key insights block JSON written to: {args.output_json}")
    print(f"key insights block HTML written to: {args.output_html}")
    print(json.dumps({"sections": len(sections), "warnings": len(warnings)}, ensure_ascii=False))

    if not sections:
        print("no usable insight sections were found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
