"""Persistent report registry for generated HTML report assets."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .auth import normalize_owner_id
from .config_builder import config_hash


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")
OUTPUT_ROOT = Path(os.getenv("OUTPUT_BASE_DIR", str(ROOT_DIR / "outputs"))).expanduser()
if not OUTPUT_ROOT.is_absolute():
    OUTPUT_ROOT = ROOT_DIR / OUTPUT_ROOT
REPORTS_DIR = OUTPUT_ROOT / "reports"
REGISTRY_FILE = OUTPUT_ROOT / "report_registry.json"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_filename_part(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "", value.strip())
    return cleaned or "report"


def safe_owner(value: str) -> str:
    return normalize_owner_id(value)


def report_filename(config: dict[str, Any]) -> str:
    brand = safe_filename_part(str(config.get("brand") or "海信"))
    start_date = str(config.get("start_date") or "")
    end_date = str(config.get("end_date") or "")
    return f"{brand}品牌监测周报_{start_date}_{end_date}.html"


def report_title(config: dict[str, Any]) -> str:
    return f"{str(config.get('brand') or '海信').strip()}品牌监测周报"


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def stored_path(report: dict[str, Any]) -> Path:
    path = Path(str(report.get("file_path", "")))
    return path if path.is_absolute() else ROOT_DIR / path


def load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return {"reports": []}
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"reports": []}
    if not isinstance(data.get("reports"), list):
        data["reports"] = []
    return data


def save_registry(registry: dict[str, Any]) -> None:
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def report_owner(report: dict[str, Any]) -> str | None:
    owner = report.get("owner")
    return str(owner) if owner else None


def matches_owner(report: dict[str, Any], owner: str) -> bool:
    return report_owner(report) == safe_owner(owner)


def is_valid_report_file(report: dict[str, Any]) -> bool:
    return bool(report.get("file_path")) and stored_path(report).exists()


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    report_id = report["report_id"]
    return {
        "report_id": report_id,
        "config_hash": report.get("config_hash", report_id),
        "owner": report.get("owner"),
        "brand": report.get("brand"),
        "title": report.get("title"),
        "period": report.get("period"),
        "compare_period": report.get("compare_period"),
        "filename": report.get("filename"),
        "created_at": report.get("created_at"),
        "status": report.get("status", "completed"),
        "preview_url": f"/api/reports/{report_id}/preview",
        "download_url": f"/api/reports/{report_id}/download",
    }


def list_reports(owner: str) -> list[dict[str, Any]]:
    registry = load_registry()
    reports = [
        report
        for report in registry["reports"]
        if matches_owner(report, owner) and report.get("status") == "completed" and is_valid_report_file(report)
    ]
    return [public_report(report) for report in reports]


def get_report(report_id: str, owner: str) -> dict[str, Any] | None:
    registry = load_registry()
    for report in registry["reports"]:
        if report.get("report_id") == report_id and matches_owner(report, owner) and is_valid_report_file(report):
            return report
    return None


def get_report_by_config_hash(config_hash_value: str, owner: str) -> dict[str, Any] | None:
    registry = load_registry()
    for report in registry["reports"]:
        if (
            report.get("config_hash") == config_hash_value
            and matches_owner(report, owner)
            and report.get("status") == "completed"
            and is_valid_report_file(report)
        ):
            return report
    return None


def register_completed_report(config: dict[str, Any], source_html: Path, owner: str) -> dict[str, Any]:
    if not source_html.exists():
        raise FileNotFoundError(f"report preview not found: {source_html}")

    owner = safe_owner(owner)
    report_id = str(config.get("config_hash") or config_hash(config))
    title = report_title(config)
    filename = report_filename(config)
    report_dir = REPORTS_DIR / owner / report_id
    target = report_dir / filename
    report_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_html, target)

    meta = {
        "report_id": report_id,
        "config_hash": report_id,
        "owner": owner,
        "brand": config.get("brand"),
        "title": title,
        "start_date": config.get("start_date"),
        "end_date": config.get("end_date"),
        "compare_start_date": config.get("compare_start_date"),
        "compare_end_date": config.get("compare_end_date"),
        "period": {
            "start_date": config.get("start_date"),
            "end_date": config.get("end_date"),
        },
        "compare_period": {
            "start_date": config.get("compare_start_date"),
            "end_date": config.get("compare_end_date"),
        },
        "filename": filename,
        "file_path": display_path(target),
        "created_at": now_iso(),
        "status": "completed",
        "config": config,
    }

    (report_dir / "report_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    registry = load_registry()
    registry["reports"] = [
        item
        for item in registry["reports"]
        if not (item.get("report_id") == report_id and matches_owner(item, owner))
    ]
    registry["reports"].insert(0, {key: value for key, value in meta.items() if key != "config"})
    save_registry(registry)
    return public_report(meta)


def register_report(config: dict[str, Any], source_html: Path, owner: str = "default") -> dict[str, Any]:
    return register_completed_report(config, source_html, owner)


def delete_report(report_id: str, owner: str) -> bool:
    owner = safe_owner(owner)
    registry = load_registry()
    removed = [item for item in registry["reports"] if item.get("report_id") == report_id and matches_owner(item, owner)]
    registry["reports"] = [
        item
        for item in registry["reports"]
        if not (item.get("report_id") == report_id and matches_owner(item, owner))
    ]
    save_registry(registry)

    report_dir = REPORTS_DIR / owner / report_id
    had_dir = report_dir.exists()
    if report_dir.exists():
        shutil.rmtree(report_dir)
    return bool(removed) or had_dir
