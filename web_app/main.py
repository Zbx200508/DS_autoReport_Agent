"""Local FastAPI app for the report workbench."""

from __future__ import annotations

import hmac
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from dotenv import load_dotenv
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from .auth import normalize_owner_id
from .config_builder import UiConfigPayload, default_ui_config, ensure_query_config_version, save_query_config
from .pipeline_runner import ROOT_DIR, pipeline_runner
from .report_registry import (
    OUTPUT_ROOT,
    delete_report,
    get_report,
    get_report_by_config_hash,
    list_reports,
    public_report,
    report_filename,
    stored_path,
)


load_dotenv(ROOT_DIR / ".env")

STATIC_DIR = ROOT_DIR / "web_app" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
QUERY_CONFIG_UI = ROOT_DIR / "configs" / "query_config.ui.json"
REPORT_PREVIEW = ROOT_DIR / "outputs" / "report_preview.html"
TABLE_DATA_AUDIT_HTML = ROOT_DIR / "outputs" / "audit" / "table_data_audit.html"
TABLE_DATA_AUDIT_CSV = ROOT_DIR / "outputs" / "audit" / "table_data_audit.csv"
TABLE_MCP_AUDIT_HTML = ROOT_DIR / "outputs" / "audit" / "table_mcp_fetch_audit.html"
TABLE_MCP_AUDIT_CSV = ROOT_DIR / "outputs" / "audit" / "table_mcp_fetch_audit.csv"


class RunRequest(BaseModel):
    query_config_file: str = "configs/query_config.ui.json"
    force: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


app = FastAPI(title="Hisense Report Workbench", version="0.1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("APP_SESSION_SECRET", "local-dev-secret"),
    same_site="lax",
)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    if exc.status_code == 401:
        return auth_error_response()
    return JSONResponse({"success": False, "error": exc.detail}, status_code=exc.status_code)


def app_env() -> str:
    return os.getenv("APP_ENV", "local").lower()


def auth_enabled() -> bool:
    return not (app_env() == "local" and not os.getenv("APP_PASSWORD"))


def configured_username() -> str:
    return os.getenv("APP_USERNAME", "admin")


def session_user(username: str) -> dict[str, str]:
    return {"username": username, "owner_id": normalize_owner_id(username)}


def set_login_session(request: Request, username: str) -> dict[str, str]:
    user = session_user(username)
    request.session["username"] = user["username"]
    request.session["owner_id"] = user["owner_id"]
    request.session["user_id"] = user["username"]
    return user


def current_user(request: Request) -> dict[str, str] | None:
    if not auth_enabled():
        return session_user(configured_username())
    username = request.session.get("username") or request.session.get("user_id")
    if not username:
        return None
    username = str(username)
    owner_id = str(request.session.get("owner_id") or normalize_owner_id(username))
    if request.session.get("owner_id") != owner_id or request.session.get("username") != username:
        request.session["username"] = username
        request.session["owner_id"] = owner_id
    return {"username": username, "owner_id": owner_id}


def require_user(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user["username"]


def require_owner_id(request: Request) -> str:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user["owner_id"]


def auth_error_response() -> JSONResponse:
    return JSONResponse({"success": False, "error": "未登录或登录已过期"}, status_code=401)


def table_data_audit_error_response() -> JSONResponse:
    return JSONResponse({"success": False, "error": "表格原数据核对页尚未生成，请先生成报告。"}, status_code=404)


def table_data_audit_filename() -> str:
    brand = "海信"
    start_date = ""
    end_date = ""
    if QUERY_CONFIG_UI.exists():
        try:
            config = json.loads(QUERY_CONFIG_UI.read_text(encoding="utf-8"))
            brand = str(config.get("brand") or brand).strip() or brand
            start_date = str(config.get("start_date") or "")
            end_date = str(config.get("end_date") or "")
        except Exception:
            pass
    return f"{brand}品牌监测周报_表格原数据核对_{start_date}_{end_date}.csv"


def table_mcp_audit_error_response() -> JSONResponse:
    return JSONResponse({"success": False, "error": "表格 MCP 取数核对尚未生成，请先点击表格取数核对。"}, status_code=404)


def table_mcp_audit_filename() -> str:
    brand = "海信"
    start_date = ""
    end_date = ""
    if QUERY_CONFIG_UI.exists():
        try:
            config = json.loads(QUERY_CONFIG_UI.read_text(encoding="utf-8"))
            brand = str(config.get("brand") or brand).strip() or brand
            start_date = str(config.get("start_date") or "")
            end_date = str(config.get("end_date") or "")
        except Exception:
            pass
    return f"{brand}品牌监测周报_表格MCP取数核对_{start_date}_{end_date}.csv"


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(INDEX_HTML)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "app_env": app_env(),
        "auth_enabled": auth_enabled(),
        "has_app_username": bool(os.getenv("APP_USERNAME")),
        "has_app_password": bool(os.getenv("APP_PASSWORD")),
        "has_session_secret": bool(os.getenv("APP_SESSION_SECRET")),
        "has_mcp_server_url": bool(os.getenv("MCP_SERVER_URL")),
        "has_mcp_authorization": bool(os.getenv("MCP_AUTHORIZATION")),
        "has_ark_api_key": bool(os.getenv("ARK_API_KEY")),
        "output_base_dir": str(OUTPUT_ROOT),
    }


