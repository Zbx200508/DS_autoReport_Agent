#!/usr/bin/env python3
"""Build a readable audit page for report table 1.1 and 1.2 source data.

The script only reads existing aggregate block JSON files and query config. It
does not call MCP, LLM, or read any post-level data.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT_DIR / "configs" / "query_config.ui.json"
BLOCK_1_1 = ROOT_DIR / "outputs" / "blocks" / "block_1_1_brand_overall_table.json"
BLOCK_1_2 = ROOT_DIR / "outputs" / "blocks" / "block_1_2_platform_overall_table.json"
AUDIT_DIR = ROOT_DIR / "outputs" / "audit"
HTML_OUTPUT = AUDIT_DIR / "table_data_audit.html"
CSV_OUTPUT = AUDIT_DIR / "table_data_audit.csv"
JSON_OUTPUT = AUDIT_DIR / "table_data_audit.json"

TABLE_1_MODULE = "1.1 品牌整体表现"
TABLE_2_MODULE = "1.2 品牌整体分平台表现"
EMPTY = "-"

TABLE_1_COLUMNS = [
    "报告周期",
    "同比周期",
    "品牌",
    "站点/平台",
    "声量",
    "声量占比 SOV",
    "SOV 同比变化",
    "互动量",
    "互动量占比 SOE",
    "SOE 同比变化",
    "全网口碑指数 NSR",
    "NSR 同比变化",
    "数据来源",
]

TABLE_2_COLUMNS = [
    "报告周期",
    "同比周期",
    "品牌",
    "站点/平台",
    "声量",
    "声量占比 SOV",
    "SOV 同比变化",
    "互动量",
    "互动量占比 SOE",
    "SOE 同比变化",
    "全网口碑指数 NSR",
    "NSR 同比变化",
    "SOV 控比",
    "SOE 控比",
    "特殊口径说明",
    "数据来源",
]

CSV_COLUMNS = ["表格模块", *TABLE_2_COLUMNS]
NUMERIC_COLUMNS = {"声量", "互动量"}
RIGHT_ALIGN_COLUMNS = {
    "声量",
    "声量占比 SOV",
    "SOV 同比变化",
    "互动量",
    "互动量占比 SOE",
    "SOE 同比变化",
    "全网口碑指数 NSR",
    "NSR 同比变化",
    "SOV 控比",
    "SOE 控比",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build table data audit HTML/CSV/JSON.")
    parser.add_argument("--query-config-file", default=str(DEFAULT_CONFIG), help="Path to query config JSON.")
    parser.add_argument("--block-1-1-file", default=str(BLOCK_1_1), help="Path to block 1.1 JSON.")
    parser.add_argument("--block-1-2-file", default=str(BLOCK_1_2), help="Path to block 1.2 JSON.")
    parser.add_argument("--output-dir", default=str(AUDIT_DIR), help="Directory for audit outputs.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def period_text(start_date: Any, end_date: Any) -> str:
    start = str(start_date or "").strip()
    end = str(end_date or "").strip()
    if start and end:
        return f"{start} 至 {end}"
    return ""


def resolve_periods(config: dict[str, Any], *blocks: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    start = first_text(config.get("start_date"), *(block.get("period", {}).get("start_date") for block in blocks))
    end = first_text(config.get("end_date"), *(block.get("period", {}).get("end_date") for block in blocks))
    compare_start = first_text(
        config.get("compare_start_date"),
        *(block.get("compare_period", {}).get("start_date") for block in blocks),
    )
    compare_end = first_text(
        config.get("compare_end_date"),
        *(block.get("compare_period", {}).get("end_date") for block in blocks),
    )
    return (
        period_text(start, end),
        period_text(compare_start, compare_end),
        {
            "start_date": start,
            "end_date": end,
            "compare_start_date": compare_start,
            "compare_end_date": compare_end,
        },
    )


def numeric_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.replace(",", "").replace("%", "").strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def format_empty(value: Any) -> str:
    if is_blank(value):
        return EMPTY
    return str(value)


def format_integer(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    number = numeric_value(value)
    if number is None:
        return EMPTY
    return f"{number:.0f}"


def format_percent(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    number = numeric_value(value)
    if number is None:
        return EMPTY
    return f"{number * 100:.2f}%"


def format_share_or_count(value: Any, *, display_type: str | None = None) -> str:
    if display_type == "love_like_unavailable":
        return EMPTY
    if display_type == "love_like":
        return format_integer(value)
    return format_percent(value)


def first_present(*values: Any) -> Any:
    for value in values:
        if not is_blank(value):
            return value
    return None


def normalize_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def build_table_1_rows(config: dict[str, Any], block: dict[str, Any], report_period: str, compare_period: str) -> list[dict[str, str]]:
    target_brand = first_text(config.get("brand"), block.get("target_brand"))
    rows: list[dict[str, str]] = []
    for item in normalize_rows(block.get("rows")):
        brand = first_text(item.get("brand"), target_brand)
        rows.append(
            {
                "报告周期": report_period or EMPTY,
                "同比周期": compare_period or EMPTY,
                "品牌": brand or EMPTY,
                "站点/平台": "总体",
                "声量": format_integer(item.get("total_volume")),
                "声量占比 SOV": format_percent(item.get("sov")),
                "SOV 同比变化": format_percent(item.get("sov_yoy")),
                "互动量": format_integer(item.get("total_interaction")),
                "互动量占比 SOE": format_percent(item.get("soe")),
                "SOE 同比变化": format_percent(item.get("soe_yoy")),
                "全网口碑指数 NSR": format_percent(item.get("nsr")),
                "NSR 同比变化": format_percent(item.get("nsr_yoy")),
                "数据来源": "block_1_1",
            }
        )
    return rows


def special_note(row: dict[str, Any], platform: str) -> str:
    explicit = first_text(row.get("special_scope_note"), row.get("special_note"), row.get("notes"))
    if explicit:
        return explicit
    if row.get("soe_display_type") == "love_like_unavailable":
        return "MCP 当前未返回 private_like_cnt，微信视频号 SOE 暂无法按爱心赞口径复现"
    if row.get("soe_display_type") == "love_like" or "微信视频号" in platform:
        return "微信视频号互动量口径请以当前系统字段为准"
    return EMPTY


def build_table_2_rows(config: dict[str, Any], block: dict[str, Any], report_period: str, compare_period: str) -> list[dict[str, str]]:
    target_brand = first_text(config.get("brand"), block.get("target_brand"))
    rows: list[dict[str, str]] = []
    for item in normalize_rows(block.get("rows")):
        platform = first_text(item.get("platform"), item.get("site"))
        rows.append(
            {
                "报告周期": report_period or EMPTY,
                "同比周期": compare_period or EMPTY,
                "品牌": target_brand or EMPTY,
                "站点/平台": platform or EMPTY,
                "声量": format_integer(first_present(item.get("total_volume"), item.get("volume"))),
                "声量占比 SOV": format_percent(item.get("sov")),
                "SOV 同比变化": format_percent(item.get("sov_yoy")),
                "互动量": format_integer(first_present(item.get("total_interaction"), item.get("interaction"))),
                "互动量占比 SOE": format_share_or_count(item.get("soe"), display_type=item.get("soe_display_type")),
                "SOE 同比变化": format_percent(item.get("soe_yoy")),
                "全网口碑指数 NSR": format_percent(item.get("nsr")),
                "NSR 同比变化": format_percent(item.get("nsr_yoy")),
                "SOV 控比": format_percent(first_present(item.get("sov_vs_benchmark"), item.get("sov_vs_target"))),
                "SOE 控比": format_percent(first_present(item.get("soe_vs_benchmark"), item.get("soe_vs_target"))),
                "特殊口径说明": special_note(item, platform),
                "数据来源": "block_1_2",
            }
        )
    return rows


def collect_checks(table_1_rows: list[dict[str, str]], table_2_rows: list[dict[str, str]]) -> list[str]:
    checks: list[str] = []
    if table_1_rows:
        checks.append("表格 1 已读取到品牌数据。")
    else:
        checks.append("表格 1 未读取到品牌数据，请检查 block_1_1 输出。")
    if table_2_rows:
        checks.append("表格 2 已读取到平台数据。")
    else:
        checks.append("表格 2 未读取到平台数据，请检查 block_1_2 输出。")

    all_rows = [*table_1_rows, *table_2_rows]
    missing_volume = [row for row in all_rows if row.get("声量") == EMPTY]
    missing_interaction = [row for row in all_rows if row.get("互动量") == EMPTY]
    missing_nsr = [row for row in all_rows if row.get("全网口碑指数 NSR") == EMPTY]
    if missing_volume:
        checks.append(f"有 {len(missing_volume)} 行缺少声量。")
    if missing_interaction:
        checks.append(f"有 {len(missing_interaction)} 行缺少互动量。")
    if missing_nsr:
        checks.append(f"有 {len(missing_nsr)} 行缺少 NSR。")
    if any("private_like_cnt" in str(row.get("特殊口径说明") or "") for row in table_2_rows):
        checks.append("MCP 当前未返回 private_like_cnt，微信视频号 SOE 暂无法按爱心赞口径复现。")
    elif any(row.get("站点/平台") == "微信视频号" or row.get("特殊口径说明") != EMPTY for row in table_2_rows):
        checks.append("微信视频号互动量口径请以当前系统字段为准。")
    return checks


def render_table(columns: list[str], rows: list[dict[str, str]]) -> str:
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    if not rows:
        return f'<table><thead><tr>{head}</tr></thead><tbody><tr><td colspan="{len(columns)}" class="empty">暂无数据</td></tr></tbody></table>'
    body_rows = []
    for row in rows:
        cells = []
        for column in columns:
            classes = "num" if column in RIGHT_ALIGN_COLUMNS else ""
            value = row.get(column, EMPTY)
            cells.append(f'<td class="{classes}">{html.escape(format_empty(value))}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def render_html(payload: dict[str, Any]) -> str:
    report_period = payload["periods"]["report_period"] or EMPTY
    compare_period = payload["periods"]["compare_period"] or EMPTY
    table_1_rows = payload["tables"]["table_1"]["rows"]
    table_2_rows = payload["tables"]["table_2"]["rows"]
    checks = payload["checks"]
    check_html = "".join(f"<li>{html.escape(item)}</li>" for item in checks)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>表格原数据核对</title>
  <style>
    body {{
      margin: 0;
      padding: 28px;
      background: #ffffff;
      color: #1f2937;
      font-family: Arial, "Microsoft YaHei", "PingFang SC", sans-serif;
      line-height: 1.55;
    }}
    main {{ max-width: 1480px; margin: 0 auto; }}
    h1 {{ margin: 0 0 10px; font-size: 24px; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; }}
    .meta {{ margin: 0 0 4px; color: #4b5563; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid #d9dee7; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 1180px; background: #fff; }}
    th, td {{ border: 1px solid #d9dee7; padding: 10px 12px; font-size: 13px; vertical-align: middle; }}
    th {{ background: #f3f5f8; color: #111827; font-weight: 700; white-space: nowrap; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    td.empty {{ text-align: center; color: #6b7280; }}
    .notes {{ margin-top: 28px; padding-top: 18px; border-top: 1px solid #e5e7eb; }}
    .notes ul {{ margin: 8px 0 0; padding-left: 22px; }}
  </style>
</head>
<body>
  <main>
    <h1>表格原数据核对</h1>
    <p class="meta">报告周期：{html.escape(report_period)}</p>
    <p class="meta">同比周期：{html.escape(compare_period)}</p>

    <h2>1.1 品牌整体表现原数据</h2>
    <div class="table-wrap">{render_table(TABLE_1_COLUMNS, table_1_rows)}</div>

    <h2>1.2 品牌整体分平台表现原数据</h2>
    <div class="table-wrap">{render_table(TABLE_2_COLUMNS, table_2_rows)}</div>

    <section class="notes">
      <h2>数据说明</h2>
      <ul>
        <li>本页面只展示表格 1 和表格 2 使用的聚合数据。</li>
        <li>不包含第三部分竞品动态原帖。</li>
        <li>不重新调用 MCP，不重新调用 LLM。</li>
      </ul>
      <h2>数据检查提示</h2>
      <ul>{check_html}</ul>
    </section>
  </main>
</body>
</html>
"""


