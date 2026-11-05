"""Small local web UI and JSON API."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from homekit_one.config import set_provider_devices, set_provider_enabled
from homekit_one.core.model import Capability
from homekit_one.log import ring_buffer
from homekit_one.providers.mijia.models import MODELS
from homekit_one.providers.mijia.token_import import (
    TokenImportError,
    import_devices,
    import_devices_from_oauth,
)
from homekit_one.providers.mijia_cloud.oauth import (
    DEFAULT_REDIRECT_URI,
    XiaomiOAuthClient,
    generate_device_id,
    parse_callback,
    state_for,
)
from homekit_one.providers.onboarding import (
    PROVIDER_LABELS,
    ProviderReport,
    probe_mijia_device,
    probe_provider,
)
from homekit_one.providers.plugins import get_plugin, plugins

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
    "vacuum": "扫地机",
    "camera": "摄像头",
    "pet_feeder": "宠物喂食器",
    "pet_waterer": "宠物饮水机",
    "speaker": "音箱",
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
    "battery": "电量",
    "motion": "移动",
    "voc": "甲醛/VOC",
    "uv": "UV 杀菌",
    "anion": "负离子",
    "buzzer": "提示音",
    "smart_mode": "智能托管",
    "smart_night": "夜间智能",
    "filter_days": "滤芯剩余天数",
    "motion_detection": "移动侦测",
    "camera_wdr": "宽动态",
    "camera_full_color": "全彩夜视",
    "camera_tracking": "移动追踪",
    "camera_watermark": "时间水印",
    "carpet_boost": "地毯增压",
    "auto_dust_arrest": "自动集尘",
    "auto_mop_dry": "自动烘干",
    "no_disturb": "请勿打扰",
    "ai_cleaning": "AI 清扫",
    "sleep_mode": "睡眠模式",
    "mite_removal": "除螨",
    "alarm": "报警音",
    "filter_reset": "滤芯复位",
}

CAP_UNITS = {
    "pm25": "µg/m³",
    "voc": "µg/m³",
    "temperature": "°C",
    "current_temperature": "°C",
    "target_temperature": "°C",
    "humidity": "%",
    "filter_life": "%",
    "filter_days": "天",
    "battery": "%",
    "fan_speed": "%",
    "brightness": "%",
    "volume": "%",
    "color_temp": "K",
}

MODE_LABELS = {
    "auto": "自动",
    "cool": "制冷",
    "heat": "制热",
    "dry": "除湿",
    "fan": "送风",
    "manual": "手动",
    "sleep": "睡眠",
    "comfort": "极净",
}

MODE_OPTIONS = [
    "auto",
    "cool",
    "heat",
    "dry",
    "fan",
    "manual",
    "sleep",
    "comfort",
]

BOOL_CAPABILITIES = {
    Capability.POWER,
    Capability.SWING,
    Capability.CHILD_LOCK,
    Capability.DISPLAY,
    Capability.OUTLET_IN_USE,
    Capability.MOTION,
    Capability.MUTE,
    Capability.UV,
    Capability.ANION,
    Capability.BUZZER,
    Capability.SMART_MODE,
    Capability.SMART_NIGHT,
    Capability.MOTION_DETECTION,
    Capability.CAMERA_WDR,
    Capability.CAMERA_FULL_COLOR,
    Capability.CAMERA_TRACKING,
    Capability.CAMERA_WATERMARK,
    Capability.CARPET_BOOST,
    Capability.AUTO_DUST_ARREST,
    Capability.AUTO_MOP_DRY,
    Capability.NO_DISTURB,
    Capability.AI_CLEANING,
    Capability.SLEEP_MODE,
    Capability.MITE_REMOVAL,
    Capability.ALARM,
    Capability.FILTER_RESET,
}
INT_CAPABILITIES = {
    Capability.FAN_SPEED,
    Capability.AIR_QUALITY,
    Capability.FILTER_LIFE,
    Capability.BRIGHTNESS,
    Capability.COLOR_TEMP,
    Capability.BATTERY,
    Capability.FILTER_DAYS,
}


def create_app(gateway: Any) -> FastAPI:
    app = FastAPI(title="HomeKit One", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def ajax_redirects(request: Request, call_next):
        response = await call_next(request)
        if (
            request.headers.get("x-homeone-ajax")
            and response.status_code in (302, 303)
            and "location" in response.headers
        ):
            location = response.headers["location"]
            params = parse_qs(urlparse(location).query)
            message = (params.get("message") or [""])[0]
            error = (params.get("error") or [""])[0]
            return JSONResponse(
                {
                    "ok": not error,
                    "message": message,
                    "error": error,
                    "location": location,
                }
            )
        return response

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
                "provider_labels": PROVIDER_LABELS,
                "provider_enabled": _enabled_map(gateway),
                "cap_units": CAP_UNITS,
                "mode_options": MODE_OPTIONS,
                "mode_labels": MODE_LABELS,
                "bool_capabilities": {str(c) for c in BOOL_CAPABILITIES},
                "int_capabilities": {str(c) for c in INT_CAPABILITIES},
                "setup_dirty": bool(getattr(gateway, "setup_dirty", False)),
                "demo_mode": _demo_mode(gateway),
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

    @app.get("/devices/{device_id}", response_class=HTMLResponse)
    async def device_detail(request: Request, device_id: str) -> HTMLResponse:
        device = gateway.manager.get(device_id)
        if device is None:
            raise HTTPException(404, "设备不存在")
        return render(
            request,
            "device.html",
            device=device,
            message=request.query_params.get("message"),
            error=request.query_params.get("error"),
        )

    @app.post("/devices/{device_id}/refresh")
    async def refresh_device_page(device_id: str) -> RedirectResponse:
        try:
            await gateway.manager.refresh_device(device_id)
        except KeyError:
            return _redirect("/", error="设备不存在")
        except Exception as exc:  # noqa: BLE001
            return _redirect(_device_path(device_id), error=f"刷新失败: {exc}")
        return _redirect(_device_path(device_id), message="已刷新")

    @app.post("/devices/{device_id}/set")
    async def set_device_page(
        device_id: str,
        capability: str = Form(...),
        value: str = Form(...),
    ) -> RedirectResponse:
        try:
            cap = Capability(capability)
        except ValueError:
            return _redirect(_device_path(device_id), error="未知 capability")
        try:
            await gateway.manager.set_capability(
                device_id, cap, _coerce(cap, value)
            )
        except KeyError:
            return _redirect("/", error="设备不存在")
        except Exception as exc:  # noqa: BLE001
            return _redirect(
                _device_path(device_id),
                error=f"设置 {CAP_LABELS.get(str(cap), cap)} 失败: {exc}",
            )
        return _redirect(
            _device_path(device_id),
            message=f"{CAP_LABELS.get(str(cap), cap)} 已设置为 {value}",
        )

    @app.get("/setup", response_class=HTMLResponse)
    async def setup(request: Request) -> HTMLResponse:
        mijia_entries = gateway.registry.mijia_entries()
        mijia_tokens = gateway.secrets.get("mijia", "tokens", default={}) or {}
        configured_tokens = {
            item.id for item in gateway.config.mijia.devices if item.token
        }
        token_ids = set(mijia_tokens) if isinstance(mijia_tokens, dict) else set()
        token_ids |= configured_tokens
        oauth = gateway.secrets.section("mijia_oauth")
        return render(
            request,
            "setup.html",
            mijia_devices=mijia_entries,
            mijia_tokens=token_ids,
            mijia_cloud_saved=bool(
                gateway.secrets.get("mijia_cloud", "username")
            ),
            mijia_oauth_saved=bool(
                oauth.get("refresh_token") or oauth.get("access_token")
            ),
            mijia_country=(
                gateway.secrets.get("mijia_cloud", "country")
                or gateway.config.mijia_cloud.country
                or "cn"
            ),
            treeow_saved=bool(gateway.secrets.get("treeow", "account")),
            treeow_account=(
                gateway.secrets.get("treeow", "account")
                or gateway.config.treeow.account
                or ""
            ),
            reports=getattr(gateway, "setup_reports", {}) or {},
            provider_errors={
                provider.name: getattr(provider, "last_error", None)
                for provider in getattr(gateway, "providers", [])
                if getattr(provider, "last_error", None)
            },
            allow_lists={
                "mijia_cloud": list(gateway.config.mijia_cloud.devices),
                "treeow": list(gateway.config.treeow.devices),
            },
            discovery=getattr(gateway, "mijia_discovery", None),
            supported_models=sorted(MODELS),
            plugin_cards=[
                plugin
                for plugin in plugins()
                if plugin.setup_fields and not plugin.builtin
            ],
            message=request.query_params.get("message"),
            error=request.query_params.get("error"),
        )

    @app.post("/setup/mijia-cloud")
    async def setup_mijia_cloud(
        username: str = Form(""),
        password: str = Form(""),
        country: str = Form("cn"),
        verify: str = Form(""),
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
        _mark_setup_changed(gateway)
        saved = "米家云凭据已保存"
        if verify:
            report = await _probe_and_store(gateway, "mijia_cloud")
            return _setup_result(gateway, report, saved)
        return _redirect(
            "/setup",
            message=f"{saved}，重启网关后生效（也可以点「验证」先检查账号）",
        )

    @app.post("/setup/treeow")
    async def setup_treeow(
        account: str = Form(""),
        password: str = Form(""),
        verify: str = Form(""),
    ) -> RedirectResponse:
        if account.strip():
            gateway.secrets.set("treeow", "account", account.strip())
        if password:
            gateway.secrets.set("treeow", "password", password)
        if not gateway.secrets.get("treeow", "account") or not gateway.secrets.get("treeow", "password"):
            return _redirect("/setup", error="树新风账号和密码都需要填写")
        set_provider_enabled(gateway.config.path, "treeow", True)
        _mark_setup_changed(gateway)
        saved = "树新风凭据已保存"
        if verify:
            report = await _probe_and_store(gateway, "treeow")
            return _setup_result(gateway, report, saved)
        return _redirect("/setup", message=f"{saved}，重启网关后生效")

    @app.post("/setup/mijia")
    async def setup_mijia(
        device_id: str = Form(...),
        name: str = Form(""),
        model: str = Form(...),
        host: str = Form(...),
        token: str = Form(""),
        verify: str = Form(""),
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
        token = token.strip()
        if token:
            tokens = dict(gateway.secrets.get("mijia", "tokens", default={}) or {})
            tokens[device_id] = token
            gateway.secrets.set("mijia", "tokens", tokens)
        set_provider_enabled(gateway.config.path, "mijia", True)
        _mark_setup_changed(gateway)
        if verify:
            saved_token = token
            if not saved_token:
                from homekit_one.providers.mijia.provider import MijiaProvider

                configured = next(
                    (
                        item.token
                        for item in gateway.config.mijia.devices
                        if str(item.id) == device_id
                    ),
                    None,
                )
                saved_token = (
                    MijiaProvider(
                        gateway.config.mijia,
                        gateway.secrets,
                        gateway.config.state_dir,
                        gateway.registry,
                    )._token(device_id, configured)
                    or ""
                )
            report = await probe_mijia_device(
                device_id, host, saved_token, model, name=entry["name"]
            )
            _store_report(gateway, report)
            return _setup_result(gateway, report, "米家设备已保存")
        return _redirect("/setup", message="米家设备已保存，重启网关后生效")

    @app.post("/setup/mijia/remove")
    async def remove_mijia(device_id: str = Form(...)) -> RedirectResponse:
        gateway.registry.remove_mijia(device_id)
        tokens = dict(gateway.secrets.get("mijia", "tokens", default={}) or {})
        tokens.pop(device_id, None)
        gateway.secrets.set("mijia", "tokens", tokens)
        _mark_setup_changed(gateway)
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
            _mark_setup_changed(gateway)
        return _redirect("/setup", message=_import_summary(found, "米家"))

    @app.post("/setup/mijia/import-oauth")
    async def import_mijia_oauth() -> RedirectResponse:
        """Import LAN tokens with the already-authorized Xiaomi OAuth session."""
        oauth = gateway.secrets.section("mijia_oauth")
        if not (oauth.get("access_token") or oauth.get("refresh_token")):
            return _redirect(
                "/setup",
                error="还没有小米 OAuth 授权；先完成「小米 OAuth 授权」再导入",
            )
        from homekit_one.providers.mijia_cloud.client import (
            MijiaCloudAuthError,
            MijiaCloudError,
        )

        country = (
            gateway.secrets.get("mijia_cloud", "country")
            or gateway.config.mijia_cloud.country
            or "cn"
        ).lower()
        client = XiaomiOAuthClient(
            country,
            gateway.config.state_dir,
            tokens=oauth,
            on_tokens=lambda values: gateway.secrets.set_many("mijia_oauth", values),
        )
        try:
            await client.login()
            found, total = await import_devices_from_oauth(client)
        except (MijiaCloudAuthError, MijiaCloudError) as exc:
            return _redirect("/setup", error=f"小米云导入失败: {exc}")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("小米 OAuth 导入失败")
            return _redirect("/setup", error=f"小米云导入失败: {exc}")
        finally:
            await client.close()

        tokens = dict(gateway.secrets.get("mijia", "tokens", default={}) or {})
        for item in found:
            token = item.pop("token", None)
            gateway.registry.upsert_mijia(item)
            if token:
                tokens[str(item["id"])] = token
        gateway.secrets.set("mijia", "tokens", tokens)
        if found:
            set_provider_enabled(gateway.config.path, "mijia", True)
            _mark_setup_changed(gateway)
        summary = _import_summary(found, "米家")
        if total > len(found):
            summary += f"；账号下另有 {total - len(found)} 台没返回局域网 token/IP，需手动添加"
        return _redirect("/setup", message=summary)

    # ---- Xiaomi OAuth --------------------------------------------------
    async def _oauth_page_context(
        country: str = "", redirect_uri: str = "", device_id: str = ""
    ) -> dict[str, Any]:
        oauth = gateway.secrets.section("mijia_oauth")
        country = (
            country
            or gateway.secrets.get("mijia_cloud", "country")
            or gateway.config.mijia_cloud.country
            or "cn"
        ).lower()
        device_id = str(device_id or oauth.get("device_id") or generate_device_id())
        redirect_uri = (
            redirect_uri
            or str(oauth.get("redirect_uri") or "")
            or DEFAULT_REDIRECT_URI
        )
        client = XiaomiOAuthClient(
            country,
            gateway.config.state_dir,
            tokens=oauth,
            device_id=device_id,
            redirect_uri=redirect_uri,
        )
        try:
            auth_url = client.auth_url()
        finally:
            await client.close()
        return {
            "auth_url": auth_url,
            "country": country,
            "device_id": device_id,
            "redirect_uri": redirect_uri,
        }

    @app.get("/setup/mijia-cloud/oauth", response_class=HTMLResponse)
    async def mijia_oauth_page(
        request: Request,
        country: str = "",
        redirect_uri: str = "",
        device_id: str = "",
    ) -> HTMLResponse:
        return render(
            request,
            "oauth.html",
            **await _oauth_page_context(
                country.strip(), redirect_uri.strip(), device_id.strip()
            ),
            message=request.query_params.get("message"),
            error=request.query_params.get("error"),
        )

    def _oauth_retry_path(
        country: str, device_id: str, redirect_uri: str
    ) -> str:
        query = urlencode(
            {
                "country": country or "cn",
                "device_id": device_id,
                "redirect_uri": redirect_uri or DEFAULT_REDIRECT_URI,
            }
        )
        return f"/setup/mijia-cloud/oauth?{query}"

    @app.post("/setup/mijia-cloud/oauth/callback")
    async def mijia_oauth_callback(
        callback: str = Form(...),
        device_id: str = Form(""),
        country: str = Form("cn"),
        redirect_uri: str = Form(""),
    ) -> RedirectResponse:
        retry_path = _oauth_retry_path(country, device_id, redirect_uri)
        if not device_id:
            return _redirect(
                "/setup/mijia-cloud/oauth",
                error="授权会话已失效，请重新打开授权页",
            )
        try:
            code, state = parse_callback(callback)
        except ValueError as exc:
            return _redirect(retry_path, error=str(exc))
        if state and state != state_for(device_id):
            return _redirect(
                retry_path,
                error="state 不匹配：可能粘贴了旧的授权回调，请重新授权",
            )
        client = XiaomiOAuthClient(
            (country or "cn").lower(),
            gateway.config.state_dir,
            tokens=gateway.secrets.section("mijia_oauth"),
            device_id=device_id,
            redirect_uri=redirect_uri or DEFAULT_REDIRECT_URI,
        )
        try:
            await client.exchange_code(code)
        except Exception as exc:  # noqa: BLE001 - surface the provider error
            return _redirect(retry_path, error=f"授权失败: {exc}")
        finally:
            await client.close()

        gateway.secrets.set_many("mijia_oauth", client.tokens())
        gateway.secrets.set("mijia_cloud", "country", (country or "cn").lower())
        set_provider_enabled(gateway.config.path, "mijia_cloud", True)
        _mark_setup_changed(gateway)
        report = await _probe_and_store(gateway, "mijia_cloud")
        return _setup_result(gateway, report, "小米 OAuth 授权成功")

    @app.post("/setup/mijia-cloud/oauth/clear")
    async def mijia_oauth_clear() -> RedirectResponse:
        gateway.secrets.delete_section("mijia_oauth")
        has_password = bool(
            gateway.secrets.get("mijia_cloud", "username")
            and gateway.secrets.get("mijia_cloud", "password")
        )
        if not has_password:
            set_provider_enabled(gateway.config.path, "mijia_cloud", False)
        _mark_setup_changed(gateway)
        return _redirect("/setup", message="小米 OAuth 授权已清除，重启网关后生效")

    # ---- Mijia LAN discovery -------------------------------------------
    @app.post("/setup/mijia/discover")
    async def mijia_discover() -> RedirectResponse:
        from homekit_one.providers.mijia.discovery import discover

        try:
            found = await discover(duration=3.0)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception("米家局域网发现失败")
            return _redirect("/setup", error=f"局域网扫描失败: {exc}")
        gateway.mijia_discovery = [
            {
                "host": device.host,
                "id": device.did,
                "hex_id": device.hex_id,
                "token": device.token or "",
            }
            for device in found
        ]
        if not found:
            return _redirect(
                "/setup",
                error=(
                    "没有发现米家设备。确认 Mac 与设备在同一网段，并允许 Python/终端"
                    "使用「本地网络」；也可以在「系统设置 → 隐私与安全性 → 本地网络」里检查。"
                ),
            )
        return _redirect("/setup", message=f"发现 {len(found)} 台米家设备，可在下方填入表单")

    # ---- external provider plugins -------------------------------------
    @app.post("/setup/plugin/{plugin_name}")
    async def setup_plugin(plugin_name: str, request: Request) -> RedirectResponse:
        plugin = get_plugin(plugin_name)
        if plugin is None or plugin.builtin:
            raise HTTPException(404, "未知 provider 插件")
        form = await request.form()
        values: dict[str, str] = {}
        missing: list[str] = []
        for field in plugin.setup_fields:
            value = str(form.get(field.key, "")).strip()
            if value:
                values[field.key] = value
            elif field.default and not gateway.secrets.get(plugin_name, field.key):
                values[field.key] = field.default
            elif field.required and not gateway.secrets.get(plugin_name, field.key):
                missing.append(field.label)
        if missing:
            return _redirect(
                "/setup", error=f"{plugin.label} 还需要填写：{'、'.join(missing)}"
            )
        if values:
            gateway.secrets.set_many(plugin_name, values)
        set_provider_enabled(gateway.config.path, plugin_name, True)
        _mark_setup_changed(gateway)
        report = await _probe_and_store(gateway, plugin_name)
        return _setup_result(gateway, report, f"{plugin.label} 凭据已保存")

    # ---- provider actions ----------------------------------------------
    @app.post("/setup/{provider}/devices")
    async def save_provider_devices(provider: str, request: Request) -> RedirectResponse:
        if provider not in PROVIDER_LABELS:
            raise HTTPException(404, "未知 provider")
        form = await request.form()
        selected = [
            str(value).strip()
            for value in form.getlist("devices")
            if str(value).strip()
        ]
        reports = getattr(gateway, "setup_reports", {}) or {}
        report = reports.get(provider)
        all_selected = bool(
            report
            and report.devices
            and set(selected) == {device.id for device in report.devices}
        )
        if all_selected:
            # An empty allow-list means "all devices", so newly added devices
            # are picked up automatically instead of being frozen out.
            selected = []
        set_provider_devices(gateway.config.path, provider, selected)
        _mark_setup_changed(gateway)
        if selected:
            message = (
                f"{PROVIDER_LABELS[provider]} 白名单已保存（{len(selected)} 台），"
                "重启网关后生效"
            )
        elif all_selected:
            message = (
                f"{PROVIDER_LABELS[provider]} 已选择全部受支持设备，"
                "重启网关后生效"
            )
        else:
            message = (
                f"{PROVIDER_LABELS[provider]} 白名单已清空，"
                "重启网关后会接入账号下全部受支持设备"
            )
        return _redirect("/setup", message=message)

    @app.post("/setup/{provider}/verify")
    async def verify_provider(provider: str) -> RedirectResponse:
        if provider not in PROVIDER_LABELS:
            raise HTTPException(404, "未知 provider")
        report = await _probe_and_store(gateway, provider)
        return _setup_result(
            gateway,
            report,
            f"{PROVIDER_LABELS[provider]} 验证完成",
        )

    @app.post("/setup/{provider}/enable")
    async def enable_provider(provider: str) -> RedirectResponse:
        if provider not in PROVIDER_LABELS:
            raise HTTPException(404, "未知 provider")
        set_provider_enabled(gateway.config.path, provider, True)
        _mark_setup_changed(gateway)
        return _redirect(
            "/setup",
            message=f"{PROVIDER_LABELS[provider]} 已启用，重启网关后生效",
        )

    @app.post("/setup/{provider}/disable")
    async def disable_provider(provider: str) -> RedirectResponse:
        if provider not in PROVIDER_LABELS:
            raise HTTPException(404, "未知 provider")
        set_provider_enabled(gateway.config.path, provider, False)
        _mark_setup_changed(gateway)
        return _redirect(
            "/setup",
            message=f"{PROVIDER_LABELS[provider]} 已停用，重启网关后生效",
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


def _demo_mode(gateway: Any) -> bool:
    running = getattr(gateway, "providers", None)
    if running is not None:
        return any(provider.name == "fake" for provider in running)
    fake = getattr(gateway.config, "fake", None)
    return bool(fake and fake.enabled)


def _enabled_map(gateway: Any) -> dict[str, bool]:
    # Prefer what the running process actually built: after the user saves a
    # new provider, config/secrets already changed but the process is stale.
    running = getattr(gateway, "providers", None)
    if running is not None:
        names = {provider.name for provider in running}
        return {name: name in names for name in PROVIDER_LABELS}
    config = gateway.config
    registry = getattr(gateway, "registry", None)
    result: dict[str, bool] = {}
    for name in PROVIDER_LABELS:
        plugin = get_plugin(name)
        result[name] = plugin.is_enabled(config, registry) if plugin else False
    return result


def _mark_setup_changed(gateway: Any) -> None:
    marker = getattr(gateway, "mark_setup_changed", None)
    if callable(marker):
        marker()
    else:  # test stubs and older gateways
        gateway.setup_dirty = True


def _store_report(gateway: Any, report: ProviderReport) -> ProviderReport:
    reports = getattr(gateway, "setup_reports", None)
    if not isinstance(reports, dict):
        reports = {}
        gateway.setup_reports = reports
    reports[report.provider] = report
    return report


async def _probe_and_store(gateway: Any, provider: str) -> ProviderReport:
    report = await probe_provider(
        provider,
        gateway.config,
        gateway.secrets,
        gateway.config.state_dir,
        getattr(gateway, "registry", None),
    )
    return _store_report(gateway, report)


def _setup_result(
    gateway: Any, report: ProviderReport, saved_message: str
) -> RedirectResponse:
    if report.ok:
        return _redirect("/setup", message=f"{saved_message}；{report.summary}")
    detail = report.warnings[0] if report.warnings else report.summary
    return _redirect(
        "/setup",
        error=f"{saved_message}，但验证失败：{_clip(detail)}",
    )


def _import_summary(found: list[dict[str, Any]], brand: str) -> str:
    if not found:
        return f"没有从{brand}导入设备（账号下没有返回局域网 token 的设备）"
    supported = [item for item in found if str(item.get("model") or "") in MODELS]
    unsupported = len(found) - len(supported)
    missing_host = [item for item in found if not item.get("host")]
    message = f"已从{brand}导入 {len(found)} 台设备"
    if supported:
        message += f"，其中 {len(supported)} 台型号已支持"
    if unsupported:
        message += f"；{unsupported} 台型号暂未支持（记录已保存，但不会出现在 HomeKit）"
    if missing_host:
        message += f"；{len(missing_host)} 台没有返回 IP，需要手动补 IP"
    return message + "，重启网关后生效"


def _clip(text: str, limit: int = 180) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


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


def _device_path(device_id: str) -> str:
    return f"/devices/{quote(device_id, safe='')}"


def _redirect(path: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    query = ""
    if message:
        query = f"message={quote(message)}"
    elif error:
        query = f"error={quote(error)}"
    if query:
        path = f"{path}{'&' if '?' in path else '?'}{query}"
    return RedirectResponse(path, status_code=303)
