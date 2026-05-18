"""In-memory runner for the local full report pipeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from .config_builder import ensure_query_config_version
from .report_registry import register_completed_report


ROOT_DIR = Path(__file__).resolve().parents[1]
RUN_SUMMARY_DIR = ROOT_DIR / "outputs" / "pipeline_runs"
DEFAULT_SUMMARY_FILE = ROOT_DIR / "outputs" / "pipeline_run_summary.json"
FINAL_REPORT = ROOT_DIR / "outputs" / "report_preview.html"
STAGE_NAMES = [
    "block_1_1",
    "block_1_2",
    "insight_1_input",
    "insight_1_text",
    "insight_2_input",
    "insight_2_text",
    "insight_3_input",
    "insight_3_text",
    "key_insights",
    "report_preview",
]
MAX_RUNS = 5
SECRET_PATTERNS = [
    re.compile(r"(bear\s+)[^\s]+", re.IGNORECASE),
    re.compile(r"(authorization\s*[:=]\s*)[^\s]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*)[^\s]+", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)[^\s]+", re.IGNORECASE),
]


def now_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def initial_stage_status() -> list[dict[str, Any]]:
    return [{"stage": stage, "status": "pending"} for stage in STAGE_NAMES]


def normalize_stage_status(stages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    by_stage = {
        item.get("stage"): item.get("status") or "pending"
        for item in stages or []
        if item.get("stage") in STAGE_NAMES
    }
    return [{"stage": stage, "status": by_stage.get(stage, "pending")} for stage in STAGE_NAMES]


def sanitize_log(text: str) -> str:
    cleaned = text.rstrip("\r\n")
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub(lambda match: match.group(1) + "[REDACTED]", cleaned)
    return cleaned


class PipelineRunner:
    def __init__(self) -> None:
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def _trim_runs(self) -> None:
        while len(self._runs) > MAX_RUNS:
            self._runs.popitem(last=False)

    def _set_run(self, run_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._runs[run_id] = data
            self._runs.move_to_end(run_id)
            self._trim_runs()

    def _get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                self._runs.move_to_end(run_id)
            return run

    def _append_log(self, run_id: str, line: str) -> None:
        run = self._get_run(run_id)
        if run is None:
            return
        line = sanitize_log(line)
        if not line:
            return
        with self._lock:
            logs = run.setdefault("logs", [])
            logs.append(line)
            if len(logs) > 200:
                del logs[:-200]
            self._handle_pipeline_log_line(run, line)

    def _set_stage_status(self, run: dict[str, Any], stage: str, status: str) -> None:
        if stage not in STAGE_NAMES:
            return
        for item in run["stages"]:
            if item["stage"] == stage:
                item["status"] = status
                break

    def _mark_running_stage_failed(self, run: dict[str, Any]) -> None:
        for item in run["stages"]:
            if item["status"] == "running":
                item["status"] = "failed"
                run["current_stage"] = item["stage"]
                return

    def _handle_pipeline_log_line(self, run: dict[str, Any], line: str) -> None:
        marker_match = re.match(r"PIPELINE_STAGE_(START|SUCCESS|FAILED)\s+([a-zA-Z0-9_]+)", line)
        if marker_match:
            event = marker_match.group(1)
            stage = marker_match.group(2)
            if stage not in STAGE_NAMES:
                return
            run["current_stage"] = stage
            if event == "START":
                self._set_stage_status(run, stage, "running")
            elif event == "SUCCESS":
                self._set_stage_status(run, stage, "success")
            else:
                self._set_stage_status(run, stage, "failed")
                error = {"stage": stage, "message": f"stage failed: {stage}"}
                if error not in run.setdefault("errors", []):
                    run["errors"].append(error)
            return

        running_match = re.search(r"Running\s+([a-zA-Z0-9_]+)", line)
        if running_match:
            stage = running_match.group(1)
            if stage not in STAGE_NAMES:
                return
            run["current_stage"] = stage
            self._set_stage_status(run, stage, "running")
            return

        finished_match = re.search(r"\]\s+([a-zA-Z0-9_]+)\s+(success|failed)", line)
        if finished_match:
            stage = finished_match.group(1)
            status = finished_match.group(2)
            if stage not in STAGE_NAMES:
                return
            run["current_stage"] = stage
            self._set_stage_status(run, stage, status)

    def _summary_candidates(self, run: dict[str, Any], summary_file: Path) -> list[Path]:
        candidates = [summary_file]
        started_at_epoch = run.get("started_at_epoch")
        if DEFAULT_SUMMARY_FILE != summary_file and DEFAULT_SUMMARY_FILE.exists():
            try:
                if started_at_epoch is None or DEFAULT_SUMMARY_FILE.stat().st_mtime >= started_at_epoch:
                    candidates.append(DEFAULT_SUMMARY_FILE)
            except OSError:
                pass
        return candidates

    def _read_summary(self, run: dict[str, Any], summary_file: Path) -> dict[str, Any] | None:
        for candidate in self._summary_candidates(run, summary_file):
            if not candidate.exists():
                continue
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as exc:
                with self._lock:
                    run.setdefault("errors", []).append(f"failed to read pipeline summary: {exc}")
                return None
        return None

    def _apply_summary(self, run: dict[str, Any], summary: dict[str, Any], *, terminal: bool) -> None:
        stages = normalize_stage_status(summary.get("stages", []))
        run["stages"] = stages
        run["summary"] = summary

        running_stage = next((item["stage"] for item in stages if item["status"] == "running"), None)
        if running_stage:
            run["current_stage"] = running_stage
        elif terminal:
            run["current_stage"] = None

        if terminal:
            run["status"] = summary.get("status") or "failed"
            run["errors"] = summary.get("errors", [])

    def _resolve_query_config(self, query_config_file: str) -> Path:
        path = Path(query_config_file)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path

    def _load_run_config(self, query_config_file: str) -> dict[str, Any]:
        path = self._resolve_query_config(query_config_file)
        config = json.loads(path.read_text(encoding="utf-8"))
        config = ensure_query_config_version(config)
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return config

    def _clear_report_links(self, run: dict[str, Any]) -> None:
        run["report"] = None
        run["final_report"] = None
        run["download_url"] = None

    def _registration_error(self, run: dict[str, Any], message: str) -> None:
        run["status"] = "partial_success"
        self._clear_report_links(run)
        run.setdefault("errors", []).append(f"报告生成成功，但报告资产注册失败：{message}")

    def _publish_success_report(self, run: dict[str, Any]) -> None:
        config = run.get("report_config")
        if not config:
            self._registration_error(run, "missing run config")
            return
        try:
            report = register_completed_report(config, FINAL_REPORT, owner=run.get("owner", "default"))
        except Exception as exc:
            self._registration_error(run, str(exc))
            return
        run["report"] = report
        run["final_report"] = report["preview_url"]
        run["download_url"] = report["download_url"]

    def start(self, query_config_file: str, owner: str = "default") -> dict[str, Any]:
        missing_env = [
            name
            for name in ("MCP_SERVER_URL", "MCP_AUTHORIZATION", "ARK_API_KEY")
            if not os.getenv(name)
        ]
        if missing_env:
            return {
                "success": False,
                "error": "缺少环境变量 " + "、".join(missing_env),
            }

        run_id = now_run_id()
        if self._get_run(run_id) is not None:
            run_id = f"{run_id}_{len(self._runs) + 1}"

        RUN_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        summary_file = RUN_SUMMARY_DIR / f"{run_id}_summary.json"
        try:
            report_config = self._load_run_config(query_config_file)
        except Exception as exc:
            return {"success": False, "error": f"failed to read query config: {exc}"}
        command = [
            sys.executable,
            str(ROOT_DIR / "scripts" / "run_full_report_pipeline.py"),
            "--query-config-file",
            query_config_file,
            "--summary-file",
            str(summary_file),
        ]
        run = {
            "run_id": run_id,
            "status": "running",
            "current_stage": STAGE_NAMES[0],
            "stages": [
                {"stage": stage, "status": "running" if index == 0 else "pending"}
                for index, stage in enumerate(STAGE_NAMES)
            ],
            "logs": [],
            "final_report": None,
            "download_url": None,
            "report": None,
            "errors": [],
            "summary_file": str(summary_file),
            "config_hash": report_config["config_hash"],
            "owner": owner,
            "query_config": report_config,
            "report_config": report_config,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "started_at_epoch": datetime.now().timestamp(),
        }
        self._set_run(run_id, run)

        try:
            env = os.environ.copy()
            process = subprocess.Popen(
                command,
                cwd=str(ROOT_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:
            with self._lock:
                run["status"] = "failed"
                run["errors"].append(str(exc))
            return {"success": False, "error": str(exc)}

        with self._lock:
            run["process"] = process

        thread = threading.Thread(target=self._watch_process, args=(run_id, process, summary_file), daemon=True)
        thread.start()
        return {"success": True, "run_id": run_id, "status": "running"}

    def _watch_process(self, run_id: str, process: subprocess.Popen[str], summary_file: Path) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                self._append_log(run_id, line)
        return_code = process.wait()
        self._finish_run(run_id, return_code, summary_file)

    def _finish_run(self, run_id: str, return_code: int, summary_file: Path) -> None:
        run = self._get_run(run_id)
        if run is None:
            return

        summary = self._read_summary(run, summary_file)

        with self._lock:
            if summary:
                summary_status = summary.get("status")
                run["status"] = "success" if summary_status == "success" else summary_status or "failed"
                run["stages"] = [
                    {"stage": item.get("stage"), "status": item.get("status")}
                    for item in summary.get("stages", [])
                ] or run["stages"]
                run["errors"] = summary.get("errors", [])
                run["summary"] = summary
                run["stages"] = normalize_stage_status(run.get("stages", []))
            elif return_code == 0:
                run["status"] = "success"
                run["stages"] = normalize_stage_status(run.get("stages", []))
            else:
                run["status"] = "failed"
                self._mark_running_stage_failed(run)
                run["errors"].append(f"pipeline exited with code {return_code}")
                run["stages"] = normalize_stage_status(run.get("stages", []))

            if run["status"] == "success":
                self._publish_success_report(run)
            else:
                self._clear_report_links(run)
            run["current_stage"] = None
            run.pop("process", None)
            run["ended_at"] = datetime.now().isoformat(timespec="seconds")

    def _sync_run_status(self, run_id: str, run: dict[str, Any]) -> None:
        if run.get("status") != "running" and run.get("ended_at"):
            return
        summary_file = Path(run.get("summary_file", ""))
        summary = self._read_summary(run, summary_file)
        if summary:
            terminal = summary.get("status") in {"success", "failed", "partial_success"}
            with self._lock:
                self._apply_summary(run, summary, terminal=terminal)
                if terminal:
                    if run["status"] == "success":
                        self._publish_success_report(run)
                    else:
                        self._clear_report_links(run)
                    run.pop("process", None)
                    run["ended_at"] = run.get("ended_at") or datetime.now().isoformat(timespec="seconds")

        process = run.get("process")
        if process is None or run.get("status") != "running":
            return

        return_code = process.poll()
        if return_code is not None:
            self._finish_run(run_id, return_code, summary_file)

    def status(self, run_id: str, owner: str | None = None) -> dict[str, Any] | None:
        run = self._get_run(run_id)
        if run is None:
            return None
        if owner is not None and run.get("owner") != owner:
            return None
        self._sync_run_status(run_id, run)
        with self._lock:
            stages = normalize_stage_status(run.get("stages", []))
            run["stages"] = stages
            return {
                "run_id": run["run_id"],
                "status": run["status"],
                "current_stage": run.get("current_stage"),
                "stages": stages,
                "logs": list(run.get("logs", [])),
                "report": run.get("report"),
                "final_report": run.get("final_report"),
                "download_url": run.get("download_url"),
                "errors": list(run.get("errors", [])),
            }


pipeline_runner = PipelineRunner()