def write_csv(path: Path, table_1_rows: list[dict[str, str]], table_2_rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in table_1_rows:
            writer.writerow({"表格模块": TABLE_1_MODULE, **row})
        for row in table_2_rows:
            writer.writerow({"表格模块": TABLE_2_MODULE, **row})


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    html_output = output_dir / "table_data_audit.html"
    csv_output = output_dir / "table_data_audit.csv"
    json_output = output_dir / "table_data_audit.json"

    config = load_json(Path(args.query_config_file))
    block_1_1 = load_json(Path(args.block_1_1_file))
    block_1_2 = load_json(Path(args.block_1_2_file))
    report_period, compare_period, period_parts = resolve_periods(config, block_1_1, block_1_2)

    table_1_rows = build_table_1_rows(config, block_1_1, report_period, compare_period)
    table_2_rows = build_table_2_rows(config, block_1_2, report_period, compare_period)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_files": {
            "query_config": str(Path(args.query_config_file)),
            "block_1_1": str(Path(args.block_1_1_file)),
            "block_1_2": str(Path(args.block_1_2_file)),
        },
        "periods": {
            "report_period": report_period,
            "compare_period": compare_period,
            **period_parts,
        },
        "tables": {
            "table_1": {"module": TABLE_1_MODULE, "columns": TABLE_1_COLUMNS, "rows": table_1_rows},
            "table_2": {"module": TABLE_2_MODULE, "columns": TABLE_2_COLUMNS, "rows": table_2_rows},
        },
        "checks": collect_checks(table_1_rows, table_2_rows),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    html_output.write_text(render_html(payload), encoding="utf-8")
    write_csv(csv_output, table_1_rows, table_2_rows)
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"audit html written to: {html_output}")
    print(f"audit csv written to: {csv_output}")
    print(f"audit json written to: {json_output}")
    print(json.dumps({"table_1_rows": len(table_1_rows), "table_2_rows": len(table_2_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
