"""Small local web UI and JSON API."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from homeone.config import set_provider_enabled
from homeone.core.model import Capability
from homeone.log import ring_buffer
from homeone.providers.mijia.token_import import TokenImportError, import_devices

_LOGGER = logging.getLogger(__name__)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

KIND_LABELS = {
    "ac": "空调",
    "purifier": "净化器",
    "fan": "风扇",
    "plug": "插座",
    "switch": "开关",
    "light": "灯",
    "tv": "电视",
    "thermostat": "温控",
    "dehumidifier": "除湿机",
    "sensor": "传感器",
}

CAP_LABELS = {
    "power": "电源",
    "mode": "模式",
    "target_temperature": "设定温度",
    "current_temperature": "当前温度",
    "fan_speed": "风速",
    "swing": "摆风",
    "pm25": "PM2.5",
    "temperature": "温度",
    "humidity": "湿度",
    "air_quality": "空气质量",
    "filter_life": "滤芯寿命",
    "child_lock": "童锁",
    "display": "显示屏",
    "brightness": "亮度",
    "color_temp": "色温",
    "outlet_in_use": "用电中",
    "volume": "音量",
    "mute": "静音",
    "input": "信源",
}

BOOL_CAPABILITIES = {
    Capability.POWER,
    Capability.SWING,
    Capability.CHILD_LOCK,
    Capability.DISPLAY,
    Capability.OUTLET_IN_USE,
}
INT_CAPABILITIES = {
    Capability.FAN_SPEED,
    Capability.AIR_QUALITY,
    Capability.FILTER_LIFE,
    Capability.BRIGHTNESS,
    Capability.COLOR_TEMP,
}


def create_app(gateway: Any) -> FastAPI:
    app = FastAPI(title="HomeOne", docs_url=None, redoc_url=None)

    def render(request: Request, name: str, **context: Any) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request,
            name,
            {
                "request": request,
                "gateway": gateway,
                "config": gateway.config,
                "cap_labels": CAP_LABELS,
                "kind_labels": KIND_LABELS,
                **context,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return render(
            request,
            "index.html",
            devices=gateway.manager.all_devices(),
            providers=gateway.provider_status(),
            pairing=gateway.pairing_info(),
            message=request.query_params.get("message"),
            error=request.query_params.get("error"),
        )

    @app.get("/setup", response_class=HTMLResponse)
    async def setup(request: Request) -> HTMLResponse:
        return render(
            request,
            "setup.html",
            mijia_devices=gateway.registry.mijia_entries(),
            mijia_cloud_saved=bool(
                gateway.secrets.get("mijia_cloud", "username")
            ),
            mijia_oauth_saved=bool(
                gateway.secrets.section("mijia_oauth").get("refresh_token")
            ),
            tcl_saved=bool(gateway.secrets.get("tcl", "username")),
            tcl_plus_saved=bool(
                gateway.secrets.get("tcl_plus", "phone")
                or gateway.secrets.get("tcl_plus", "refresh_token")
            ),
            treeow_saved=bool(gateway.secrets.get("treeow", "account")),
            message=request.query_params.get("message"),
            error=request.query_params.get("error"),
        )

    @app.post("/setup/mijia-cloud")
    async def setup_mijia_cloud(
        username: str = Form(""),
        password: str = Form(""),
        country: str = Form("cn"),
    ) -> RedirectResponse:
        if username.strip():
            gateway.secrets.set("mijia_cloud", "username", username.strip())
        if password:
            gateway.secrets.set("mijia_cloud", "password", password)
        gateway.secrets.set("mijia_cloud", "country", (country or "cn").strip().lower())
        if not gateway.secrets.get("mijia_cloud", "username") or not gateway.secrets.get(
            "mijia_cloud", "password"
        ):
            return _redirect("/setup", error="米家云账号和密码都需要填写")
        set_provider_enabled(gateway.config.path, "mijia_cloud", True)
        return _redirect(
            "/setup",
            message="米家云凭据已保存，重启网关后生效（可在外面用 homeone mijia-cloud-list 先验证）",
        )

    @app.post("/setup/tcl")
    async def setup_tcl(
        username: str = Form(""),
        password: str = Form(""),
    ) -> RedirectResponse:
        if username.strip():
            gateway.secrets.set("tcl", "username", username.strip())
        if password:
            gateway.secrets.set("tcl", "password", password)
        if not gateway.secrets.get("tcl", "username") or not gateway.secrets.get("tcl", "password"):
            return _redirect("/setup", error="TCL 账号和密码都需要填写")
        set_provider_enabled(gateway.config.path, "tcl", True)
        return _redirect("/setup", message="TCL 凭据已保存，重启网关后生效")

    @app.post("/setup/tcl-plus")
    async def setup_tcl_plus(
        phone: str = Form(""),
        password: str = Form(""),
        account_id: str = Form(""),
        refresh_token: str = Form(""),
    ) -> RedirectResponse:
        if phone.strip():
            gateway.secrets.set("tcl_plus", "phone", phone.strip())
        if password:
            gateway.secrets.set("tcl_plus", "password", password)
        if account_id.strip():
            gateway.secrets.set("tcl_plus", "account_id", account_id.strip())
        if refresh_token.strip():
            gateway.secrets.set("tcl_plus", "refresh_token", refresh_token.strip())
        has_password = bool(
            gateway.secrets.get("tcl_plus", "phone")
            and gateway.secrets.get("tcl_plus", "password")
        )
        has_token = bool(
            gateway.secrets.get("tcl_plus", "account_id")
            and gateway.secrets.get("tcl_plus", "refresh_token")
        )
        if not has_password and not has_token:
            return _redirect(
                "/setup",
                error="TCL+ 需要手机号+密码，或 account_id+refreshToken",
            )
        set_provider_enabled(gateway.config.path, "tcl_plus", True)
        return _redirect("/setup", message="TCL+ 凭据已保存，重启网关后生效")

    @app.post("/setup/treeow")
    async def setup_treeow(
        account: str = Form(""),
        password: str = Form(""),
    ) -> RedirectResponse:
        if account.strip():
            gateway.secrets.set("treeow", "account", account.strip())
        if password:
            gateway.secrets.set("treeow", "password", password)
        if not gateway.secrets.get("treeow", "account") or not gateway.secrets.get("treeow", "password"):
            return _redirect("/setup", error="树新风账号和密码都需要填写")
        set_provider_enabled(gateway.config.path, "treeow", True)
        return _redirect("/setup", message="树新风凭据已保存，重启网关后生效")

    @app.post("/setup/mijia")
    async def setup_mijia(
        device_id: str = Form(...),
        name: str = Form(""),
        model: str = Form(...),
        host: str = Form(...),
        token: str = Form(""),
    ) -> RedirectResponse:
        device_id = device_id.strip()
        host = host.strip()
        if not device_id or not host or not model:
            return _redirect("/setup", error="米家设备需要 id、型号和 IP")
        entry = {
            "id": device_id,
            "name": name.strip() or device_id,
            "model": model,
            "host": host,
        }
        gateway.registry.upsert_mijia(entry)
        if token.strip():
            tokens = dict(gateway.secrets.get("mijia", "tokens", default={}) or {})
            tokens[device_id] = token.strip()
            gateway.secrets.set("mijia", "tokens", tokens)
        set_provider_enabled(gateway.config.path, "mijia", True)
        return _redirect("/setup", message="米家设备已保存，重启网关后生效")

    @app.post("/setup/mijia/remove")
    async def remove_mijia(device_id: str = Form(...)) -> RedirectResponse:
        gateway.registry.remove_mijia(device_id)
        tokens = dict(gateway.secrets.get("mijia", "tokens", default={}) or {})
        tokens.pop(device_id, None)
        gateway.secrets.set("mijia", "tokens", tokens)
        return _redirect("/setup", message="米家设备已删除，重启网关后生效")

    @app.post("/setup/mijia/import")
    async def import_mijia(
        username: str = Form(...),
        password: str = Form(...),
        country: str = Form("cn"),
    ) -> RedirectResponse:
        try:
            found = await asyncio.to_thread(import_devices, username, password, country)
        except TokenImportError as exc:
            return _redirect("/setup", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("小米云导入失败")
            return _redirect("/setup", error=f"小米云导入失败: {exc}")
        tokens = dict(gateway.secrets.get("mijia", "tokens", default={}) or {})
        for item in found:
            token = item.pop("token", None)
            gateway.registry.upsert_mijia(item)
            if token:
                tokens[str(item["id"])] = token
        gateway.secrets.set("mijia", "tokens", tokens)
        if found:
            set_provider_enabled(gateway.config.path, "mijia", True)
        return _redirect(
            "/setup",
            message=f"已从米家导入 {len(found)} 台设备，重启网关后生效",
        )

    # ---- pairing -------------------------------------------------------
    @app.get("/pairing", response_class=HTMLResponse)
    async def pairing(request: Request) -> HTMLResponse:
        return render(
            request,
            "pairing.html",
            pairing=gateway.pairing_info(),
            message=request.query_params.get("message"),
            error=request.query_params.get("error"),
        )

    @app.get("/pairing/qr.png")
    async def pairing_qr() -> Response:
        if gateway.bridge is None:
            raise HTTPException(503, "HomeKit 未启用")
        return Response(content=gateway.bridge.qr_png(), media_type="image/png")

    @app.post("/pairing/reset")
    async def reset_pairing() -> RedirectResponse:
        if gateway.bridge is None:
            return _redirect("/pairing", error="HomeKit 未启用")
        await gateway.bridge.reset_pairing()
        gateway.request_restart()
        return _redirect("/pairing", message="已清除配对，网关正在重启")

    # ---- logs ----------------------------------------------------------
    @app.get("/logs", response_class=HTMLResponse)
    async def logs(request: Request) -> HTMLResponse:
        return render(
            request,
            "logs.html",
            records=list(ring_buffer.records)[-300:],
        )

    # ---- JSON API ------------------------------------------------------
    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "name": gateway.config.gateway.name,
                "devices": len(gateway.manager.devices),
                "providers": gateway.provider_status(),
                "homekit": gateway.pairing_info(),
            }
        )

    @app.get("/api/devices")
    async def devices() -> JSONResponse:
        return JSONResponse(gateway.manager.snapshots())

    @app.post("/api/devices/{device_id}/refresh")
    async def refresh(device_id: str) -> JSONResponse:
        try:
            await gateway.manager.refresh_device(device_id)
        except KeyError:
            raise HTTPException(404, "设备不存在") from None
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, str(exc)) from exc
        return JSONResponse(gateway.manager.get(device_id).snapshot())

    @app.post("/api/devices/{device_id}/set")
    async def set_value(device_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        try:
            capability = Capability(str(body["capability"]))
        except (KeyError, ValueError):
            raise HTTPException(400, "未知 capability") from None
        value = _coerce(capability, body.get("value"))
        try:
            await gateway.manager.set_capability(device_id, capability, value)
        except KeyError:
            raise HTTPException(404, "设备不存在") from None
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, str(exc)) from exc
        return JSONResponse(gateway.manager.get(device_id).snapshot())

    @app.post("/api/restart")
    async def restart() -> JSONResponse:
        gateway.request_restart()
        return JSONResponse({"ok": True, "message": "restarting"})

    return app


def _coerce(capability: Capability, value: Any) -> Any:
    if capability in BOOL_CAPABILITIES:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "yes")
        return bool(value)
    if capability in INT_CAPABILITIES:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _redirect(path: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    query = ""
    if message:
        query = f"?message={quote(message)}"
    elif error:
        query = f"?error={quote(error)}"
    return RedirectResponse(f"{path}{query}", status_code=303)
