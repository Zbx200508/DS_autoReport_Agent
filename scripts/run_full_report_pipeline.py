#!/usr/bin/env python3
"""Run the end-to-end report generation pipeline.

This is a thin orchestrator around the existing module scripts. It does not
reimplement MCP calls, LLM calls, table generation, or HTML rendering logic.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - local environments may not install python-dotenv.
    load_dotenv = None


SUMMARY_PATH = Path("outputs") / "pipeline_run_summary.json"
FINAL_REPORT_PATH = Path("outputs") / "report_preview.html"
TAIL_LIMIT = 3000
CATEGORY_CONTROL_LATEST_DIR = Path("outputs") / "category_control"
CATEGORY_CONTROL_BLOCK_TITLE = "各品线重点媒介表现-1"
CATEGORY_CONTROL_5_LATEST_DIR = Path("outputs") / "category_control_5"
CATEGORY_CONTROL_5_BLOCK_TITLE = "各品线重点媒介表现-2"
CATEGORY_CONTROL_OUTPUT_FILES = [
    "category_control_table.html",
    "category_control_table.json",
    "category_control_table.csv",
    "category_control_raw_mcp.json",
    "category_control_raw_audit.csv",
    "category_control_raw_audit.html",
    "category_control_raw_audit.json",
    "category_control_warnings.json",
    "category_control_mcp_errors.json",
]

if load_dotenv:
    load_dotenv(Path(".env"))
TABLE_DATA_AUDIT_STAGE = {
    "stage": "table_data_audit",
    "script": Path("scripts") / "build_table_data_audit.py",
    "uses_query_config": True,
    "requires_mcp": False,
    "requires_llm": False,
    "outputs": [
        Path("outputs") / "audit" / "table_data_audit.html",
        Path("outputs") / "audit" / "table_data_audit.csv",
        Path("outputs") / "audit" / "table_data_audit.json",
    ],
}

STAGES = [
    {
        "stage": "block_1_1",
        "script": Path("scripts") / "build_block_1_1_brand_overall_table.py",
        "uses_query_config": True,
        "requires_mcp": True,
        "requires_llm": False,
        "outputs": [
            Path("outputs") / "blocks" / "block_1_1_brand_overall_table.json",
            Path("outputs") / "blocks" / "block_1_1_brand_overall_table.html",
        ],
    },
    {
        "stage": "block_1_2",
        "script": Path("scripts") / "build_block_1_2_platform_overall_table.py",
        "uses_query_config": True,
        "requires_mcp": True,
        "requires_llm": False,
        "outputs": [
            Path("outputs") / "blocks" / "block_1_2_platform_overall_table.json",
            Path("outputs") / "blocks" / "block_1_2_platform_overall_table.html",
        ],
    },
    {
        "stage": "category_control_table_task",
        "script": Path("scripts") / "build_category_control_table.py",
        "uses_query_config": True,
        "requires_mcp": True,
        "requires_llm": False,
        "non_blocking": True,
        "extra_args": [
            "--config",
            "configs/category_control_config.example.json",
            "--debug-mcp",
        ],
        "outputs": [
            Path("outputs") / "category_control" / "category_control_table.html",
            Path("outputs") / "category_control" / "category_control_table.json",
            Path("outputs") / "category_control" / "category_control_table.csv",
            Path("outputs") / "category_control" / "category_control_raw_audit.csv",
        ],
        "block_metadata": {
            "block_id": "category_control_table",
            "title": CATEGORY_CONTROL_BLOCK_TITLE,
            "html_path": "outputs/category_control/category_control_table.html",
            "json_path": "outputs/category_control/category_control_table.json",
            "csv_path": "outputs/category_control/category_control_table.csv",
            "raw_audit_path": "outputs/category_control/category_control_raw_audit.csv",
        },
    },
    {
        "stage": "category_control_table_5_task",
        "script": Path("scripts") / "build_category_control_table.py",
        "uses_query_config": True,
        "requires_mcp": True,
        "requires_llm": False,
        "non_blocking": True,
        "extra_args": [
            "--config",
            "configs/category_control_table_5_config.example.json",
            "--debug-mcp",
        ],
        "outputs": [
            Path("outputs") / "category_control_5" / "category_control_table.html",
            Path("outputs") / "category_control_5" / "category_control_table.json",
            Path("outputs") / "category_control_5" / "category_control_table.csv",
            Path("outputs") / "category_control_5" / "category_control_raw_audit.csv",
        ],
        "block_metadata": {
            "block_id": "category_control_table_5",
            "title": CATEGORY_CONTROL_5_BLOCK_TITLE,
            "html_path": "outputs/category_control_5/category_control_table.html",
            "json_path": "outputs/category_control_5/category_control_table.json",
            "csv_path": "outputs/category_control_5/category_control_table.csv",
            "raw_audit_path": "outputs/category_control_5/category_control_raw_audit.csv",
        },
    },
    {
        "stage": "insight_1_input",
        "script": Path("scripts") / "build_insight_1_input.py",
        "uses_query_config": False,
        "requires_mcp": False,
        "requires_llm": False,
        "outputs": [
            Path("outputs") / "insights" / "insight_1_hisense_weekly_dynamic_input.json",
        ],
    },
    {
        "stage": "insight_1_text",
        "script": Path("scripts") / "generate_insight_1_text.py",
        "uses_query_config": False,
        "requires_mcp": False,
        "requires_llm": True,
        "outputs": [
            Path("outputs") / "insights" / "insight_1_hisense_weekly_dynamic.json",
        ],
    },
    {
        "stage": "insight_2_input",
        "script": Path("scripts") / "build_insight_2_input.py",
        "uses_query_config": False,
        "requires_mcp": False,
        "requires_llm": False,
        "outputs": [
            Path("outputs") / "insights" / "insight_2_competitor_benchmark_input.json",
        ],
    },
    {
        "stage": "insight_2_text",
        "script": Path("scripts") / "generate_insight_2_text.py",
        "uses_query_config": False,
        "requires_mcp": False,
        "requires_llm": True,
        "outputs": [
            Path("outputs") / "insights" / "insight_2_competitor_benchmark.json",
        ],
    },
    {
        "stage": "insight_3_input",
        "script": Path("scripts") / "build_insight_3_input.py",
        "uses_query_config": True,
        "requires_mcp": True,
        "requires_llm": False,
        "outputs": [
            Path("outputs") / "insights" / "insight_3_competitor_weekly_dynamic_input.json",
        ],
    },
    {
        "stage": "insight_3_text",
        "script": Path("scripts") / "generate_insight_3_text.py",
        "uses_query_config": False,
        "requires_mcp": False,
        "requires_llm": True,
        "outputs": [
            Path("outputs") / "insights" / "insight_3_competitor_weekly_dynamic.json",
        ],
    },
    {
        "stage": "key_insights",
        "script": Path("scripts") / "build_key_insights_block.py",
        "uses_query_config": False,
        "requires_mcp": False,
        "requires_llm": False,
        "outputs": [
            Path("outputs") / "blocks" / "key_insights_block.json",
            Path("outputs") / "blocks" / "key_insights_block.html",
        ],
    },
    {
        "stage": "report_preview",
        "script": Path("scripts") / "build_report_preview.py",
        "uses_query_config": False,
        "requires_mcp": False,
        "requires_llm": False,
        "outputs": [
            Path("outputs") / "report_preview.html",
            Path("outputs") / "report_preview_manifest.json",
        ],
    },
]

STAGE_NAMES = [stage["stage"] for stage in STAGES]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full report generation pipeline.")
    parser.add_argument(
        "--query-config-file",
        default="configs/query_config.local.json",
        help="Path to query config JSON passed to MCP-dependent stages.",
    )
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM text generation stages.")
    parser.add_argument("--skip-mcp", action="store_true", help="Skip MCP-dependent stages.")
    parser.add_argument("--start-from", choices=STAGE_NAMES, help="Start execution from this stage.")
    parser.add_argument("--stop-after", choices=STAGE_NAMES, help="Stop execution after this stage.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue after a failed stage.")
    parser.add_argument("--open-report", action="store_true", help="Open outputs/report_preview.html after completion.")
    parser.add_argument("--summary-file", default=str(SUMMARY_PATH), help="Path for pipeline run summary JSON.")
    parser.add_argument("--run-id", help="Optional run id used for outputs/runs/<run_id>.")
    category_control_group = parser.add_mutually_exclusive_group()
    category_control_group.add_argument(
        "--enable-category-control-table",
        dest="enable_category_control_table",
        action="store_true",
        default=None,
        help="Force-enable category control table generation.",
    )
    category_control_group.add_argument(
        "--disable-category-control-table",
        dest="enable_category_control_table",
        action="store_false",
        help="Force-disable category control table generation.",
    )
    category_control_5_group = parser.add_mutually_exclusive_group()
    category_control_5_group.add_argument(
        "--enable-category-control-table-5",
        dest="enable_category_control_table_5",
        action="store_true",
        default=None,
        help="Force-enable category control table 5 generation.",
    )
    category_control_5_group.add_argument(
        "--disable-category-control-table-5",
        dest="enable_category_control_table_5",
        action="store_false",
        help="Force-disable category control table 5 generation.",
    )
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_run_id(value: str | None) -> str:
    raw = (value or default_run_id()).strip()
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in raw)
    return cleaned or default_run_id()


def run_root(run_id: str) -> Path:
    return Path("outputs") / "runs" / run_id


def category_control_output_dir(run_id: str) -> Path:
    return run_root(run_id) / "category_control"


def category_control_5_output_dir(run_id: str) -> Path:
    return run_root(run_id) / "category_control_5"


def category_control_block_metadata(
    output_dir: Path,
    *,
    enabled: bool = True,
    task_id: str = "category_control_table_task",
    block_id: str = "category_control_table",
    title: str = CATEGORY_CONTROL_BLOCK_TITLE,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "block_id": block_id,
        "title": title,
        "enabled": enabled,
        "non_blocking": True,
        "output_dir": path_str(output_dir),
        "html_path": path_str(output_dir / "category_control_table.html"),
        "json_path": path_str(output_dir / "category_control_table.json"),
        "csv_path": path_str(output_dir / "category_control_table.csv"),
        "raw_audit_path": path_str(output_dir / "category_control_raw_audit.csv"),
        "latest_sync_status": "skipped",
    }


def path_str(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def tail(text: str | None, limit: int = TAIL_LIMIT) -> str:
    if not text:
        return ""
    return text[-limit:]


def console_text(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def read_query_config(path: str, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        warnings.append(
            {
                "stage": "read_query_config",
                "source": path_str(config_path),
                "message": f"failed to read query config; using default module switches: {exc}",
            }
        )
        return {}
    return data if isinstance(data, dict) else {}


def bool_from_config(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def category_control_enabled(args: argparse.Namespace, query_config: dict[str, Any]) -> bool:
    if args.enable_category_control_table is not None:
        return bool(args.enable_category_control_table)
    report_modules = query_config.get("report_modules")
    if isinstance(report_modules, dict) and "enable_category_control_table" in report_modules:
        return bool_from_config(report_modules.get("enable_category_control_table"), True)
    return bool_from_config(query_config.get("enable_category_control_table"), True)


def category_control_5_enabled(args: argparse.Namespace, query_config: dict[str, Any]) -> bool:
    if args.enable_category_control_table_5 is not None:
        return bool(args.enable_category_control_table_5)
    report_modules = query_config.get("report_modules")
    if isinstance(report_modules, dict) and "enable_category_control_table_5" in report_modules:
        return bool_from_config(report_modules.get("enable_category_control_table_5"), True)
    return bool_from_config(query_config.get("enable_category_control_table_5"), True)


def selected_stages(args: argparse.Namespace) -> list[dict[str, Any]]:
    start_index = STAGE_NAMES.index(args.start_from) if args.start_from else 0
    stop_index = STAGE_NAMES.index(args.stop_after) if args.stop_after else len(STAGES) - 1
    if start_index > stop_index:
        raise ValueError("--start-from cannot come after --stop-after")
    return STAGES[start_index : stop_index + 1]


def stage_output_dir(stage: dict[str, Any]) -> Path | None:
    value = stage.get("category_control_output_dir")
    return value if isinstance(value, Path) else None


def prepare_stage_for_run(
    stage: dict[str, Any],
    run_id: str,
    category_control_is_enabled: bool,
    category_control_5_is_enabled: bool,
) -> dict[str, Any]:
    prepared = dict(stage)
    if stage["stage"] == "category_control_table_task":
        output_dir = category_control_output_dir(run_id)
        prepared["category_control_output_dir"] = output_dir
        prepared["category_control_latest_dir"] = CATEGORY_CONTROL_LATEST_DIR
        prepared["category_control_enabled"] = category_control_is_enabled
        prepared["extra_args"] = [
            *[str(item) for item in stage.get("extra_args", [])],
            "--output-dir",
            path_str(output_dir),
        ]
        prepared["outputs"] = [output_dir / name for name in CATEGORY_CONTROL_OUTPUT_FILES]
        prepared["block_metadata"] = category_control_block_metadata(output_dir, enabled=category_control_is_enabled)
    elif stage["stage"] == "category_control_table_5_task":
        output_dir = category_control_5_output_dir(run_id)
        prepared["category_control_output_dir"] = output_dir
        prepared["category_control_latest_dir"] = CATEGORY_CONTROL_5_LATEST_DIR
        prepared["category_control_enabled"] = category_control_5_is_enabled
        prepared["extra_args"] = [
            *[str(item) for item in stage.get("extra_args", [])],
            "--output-dir",
            path_str(output_dir),
        ]
        prepared["outputs"] = [output_dir / name for name in CATEGORY_CONTROL_OUTPUT_FILES]
        prepared["block_metadata"] = category_control_block_metadata(
            output_dir,
            enabled=category_control_5_is_enabled,
            task_id="category_control_table_5_task",
            block_id="category_control_table_5",
            title=CATEGORY_CONTROL_5_BLOCK_TITLE,
        )
    elif stage["stage"] == "report_preview":
        metadata_path = run_root(run_id) / "block_metadata.json"
        prepared["extra_args"] = [
            *[str(item) for item in stage.get("extra_args", [])],
            "--block-metadata-json",
            path_str(metadata_path),
        ]
    return prepared


def skipped_by_mode(stage: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str | None]:
    if stage["stage"] in {"category_control_table_task", "category_control_table_5_task"} and stage.get("category_control_enabled") is False:
        option_name = "enable_category_control_table_5" if stage["stage"] == "category_control_table_5_task" else "enable_category_control_table"
        return True, f"{option_name}=false"
    if args.skip_mcp and stage.get("requires_mcp"):
        return True, "--skip-mcp"
    if args.skip_llm and stage.get("requires_llm"):
        return True, "--skip-llm"
    return False, None


def required_env_for_stages(stages: list[dict[str, Any]], args: argparse.Namespace) -> list[str]:
    required: set[str] = set()
    for stage in stages:
        skip, _ = skipped_by_mode(stage, args)
        if skip:
            continue
        if stage.get("requires_mcp"):
            required.update({"MCP_SERVER_URL", "MCP_AUTHORIZATION"})
        if stage.get("requires_llm"):
            required.add("ARK_API_KEY")
    return sorted(required)


def redact_value(value: str) -> str:
    if not value:
        return value
    return "[REDACTED]"


def preflight(args: argparse.Namespace, stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    query_config = Path(args.query_config_file)
    if not query_config.exists():
        errors.append(
            {
                "stage": "preflight",
                "message": f"query_config file does not exist: {args.query_config_file}",
            }
        )

    for env_name in required_env_for_stages(stages, args):
        if not os.getenv(env_name):
            errors.append(
                {
                    "stage": "preflight",
                    "message": f"required environment variable is missing: {env_name}",
                }
            )

    for stage in stages:
        skip, _ = skipped_by_mode(stage, args)
        if skip:
            continue
        script = Path(stage["script"])
        if not script.exists():
            errors.append(
                {
                    "stage": "preflight",
                    "message": f"stage script does not exist: {script}",
                }
            )
    return errors


def command_for_stage(stage: dict[str, Any], args: argparse.Namespace) -> list[str]:
    command = [sys.executable, str(stage["script"])]
    command.extend(str(item) for item in stage.get("extra_args", []))
    if stage.get("uses_query_config"):
        command.extend(["--query-config-file", args.query_config_file])
    return command


def check_outputs(outputs: list[Path]) -> tuple[list[str], list[str]]:
    existing = []
    missing = []
    for output in outputs:
        if output.exists():
            existing.append(path_str(output))
        else:
            missing.append(path_str(output))
    return existing, missing


def skipped_stage_result(stage: dict[str, Any], reason: str) -> dict[str, Any]:
    outputs, missing = check_outputs(stage["outputs"])
    block_metadata = dict(stage.get("block_metadata") or {})
    disabled = bool(block_metadata and block_metadata.get("enabled") is False)
    if disabled:
        outputs = []
        missing = []
    if block_metadata:
        block_metadata["status"] = "skipped"
        block_metadata["latest_sync_status"] = "skipped"
        if disabled:
            block_metadata.pop("error", None)
        else:
            block_metadata["error"] = f"stage skipped by {reason}"
    warnings = []
    if missing and not disabled:
        warnings.append(
            {
                "stage": stage["stage"],
                "message": f"stage skipped by {reason}; existing downstream outputs are missing: {', '.join(missing)}",
            }
        )
    return {
        "task_id": stage["stage"],
        "stage": stage["stage"],
        "script": path_str(stage["script"]),
        "status": "skipped",
        "skip_reason": reason,
        "start_time": None,
        "end_time": None,
        "duration_seconds": 0,
        "stdout_tail": "",
        "stderr_tail": "",
        "outputs": outputs,
        "missing_outputs": missing,
        "enabled": block_metadata.get("enabled") if block_metadata else None,
        "non_blocking": bool(stage.get("non_blocking")),
        "block_metadata": block_metadata or None,
        "warnings": warnings,
        "errors": [],
    }


def run_stage(stage: dict[str, Any], args: argparse.Namespace, index: int, total: int) -> dict[str, Any]:
    stage_name = stage["stage"]
    command = command_for_stage(stage, args)
    start_time = now_iso()
    started = time.perf_counter()
    print(f"PIPELINE_STAGE_START {stage_name}", flush=True)
    print(f"[{index}/{total}] Running {stage_name}...", flush=True)

    stdout_text = ""
    stderr_text = ""
    return_code = 0
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
        return_code = completed.returncode
    except Exception as exc:
        return_code = -1
        stderr_text = str(exc)

    duration = round(time.perf_counter() - started, 3)
    end_time = now_iso()
    outputs, missing = check_outputs(stage["outputs"])
    block_metadata = dict(stage.get("block_metadata") or {})

    status = "success"
    if return_code != 0:
        status = "failed"
        errors.append(
            {
                "stage": stage_name,
                "message": f"subprocess exited with code {return_code}",
            }
        )
    if missing:
        status = "failed"
        errors.append(
            {
                "stage": stage_name,
                "message": f"missing expected outputs: {', '.join(missing)}",
            }
        )

    if block_metadata:
        block_metadata["status"] = "success" if status == "success" else "failed"
        block_metadata["enabled"] = block_metadata.get("enabled", True)
        block_metadata["non_blocking"] = bool(stage.get("non_blocking"))
        block_metadata["latest_sync_status"] = block_metadata.get("latest_sync_status", "skipped")
        if status != "success":
            messages = [error.get("message", "") for error in errors if error.get("message")]
            block_metadata["error"] = "; ".join(messages) or "stage failed"

    if status == "success":
        print(f"PIPELINE_STAGE_SUCCESS {stage_name}", flush=True)
        print(f"[{index}/{total}] {stage_name} success, {duration:.1f}s", flush=True)
    else:
        print(f"PIPELINE_STAGE_FAILED {stage_name}", flush=True)
        print(f"[{index}/{total}] {stage_name} failed, {duration:.1f}s", flush=True)
        if stderr_text:
            print(f"Error: {console_text(tail(stderr_text, 600))}", flush=True)

    return {
        "task_id": stage_name,
        "stage": stage_name,
        "script": path_str(stage["script"]),
        "command": [redact_value(part) if "MCP_AUTHORIZATION" in part or "ARK_API_KEY" in part else part for part in command],
        "status": status,
        "return_code": return_code,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": duration,
        "stdout_tail": tail(stdout_text),
        "stderr_tail": tail(stderr_text),
        "outputs": outputs,
        "missing_outputs": missing,
        "enabled": block_metadata.get("enabled") if block_metadata else None,
        "non_blocking": bool(stage.get("non_blocking")),
        "block_metadata": block_metadata or None,
        "warnings": warnings,
        "errors": errors,
    }


def run_post_report_audit(args: argparse.Namespace, warnings: list[dict[str, Any]]) -> None:
    """Generate optional table-data audit assets after the main report succeeds."""
    if not FINAL_REPORT_PATH.exists():
        return

    result = run_stage(TABLE_DATA_AUDIT_STAGE, args, len(STAGES) + 1, len(STAGES) + 1)
    if result["status"] != "success":
        warnings.append(
            {
                "stage": TABLE_DATA_AUDIT_STAGE["stage"],
                "message": "table data audit assets were not generated",
                "errors": result.get("errors", []),
            }
        )


def pipeline_status(stage_results: list[dict[str, Any]]) -> str:
    failed = [stage for stage in stage_results if stage.get("status") == "failed"]
    if not failed:
        return "success"
    if FINAL_REPORT_PATH.exists():
        return "partial_success"
    return "failed"


def build_summary(
    *,
    args: argparse.Namespace,
    stage_results: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    summary_errors = list(errors)
    summary_warnings = list(warnings)
    for stage in stage_results:
        summary_errors.extend(stage.get("errors", []))
        summary_warnings.extend(stage.get("warnings", []))
    blocks = all_block_records(stage_results)

    status = pipeline_status(stage_results)
    if errors and not stage_results:
        status = "failed"
    elif errors and not FINAL_REPORT_PATH.exists():
        status = "failed"
    elif errors and FINAL_REPORT_PATH.exists():
        status = "partial_success"

    return {
        "pipeline_id": "full_report_pipeline",
        "run_id": safe_run_id(args.run_id),
        "run_dir": path_str(run_root(safe_run_id(args.run_id))),
        "generated_at": now_iso(),
        "query_config_file": args.query_config_file,
        "status": status,
        "final_report": path_str(FINAL_REPORT_PATH),
        "options": {
            "run_id": args.run_id,
            "enable_category_control_table": args.enable_category_control_table,
            "enable_category_control_table_5": args.enable_category_control_table_5,
            "skip_llm": bool(args.skip_llm),
            "skip_mcp": bool(args.skip_mcp),
            "start_from": args.start_from,
            "stop_after": args.stop_after,
            "continue_on_error": bool(args.continue_on_error),
            "open_report": bool(args.open_report),
        },
        "stages": stage_results,
        "blocks": blocks,
        "warnings": summary_warnings,
        "errors": summary_errors,
    }


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def current_blocks(stage_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        stage["block_metadata"]
        for stage in stage_results
        if (
            isinstance(stage.get("block_metadata"), dict)
            and stage.get("block_metadata")
            and stage["block_metadata"].get("enabled") is not False
        )
    ]


def all_block_records(stage_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        stage["block_metadata"]
        for stage in stage_results
        if isinstance(stage.get("block_metadata"), dict) and stage.get("block_metadata")
    ]


def write_run_block_metadata(run_id: str, stage_results: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> Path:
    path = run_root(run_id) / "block_metadata.json"
    payload = {
        "run_id": run_id,
        "generated_at": now_iso(),
        "blocks": all_block_records(stage_results),
    }
    try:
        write_summary(path, payload)
    except Exception as exc:
        warnings.append(
            {
                "stage": "write_block_metadata",
                "source": path_str(path),
                "message": f"failed to write run block metadata: {exc}",
            }
        )
    return path


def sync_category_control_latest(output_dir: Path, latest_dir: Path, stage_result: dict[str, Any]) -> None:
    if stage_result.get("status") != "success":
        return

    warnings = stage_result.setdefault("warnings", [])
    latest_errors: list[str] = []
    copied: list[str] = []
    try:
        latest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        latest_errors.append(f"failed to create latest directory: {exc}")

    if not latest_errors:
        for file_name in CATEGORY_CONTROL_OUTPUT_FILES:
            source = output_dir / file_name
            target = latest_dir / file_name
            if not source.exists():
                continue
            try:
                shutil.copy2(source, target)
                copied.append(path_str(target))
            except Exception as exc:
                latest_errors.append(f"{file_name}: {exc}")

    if latest_errors:
        warning = {
            "stage": stage_result.get("stage", "category_control_table_task"),
            "message": "category control latest sync failed; run-specific outputs remain valid",
            "latest_sync_status": "failed",
            "latest_sync_error": "; ".join(latest_errors),
            "latest_dir": path_str(latest_dir),
        }
        warnings.append(warning)
        block_metadata = stage_result.get("block_metadata")
        if isinstance(block_metadata, dict):
            block_metadata["latest_sync_status"] = "failed"
            block_metadata["latest_sync_error"] = warning["latest_sync_error"]
        return

    block_metadata = stage_result.get("block_metadata")
    if isinstance(block_metadata, dict):
        block_metadata["latest_sync_status"] = "success"
        block_metadata["latest_dir"] = path_str(latest_dir)
    stage_result["latest_outputs"] = copied


def open_report(path: Path, warnings: list[dict[str, Any]]) -> None:
    if not path.exists():
        warnings.append({"stage": "open_report", "message": f"final report does not exist: {path}"})
        return
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path.resolve()))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path.resolve())])
    except Exception as exc:
        warnings.append({"stage": "open_report", "message": f"failed to open report: {exc}"})


def main() -> int:
    args = parse_args()
    run_id = safe_run_id(args.run_id)
    args.run_id = run_id
    summary_path = Path(args.summary_file)
    stage_results: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    query_config = read_query_config(args.query_config_file, warnings)
    category_control_is_enabled = category_control_enabled(args, query_config)
    category_control_5_is_enabled = category_control_5_enabled(args, query_config)
    args.enable_category_control_table = category_control_is_enabled
    args.enable_category_control_table_5 = category_control_5_is_enabled

    try:
        stages = [
            prepare_stage_for_run(stage, run_id, category_control_is_enabled, category_control_5_is_enabled)
            for stage in selected_stages(args)
        ]
    except Exception as exc:
        errors.append({"stage": "preflight", "message": str(exc)})
        summary = build_summary(args=args, stage_results=[], warnings=warnings, errors=errors)
        write_summary(summary_path, summary)
        print(f"Pipeline finished: failed")
        print(f"Summary: {summary_path}")
        return 1

    preflight_errors = preflight(args, stages)
    if preflight_errors:
        errors.extend(preflight_errors)
        summary = build_summary(args=args, stage_results=[], warnings=warnings, errors=errors)
        write_summary(summary_path, summary)
        print("Pipeline preflight failed:")
        for error in preflight_errors:
            print(f"- {error['message']}")
        print(f"Summary: {summary_path}")
        return 1

    total = len(stages)
    for index, stage in enumerate(stages, start=1):
        skip, reason = skipped_by_mode(stage, args)
        if skip:
            print(f"[{index}/{total}] Skipping {stage['stage']} ({reason})")
            stage_results.append(skipped_stage_result(stage, reason or "skipped"))
            continue

        if stage["stage"] == "report_preview":
            write_run_block_metadata(run_id, stage_results, warnings)

        result = run_stage(stage, args, index, total)
        output_dir = stage_output_dir(stage)
        if output_dir is not None:
            latest_dir = stage.get("category_control_latest_dir")
            if isinstance(latest_dir, Path):
                sync_category_control_latest(output_dir, latest_dir, result)
        stage_results.append(result)
        if result["status"] == "failed" and not args.continue_on_error and not stage.get("non_blocking"):
            print(f"Stopping after failed stage: {stage['stage']}")
            break

    if args.open_report:
        open_report(FINAL_REPORT_PATH, warnings)

    if stage_results and stage_results[-1].get("stage") == "report_preview" and stage_results[-1].get("status") == "success":
        run_post_report_audit(args, warnings)

    summary = build_summary(args=args, stage_results=stage_results, warnings=warnings, errors=errors)
    write_summary(summary_path, summary)

    print(f"Pipeline finished: {summary['status']}")
    if FINAL_REPORT_PATH.exists():
        print(f"Final report: {FINAL_REPORT_PATH}")
    print(f"Summary: {summary_path}")

    return 0 if summary["status"] in ("success", "partial_success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
