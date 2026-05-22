#!/usr/bin/env python3
"""Assemble completed HTML blocks into a single report preview page.

This script only reads existing block HTML files and stitches them into one
standalone preview document. It does not call MCP, does not call an LLM, and
does not regenerate tables or copy.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


KEY_INSIGHTS_HTML = Path("outputs") / "blocks" / "key_insights_block.html"
BLOCK_1_1_HTML = Path("outputs") / "blocks" / "block_1_1_brand_overall_table.html"
BLOCK_1_2_HTML = Path("outputs") / "blocks" / "block_1_2_platform_overall_table.html"
CATEGORY_CONTROL_HTML = Path("outputs") / "category_control" / "category_control_table.html"
CATEGORY_CONTROL_5_HTML = Path("outputs") / "category_control_5" / "category_control_table.html"
BLOCK_1_1_JSON = Path("outputs") / "blocks" / "block_1_1_brand_overall_table.json"
OUTPUT_HTML = Path("outputs") / "report_preview.html"
OUTPUT_MANIFEST = Path("outputs") / "report_preview_manifest.json"
CATEGORY_CONTROL_BLOCK_ID = "category_control_table"
CATEGORY_CONTROL_5_BLOCK_ID = "category_control_table_5"

MODULES = [
    {
        "module_id": "block_1_1_brand_overall_table",
        "source": BLOCK_1_1_HTML,
    },
    {
        "module_id": "block_1_2_platform_overall_table",
        "source": BLOCK_1_2_HTML,
    },
    {
        "module_id": "key_insights_block",
        "source": KEY_INSIGHTS_HTML,
    },
    {
        "module_id": "category_control_table",
        "source": CATEGORY_CONTROL_HTML,
        "placeholder": "各品线重点媒介表现-1暂未生成，请检查 category_control_table_task 日志。",
    },
    {
        "module_id": "category_control_table_5",
        "source": CATEGORY_CONTROL_5_HTML,
        "placeholder": "各品线重点媒介表现-2暂未生成，请检查 category_control_table_5_task 日志。",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a single-page HTML report preview.")
    parser.add_argument("--key-insights-html", default=str(KEY_INSIGHTS_HTML), help="Path to key insights block HTML.")
    parser.add_argument("--block-1-1-html", default=str(BLOCK_1_1_HTML), help="Path to block 1.1 HTML.")
    parser.add_argument("--block-1-2-html", default=str(BLOCK_1_2_HTML), help="Path to block 1.2 HTML.")
    parser.add_argument("--category-control-html", default=str(CATEGORY_CONTROL_HTML), help="Path to category control table HTML.")
    parser.add_argument("--block-metadata-json", help="Optional block metadata JSON used to locate dynamic block HTML paths.")
    parser.add_argument("--metadata-json", default=str(BLOCK_1_1_JSON), help="Path to block 1.1 JSON for report title metadata.")
    parser.add_argument("--output-html", default=str(OUTPUT_HTML), help="Path for assembled report preview HTML.")
    parser.add_argument("--manifest", default=str(OUTPUT_MANIFEST), help="Path for preview manifest JSON.")
    return parser.parse_args()


def category_control_blocks_from_metadata(path: str | None, warnings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    metadata_path = Path(path)
    if not metadata_path.exists():
        warnings.append(
            {
                "stage": "read_block_metadata",
                "source": str(metadata_path),
                "message": "block metadata JSON file does not exist; using fallback category control HTML path",
            }
        )
        return {}
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        warnings.append(
            {
                "stage": "read_block_metadata",
                "source": str(metadata_path),
                "message": f"failed to read block metadata JSON; using fallback category control HTML path: {exc}",
            }
        )
        return {}

    blocks = data.get("blocks") if isinstance(data, dict) else data
    if isinstance(blocks, dict):
        blocks = [blocks]
    if not isinstance(blocks, list):
        warnings.append(
            {
                "stage": "read_block_metadata",
                "source": str(metadata_path),
                "message": "block metadata JSON does not contain a block list; using fallback category control HTML path",
            }
        )
        return {}

    result: dict[str, dict[str, Any]] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_id = block.get("block_id")
        if block_id not in {CATEGORY_CONTROL_BLOCK_ID, CATEGORY_CONTROL_5_BLOCK_ID}:
            continue
        result[str(block_id)] = block
    return result


def module_sources_from_args(args: argparse.Namespace, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    category_control_blocks = category_control_blocks_from_metadata(args.block_metadata_json, warnings)
    modules = [
        {"module_id": "block_1_1_brand_overall_table", "source": Path(args.block_1_1_html)},
        {"module_id": "block_1_2_platform_overall_table", "source": Path(args.block_1_2_html)},
        {"module_id": "key_insights_block", "source": Path(args.key_insights_html)},
    ]
    dynamic_tables = [
        (
            CATEGORY_CONTROL_BLOCK_ID,
            Path(args.category_control_html),
            "各品线重点媒介表现-1",
            "各品线重点媒介表现-1暂未生成，请检查 category_control_table_task 日志。",
        ),
        (
            CATEGORY_CONTROL_5_BLOCK_ID,
            CATEGORY_CONTROL_5_HTML,
            "各品线重点媒介表现-2",
            "各品线重点媒介表现-2暂未生成，请检查 category_control_table_5_task 日志。",
        ),
    ]
    for module_id, fallback_source, placeholder_title, placeholder in dynamic_tables:
        block = category_control_blocks.get(module_id)
        if block is not None and block.get("enabled") is False:
            continue
        source = fallback_source
        if block is not None:
            html_path = block.get("html_path")
            if isinstance(html_path, str) and html_path.strip():
                source = Path(html_path)
        modules.append({"module_id": module_id, "source": source, "placeholder_title": placeholder_title, "placeholder": placeholder})
    return modules


def read_report_metadata(path: Path, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    metadata = {
        "title": "品牌监测周报",
        "brand": None,
        "period": {"start_date": None, "end_date": None},
    }
    if not path.exists():
        warnings.append(
            {
                "stage": "read_metadata",
                "source": str(path),
                "message": "metadata JSON file does not exist; using default report title",
            }
        )
        return metadata

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(
            {
                "stage": "read_metadata",
                "source": str(path),
                "message": f"failed to read metadata JSON; using default report title: {exc}",
            }
        )
        return metadata

    if not isinstance(data, dict):
        warnings.append(
            {
                "stage": "read_metadata",
                "source": str(path),
                "message": "metadata JSON root is not an object; using default report title",
            }
        )
        return metadata

    brand = data.get("target_brand")
    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    start_date = period.get("start_date")
    end_date = period.get("end_date")

    if isinstance(brand, str) and brand.strip():
        metadata["brand"] = brand.strip()
        metadata["title"] = f"{brand.strip()}品牌监测周报"
    else:
        warnings.append(
            {
                "stage": "read_metadata",
                "source": str(path),
                "message": "target_brand is missing; using default report title",
            }
        )

    if isinstance(start_date, str) and start_date.strip():
        metadata["period"]["start_date"] = start_date.strip()
    else:
        warnings.append(
            {
                "stage": "read_metadata",
                "source": str(path),
                "message": "period.start_date is missing",
            }
        )

    if isinstance(end_date, str) and end_date.strip():
        metadata["period"]["end_date"] = end_date.strip()
    else:
        warnings.append(
            {
                "stage": "read_metadata",
                "source": str(path),
                "message": "period.end_date is missing",
            }
        )

    return metadata


def extract_style_content(html_text: str) -> list[str]:
    try:
        return [
            sanitize_embedded_style(match.strip())
            for match in re.findall(r"<style\b[^>]*>(.*?)</style>", html_text, flags=re.IGNORECASE | re.DOTALL)
            if sanitize_embedded_style(match.strip())
        ]
    except Exception:
        return []


def sanitize_embedded_style(style_text: str) -> str:
    """Drop standalone-page html/body rules before embedding a block."""
    sanitized = re.sub(
        r"(?is)(?:html\s*,\s*)?body\s*\{[^{}]*\}",
        "",
        style_text,
    )
    sanitized = re.sub(
        r"(?is)html\s*\{[^{}]*\}",
        "",
        sanitized,
    )
    return sanitized.strip()


def extract_body_content(html_text: str) -> str:
    try:
        match = re.search(r"<body\b[^>]*>(.*?)</body>", html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        cleaned = re.sub(r"<!doctype[^>]*>", "", html_text, flags=re.IGNORECASE)
        cleaned = re.sub(r"<html\b[^>]*>|</html>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<head\b[^>]*>.*?</head>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"<body\b[^>]*>|</body>", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()
    except Exception:
        return html_text.strip()


def read_module_html(module: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    source = Path(module["source"])
    result = {
        "module_id": module["module_id"],
        "source": str(source).replace("\\", "/"),
        "included": False,
        "styles": [],
        "body": "",
    }

    if not source.exists():
        message = module.get("placeholder")
        if isinstance(message, str) and message.strip():
            placeholder_title = str(module.get("placeholder_title") or "各品线重点媒介表现-1")
            result["body"] = (
                '<section class="report-placeholder-module">'
                f'<h1>{html.escape(placeholder_title, quote=True)}</h1>'
                f'<p>{html.escape(message, quote=True)}</p>'
                "</section>"
            )
            result["included"] = True
            warnings.append(
                {
                    "stage": "read_module",
                    "module_id": module["module_id"],
                    "source": str(source),
                    "message": "block HTML file does not exist; placeholder inserted",
                }
            )
            return result
        warnings.append(
            {
                "stage": "read_module",
                "module_id": module["module_id"],
                "source": str(source),
                "message": "block HTML file does not exist",
            }
        )
        return result

    try:
        html_text = source.read_text(encoding="utf-8")
    except Exception as exc:
        warnings.append(
            {
                "stage": "read_module",
                "module_id": module["module_id"],
                "source": str(source),
                "message": f"failed to read block HTML: {exc}",
            }
        )
        return result

    if not html_text.strip():
        warnings.append(
            {
                "stage": "read_module",
                "module_id": module["module_id"],
                "source": str(source),
                "message": "block HTML is empty",
            }
        )
        return result

    try:
        result["styles"] = extract_style_content(html_text)
        result["body"] = extract_body_content(html_text)
    except Exception as exc:
        warnings.append(
            {
                "stage": "extract_html",
                "module_id": module["module_id"],
                "source": str(source),
                "message": f"failed to extract style/body content: {exc}",
            }
        )
        result["body"] = html_text.strip()

    if result["body"]:
        result["included"] = True
    else:
        warnings.append(
            {
                "stage": "extract_html",
                "module_id": module["module_id"],
                "source": str(source),
                "message": "extracted body content is empty",
            }
        )

    return result


def escape_text(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def base_report_styles() -> str:
    return """
    :root {
      color-scheme: light;
      --report-bg: #f5f7fb;
      --paper-bg: #ffffff;
      --report-text: #1f2933;
      --report-muted: #667085;
      --report-line: #e6e8ef;
      --report-accent: #1d4ed8;
    }

    * {
      box-sizing: border-box;
    }

    html,
    body {
      margin: 0;
      min-height: 100%;
      background: var(--report-bg);
      color: var(--report-text);
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif;
    }

    body {
      padding: 36px 20px 48px;
    }

    .report-page {
      max-width: 1100px;
      margin: 0 auto;
      background: var(--paper-bg);
      border: 1px solid var(--report-line);
      border-radius: 14px;
      box-shadow: 0 14px 36px rgba(31, 41, 51, 0.08);
      overflow: hidden;
    }

    .report-header {
      padding: 34px 40px 26px;
      border-bottom: 1px solid var(--report-line);
      background: linear-gradient(180deg, #ffffff 0%, #fbfcff 100%);
    }

    .report-header h1 {
      margin: 0;
      font-size: 30px;
      line-height: 1.3;
      font-weight: 700;
      letter-spacing: 0;
      color: #111827;
    }

    .report-period {
      margin: 10px 0 0;
      color: var(--report-muted);
      font-size: 15px;
      line-height: 1.6;
      letter-spacing: 0;
    }

    .report-content {
      padding: 30px 40px 42px;
    }

    .report-module {
      margin-top: 30px;
    }

    .report-module:first-child {
      margin-top: 0;
    }

    .report-chapter-title {
      margin: 34px 0 18px;
      padding-bottom: 10px;
      border-bottom: 2px solid var(--report-line);
      color: #111827;
      font-size: 22px;
      line-height: 1.35;
      font-weight: 700;
      letter-spacing: 0;
    }

    .report-content > .wrap,
    .report-module > .wrap {
      max-width: none;
      margin: 0;
      padding: 0;
    }

    .report-module .key-insights-block {
      max-width: none;
      margin: 0;
    }

    .report-placeholder-module {
      border: 1px dashed var(--report-line);
      background: #fbfcff;
      padding: 18px 20px;
    }

    .report-placeholder-module h1 {
      margin: 0 0 8px;
      font-size: 22px;
      line-height: 1.35;
    }

    .report-placeholder-module p {
      margin: 0;
      color: var(--report-muted);
      font-size: 14px;
      line-height: 1.6;
    }

    .report-module table {
      width: 100%;
    }

    .report-module-table table th:first-child,
    .report-module-table table td:first-child {
      text-align: center !important;
      vertical-align: middle !important;
    }

    .report-module-category-control .category-control-table td.platform-cell,
    .report-module-category-control table td.platform-cell {
      text-align: center !important;
      vertical-align: middle !important;
      white-space: nowrap;
    }

    .report-module-category-control .category-control-table td.category-line-cell,
    .report-module-category-control table td.category-line-cell {
      text-align: center !important;
      vertical-align: middle !important;
    }

    @media (max-width: 760px) {
      body {
        padding: 16px;
      }

      .report-header {
        padding: 24px 22px 20px;
      }

      .report-header h1 {
        font-size: 24px;
      }

      .report-content {
        padding: 22px;
      }

      .report-chapter-title {
        font-size: 20px;
      }
    }
"""


def render_report_html(metadata: dict[str, Any], included_modules: list[dict[str, Any]]) -> str:
    title = metadata.get("title") or "品牌监测周报"
    period = metadata.get("period") if isinstance(metadata.get("period"), dict) else {}
    start_date = period.get("start_date")
    end_date = period.get("end_date")
    period_text = f"{start_date} 至 {end_date}" if start_date and end_date else ""

    combined_styles = [base_report_styles()]
    for module in included_modules:
        for style in module.get("styles", []):
            if style:
                combined_styles.append(f"/* {module['module_id']} */\n{style}")

    key_insights = next((module for module in included_modules if module["module_id"] == "key_insights_block"), None)
    block_1_1 = next((module for module in included_modules if module["module_id"] == "block_1_1_brand_overall_table"), None)
    block_1_2 = next((module for module in included_modules if module["module_id"] == "block_1_2_platform_overall_table"), None)
    category_control = next((module for module in included_modules if module["module_id"] == "category_control_table"), None)
    category_control_5 = next((module for module in included_modules if module["module_id"] == "category_control_table_5"), None)

    content_parts: list[str] = []
    table_parts: list[str] = []
    if block_1_1:
        table_parts.append(f'<div class="report-module report-module-table">\n{block_1_1["body"]}\n</div>')
    if block_1_2:
        table_parts.append(f'<div class="report-module report-module-table">\n{block_1_2["body"]}\n</div>')
    if table_parts:
        content_parts.append('<h2 class="report-chapter-title">1.品牌整体表现</h2>')
        content_parts.extend(table_parts)

    if key_insights:
        content_parts.append(f'<div class="report-module report-module-key-insights">\n{key_insights["body"]}\n</div>')
    if category_control:
        content_parts.append(f'<div class="report-module report-module-table report-module-category-control">\n{category_control["body"]}\n</div>')
    if category_control_5:
        content_parts.append(f'<div class="report-module report-module-table report-module-category-control">\n{category_control_5["body"]}\n</div>')

    body_content = "\n\n".join(content_parts)
    period_html = f'\n        <p class="report-period">{escape_text(period_text)}</p>' if period_text else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape_text(title)}</title>
  <style>
{chr(10).join(combined_styles)}
  </style>
</head>
<body>
  <main class="report-page">
    <header class="report-header">
      <h1>{escape_text(title)}</h1>{period_html}
    </header>
    <div class="report-content">
{body_content}
    </div>
  </main>
</body>
</html>
"""


def build_manifest(
    *,
    report_file: Path,
    metadata: dict[str, Any],
    module_results: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "report_file": str(report_file).replace("\\", "/"),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "title": metadata.get("title") or "品牌监测周报",
        "period": metadata.get("period") or {},
        "modules": [
            {
                "module_id": module["module_id"],
                "source": module["source"],
                "included": bool(module.get("included")),
            }
            for module in module_results
        ],
        "warnings": warnings,
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    warnings: list[dict[str, Any]] = []
    metadata = read_report_metadata(Path(args.metadata_json), warnings)
    module_results = [read_module_html(module, warnings) for module in module_sources_from_args(args, warnings)]
    included_modules = [module for module in module_results if module.get("included")]

    output_html = Path(args.output_html)
    manifest_path = Path(args.manifest)
    manifest = build_manifest(
        report_file=output_html,
        metadata=metadata,
        module_results=module_results,
        warnings=warnings,
    )
    write_json(manifest_path, manifest)

    if not included_modules:
        print("no usable block HTML modules were found", file=sys.stderr)
        print(f"report preview manifest written to: {manifest_path}")
        return 1

    report_html = render_report_html(metadata, included_modules)
    write_text(output_html, report_html)
    print(f"report preview written to: {output_html}")
    print(f"report preview manifest written to: {manifest_path}")
    print(json.dumps({"modules_included": len(included_modules), "warnings": len(warnings)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