@app.post("/api/auth/login")
def login(request: Request, payload: LoginRequest) -> dict[str, Any]:
    if not auth_enabled():
        user = set_login_session(request, configured_username())
        return {"success": True, "user": user}

    expected_username = os.getenv("APP_USERNAME")
    expected_password = os.getenv("APP_PASSWORD")
    if not expected_username or not expected_password:
        return {"success": False, "error": "登录未配置"}

    username_ok = hmac.compare_digest(payload.username, expected_username)
    password_ok = hmac.compare_digest(payload.password, expected_password)
    if not (username_ok and password_ok):
        return {"success": False, "error": "用户名或密码错误"}

    user = set_login_session(request, expected_username)
    return {"success": True, "user": user}


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": user}


@app.post("/api/auth/logout")
def logout(request: Request) -> dict[str, Any]:
    request.session.clear()
    return {"success": True}


@app.get("/api/config/default")
def get_default_config(request: Request) -> dict[str, Any]:
    require_user(request)
    return default_ui_config()


@app.get("/api/config/current")
def get_current_config(request: Request) -> dict[str, Any]:
    owner_id = require_owner_id(request)
    if not QUERY_CONFIG_UI.exists():
        return {
            "success": True,
            "config_exists": False,
            "config": None,
            "existing_report": None,
        }

    try:
        config = json.loads(QUERY_CONFIG_UI.read_text(encoding="utf-8"))
        upgraded_config = ensure_query_config_version(config)
        if upgraded_config != config:
            QUERY_CONFIG_UI.write_text(json.dumps(upgraded_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            config = upgraded_config
    except Exception as exc:
        return {
            "success": False,
            "error": f"读取当前配置失败：{exc}",
            "config_exists": False,
            "config": None,
            "existing_report": None,
        }

    config_hash = config.get("config_hash")
    existing = get_report_by_config_hash(config_hash, owner_id) if config_hash else None
    return {
        "success": True,
        "config_exists": True,
        "config": {
            "brand": config.get("brand"),
            "competitors": config.get("competitors", []),
            "start_date": config.get("start_date"),
            "end_date": config.get("end_date"),
            "compare_start_date": config.get("compare_start_date"),
            "compare_end_date": config.get("compare_end_date"),
            "keywords_raw": config.get("keywords_raw", ""),
            "filter_words_raw": config.get("filter_words_raw", ""),
            "config_hash": config_hash,
        },
        "existing_report": public_report(existing) if existing else None,
    }


@app.post("/api/config/save")
def save_config(request: Request, payload: UiConfigPayload) -> dict[str, Any]:
    owner_id = require_owner_id(request)
    result = save_query_config(payload, QUERY_CONFIG_UI.relative_to(ROOT_DIR))
    if not result.get("success"):
        return result
    existing = get_report_by_config_hash(result["config_hash"], owner_id)
    result["existing_report"] = public_report(existing) if existing else None
    return result


@app.post("/api/report/run")
def run_report(http_request: Request, request: RunRequest) -> dict[str, Any]:
    owner_id = require_owner_id(http_request)
    query_config_path = Path(request.query_config_file)
    if not query_config_path.is_absolute():
        query_config_path = ROOT_DIR / query_config_path
    try:
        query_config = json.loads(query_config_path.read_text(encoding="utf-8"))
        query_config = ensure_query_config_version(query_config)
        query_config_path.write_text(json.dumps(query_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        return {"success": False, "error": f"读取配置失败：{exc}"}

    config_hash = query_config.get("config_hash")
    if config_hash and not request.force:
        existing = get_report_by_config_hash(config_hash, owner_id)
        if existing:
            return {
                "success": False,
                "error": "当前配置下已有一份已完成报告，请先删除旧报告或选择删除并重新生成。",
                "existing_report": public_report(existing),
            }

    result = pipeline_runner.start(request.query_config_file, owner=owner_id)
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "启动失败")}
    return {
        "success": True,
        "run_id": result["run_id"],
        "status": result["status"],
    }


@app.get("/api/report/status/{run_id}")
def report_status(request: Request, run_id: str) -> dict[str, Any]:
    owner_id = require_owner_id(request)
    status = pipeline_runner.status(run_id, owner=owner_id)
    if status is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    return status


@app.get("/api/reports")
def reports(request: Request) -> dict[str, Any]:
    owner_id = require_owner_id(request)
    return {"success": True, "reports": list_reports(owner_id)}


@app.get("/api/reports/by-config/{config_hash}")
def report_by_config(request: Request, config_hash: str) -> dict[str, Any]:
    owner_id = require_owner_id(request)
    report = get_report_by_config_hash(config_hash, owner_id)
    return {"report": public_report(report) if report else None}


@app.get("/api/reports/{report_id}/preview")
def registered_report_preview(request: Request, report_id: str) -> FileResponse:
    owner_id = require_owner_id(request)
    report = get_report(report_id, owner_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    path = stored_path(report)
    return FileResponse(path, media_type="text/html")


@app.get("/api/reports/{report_id}/download")
def registered_report_download(request: Request, report_id: str) -> FileResponse:
    owner_id = require_owner_id(request)
    report = get_report(report_id, owner_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    path = stored_path(report)
    return FileResponse(
        path,
        media_type="text/html",
        filename=report.get("filename") or path.name,
    )


@app.delete("/api/reports/{report_id}")
def registered_report_delete(request: Request, report_id: str) -> dict[str, Any]:
    owner_id = require_owner_id(request)
    delete_report(report_id, owner_id)
    return {"success": True}


@app.get("/api/report/preview")
def report_preview(request: Request) -> FileResponse:
    require_user(request)
    if not REPORT_PREVIEW.exists():
        raise HTTPException(status_code=404, detail="report_preview.html not found")
    return FileResponse(REPORT_PREVIEW, media_type="text/html")


@app.get("/api/report/download")
def report_download(request: Request) -> FileResponse:
    require_user(request)
    if not REPORT_PREVIEW.exists():
        raise HTTPException(status_code=404, detail="report_preview.html not found")
    filename = "report_preview.html"
    if QUERY_CONFIG_UI.exists():
        try:
            import json

            config = json.loads(QUERY_CONFIG_UI.read_text(encoding="utf-8"))
            filename = report_filename(config)
        except Exception:
            filename = "report_preview.html"
    return FileResponse(
        REPORT_PREVIEW,
        media_type="text/html",
        filename=filename,
    )


@app.get("/api/audit/table-data/preview")
def table_data_audit_preview(request: Request):
    require_user(request)
    if not TABLE_DATA_AUDIT_HTML.exists():
        return table_data_audit_error_response()
    return FileResponse(TABLE_DATA_AUDIT_HTML, media_type="text/html; charset=utf-8")


@app.get("/api/audit/table-data/download")
def table_data_audit_download(request: Request):
    require_user(request)
    if not TABLE_DATA_AUDIT_CSV.exists():
        return table_data_audit_error_response()
    return FileResponse(
        TABLE_DATA_AUDIT_CSV,
        media_type="text/csv; charset=utf-8",
        filename=table_data_audit_filename(),
    )


@app.post("/api/audit/table-mcp/run")
def table_mcp_audit_run(request: Request) -> dict[str, Any]:
    require_user(request)
    if not QUERY_CONFIG_UI.exists():
        return {"success": False, "error": "当前配置尚未保存，请先点击“确定配置”保存当前配置。"}
    try:
        query_config = json.loads(QUERY_CONFIG_UI.read_text(encoding="utf-8"))
        upgraded_config = ensure_query_config_version(query_config)
        if upgraded_config != query_config:
            QUERY_CONFIG_UI.write_text(json.dumps(upgraded_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        return {"success": False, "error": f"读取当前配置失败：{exc}"}

    missing_env = [name for name in ("MCP_SERVER_URL", "MCP_AUTHORIZATION") if not os.getenv(name)]
    if missing_env:
        return {"success": False, "error": "缺少环境变量 " + "、".join(missing_env)}

    command = [
        sys.executable,
        str(ROOT_DIR / "scripts" / "build_table_mcp_fetch_audit.py"),
        "--query-config-file",
        str(QUERY_CONFIG_UI),
    ]
    completed = subprocess.run(
        command,
        cwd=str(ROOT_DIR),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "表格 MCP 取数核对执行失败").strip()
        return {"success": False, "error": error[-1200:]}

    return {
        "success": True,
        "message": "表格 MCP 取数核对已完成",
        "preview_url": "/api/audit/table-mcp/preview",
        "download_url": "/api/audit/table-mcp/download",
    }


@app.get("/api/audit/table-mcp/preview")
def table_mcp_audit_preview(request: Request):
    require_user(request)
    if not TABLE_MCP_AUDIT_HTML.exists():
        return table_mcp_audit_error_response()
    return FileResponse(TABLE_MCP_AUDIT_HTML, media_type="text/html; charset=utf-8")


@app.get("/api/audit/table-mcp/download")
def table_mcp_audit_download(request: Request):
    require_user(request)
    if not TABLE_MCP_AUDIT_CSV.exists():
        return table_mcp_audit_error_response()
    return FileResponse(
        TABLE_MCP_AUDIT_CSV,
        media_type="text/csv; charset=utf-8",
        filename=table_mcp_audit_filename(),
    )
