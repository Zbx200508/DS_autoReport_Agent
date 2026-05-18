#!/usr/bin/env python3
"""Run the end-to-end report generation pipeline.

This is a thin orchestrator around the existing module scripts. It does not
reimplement MCP calls, LLM calls, table generation, or HTML rendering logic.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


SUMMARY_PATH = Path("outputs") / "pipeline_run_summary.json"
FINAL_REPORT_PATH = Path("outputs") / "report_preview.html"
TAIL_LIMIT = 3000
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
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def path_str(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def tail(text: str | None, limit: int = TAIL_LIMIT) -> str:
    if not text:
        return ""
    return text[-limit:]


def selected_stages(args: argparse.Namespace) -> list[dict[str, Any]]:
    start_index = STAGE_NAMES.index(args.start_from) if args.start_from else 0
    stop_index = STAGE_NAMES.index(args.stop_after) if args.stop_after else len(STAGES) - 1
    if start_index > stop_index:
        raise ValueError("--start-from cannot come after --stop-after")
    return STAGES[start_index : stop_index + 1]


def skipped_by_mode(stage: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str | None]:
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
    warnings = []
    if missing:
        warnings.append(
            {
                "stage": stage["stage"],
                "message": f"stage skipped by {reason}; existing downstream outputs are missing: {', '.join(missing)}",
            }
        )
    return {
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

    if status == "success":
        print(f"PIPELINE_STAGE_SUCCESS {stage_name}", flush=True)
        print(f"[{index}/{total}] {stage_name} success, {duration:.1f}s", flush=True)
    else:
        print(f"PIPELINE_STAGE_FAILED {stage_name}", flush=True)
        print(f"[{index}/{total}] {stage_name} failed, {duration:.1f}s", flush=True)
        if stderr_text:
            print(f"Error: {tail(stderr_text, 600)}", flush=True)

    return {
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

    status = pipeline_status(stage_results)
    if errors and not stage_results:
        status = "failed"
    elif errors and not FINAL_REPORT_PATH.exists():
        status = "failed"
    elif errors and FINAL_REPORT_PATH.exists():
        status = "partial_success"

    return {
        "pipeline_id": "full_report_pipeline",
        "generated_at": now_iso(),
        "query_config_file": args.query_config_file,
        "status": status,
        "final_report": path_str(FINAL_REPORT_PATH),
        "options": {
            "skip_llm": bool(args.skip_llm),
            "skip_mcp": bool(args.skip_mcp),
            "start_from": args.start_from,
            "stop_after": args.stop_after,
            "continue_on_error": bool(args.continue_on_error),
            "open_report": bool(args.open_report),
        },
        "stages": stage_results,
        "warnings": summary_warnings,
        "errors": summary_errors,
    }


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    summary_path = Path(args.summary_file)
    stage_results: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    try:
        stages = selected_stages(args)
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

        result = run_stage(stage, args, index, total)
        stage_results.append(result)
        if result["status"] == "failed" and not args.continue_on_error:
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
