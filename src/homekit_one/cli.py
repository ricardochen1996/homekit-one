"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import socket
import sys
import webbrowser

from homekit_one import __version__
from homekit_one.config import ConfigError, load_config
from homekit_one.log import setup_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="homekit-one", description="米家 / 树新风 HomeKit 网关")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_run_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--config", help="配置文件路径")
        target.add_argument("--no-homekit", action="store_true", help="只启动 Web UI，不启动 HomeKit")
        target.add_argument("--web-port", type=int, help="覆盖 Web 端口")
        target.add_argument("--web-host", help="覆盖 Web 监听地址")
        target.add_argument("--open", action="store_true", help="启动后打开配置页")

    run = sub.add_parser("run", help="启动网关")
    add_run_args(run)

    setup = sub.add_parser("setup", help="启动网关并打开接入配置页（适合第一次使用）")
    add_run_args(setup)

    disc = sub.add_parser("discover", help="广播发现局域网米家设备")
    disc.add_argument("--timeout", type=float, default=3.0)

    cloud = sub.add_parser(
        "mijia-cloud-list",
        help="用小米账号列出云端设备（含中枢子设备）和 MIoT 属性",
    )
    cloud.add_argument("--config", help="配置文件路径")
    cloud.add_argument(
        "--username", "-u", help="小米账号：手机号或邮箱（不要用数字小米 ID）"
    )
    cloud.add_argument("--password", "-p", help="留空则交互式输入")
    cloud.add_argument("--country", default=None, help="小米云区域，默认 cn")
    cloud.add_argument("--specs", action="store_true", help="读取并显示 MIoT 映射")
    cloud.add_argument("--props", action="store_true", help="显示每个设备的原始属性")
    cloud.add_argument(
        "--actions", action="store_true", help="显示 MIoT actions/events（评估支持用）"
    )
    cloud.add_argument("--json", action="store_true", help="输出 JSON")
    cloud.add_argument(
        "--oauth", action="store_true", help="强制使用 OAuth token（默认有 token 时自动使用）"
    )

    oauth = sub.add_parser(
        "mijia-oauth-login",
        help="浏览器 OAuth 授权小米账号（推荐，绕开密码登录限制）",
    )
    oauth.add_argument("--config", help="配置文件路径")
    oauth.add_argument("--country", default=None, help="小米云区域，默认 cn")
    oauth.add_argument(
        "--redirect-uri",
        default=None,
        help="OAuth 回调地址，默认 http://homeassistant.local:8123",
    )
    oauth.add_argument("--device-id", default=None, help="固定 device_id，一般不用改")
    oauth.add_argument("--no-browser", action="store_true", help="只打印授权地址")
    oauth.add_argument("--code", default=None, help="直接提供回调地址或 code（适合脚本/剪贴板）")

    treeow = sub.add_parser(
        "treeow-list", help="登录 Treeow 云，列出设备、原始属性和映射结果"
    )
    treeow.add_argument("--config", help="配置文件路径")
    treeow.add_argument("--account", "-u", help="Treeow Home 账号（手机号）")
    treeow.add_argument("--password", "-p", help="留空则交互式输入")
    treeow.add_argument(
        "--relogin", action="store_true", help="忽略已保存密码，重新交互输入"
    )
    treeow.add_argument("--save", action="store_true", help="保存账号并启用 treeow provider")
    treeow.add_argument("--json", action="store_true", help="输出 JSON")

    imp = sub.add_parser("mijia-import", help="从小米云导入设备 token")
    imp.add_argument("--config", help="配置文件路径")
    imp.add_argument("--username", "-u")
    imp.add_argument("--password", "-p")
    imp.add_argument("--country", default=None, help="默认使用已保存/配置的区域")
    imp.add_argument(
        "--oauth",
        action="store_true",
        help="使用已保存的 OAuth 授权导入，不需要账号密码（推荐）",
    )

    pair = sub.add_parser("pairing", help="显示 HomeKit 配对码和 URI")
    pair.add_argument("--config", help="配置文件路径")

    doctor = sub.add_parser("doctor", help="检查配置、端口、状态目录和 provider 凭据")
    doctor.add_argument("--config", help="配置文件路径")
    doctor.add_argument("--probe", action="store_true", help="实际登录并列出每个已启用 provider 的设备")

    args = parser.parse_args(argv)

    if args.command == "run":
        _run(args, open_browser=bool(getattr(args, "open", False)))
    elif args.command == "setup":
        _run(args, open_browser=True)
    elif args.command == "discover":
        asyncio.run(_discover(args.timeout))
    elif args.command == "mijia-cloud-list":
        asyncio.run(_mijia_cloud_list(args))
    elif args.command == "mijia-oauth-login":
        asyncio.run(_mijia_oauth_login(args))
    elif args.command == "treeow-list":
        asyncio.run(_treeow_list(args))
    elif args.command == "mijia-import":
        asyncio.run(_mijia_import(args))
    elif args.command == "pairing":
        asyncio.run(_pairing(args.config))
    elif args.command == "doctor":
        _doctor(args)


def _load(path: str | None):
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _clipboard_callback() -> str:
    """Read a copied OAuth callback from the macOS clipboard, if it looks sane."""
    if sys.platform != "darwin":
        return ""
    try:
        import subprocess

        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=2, check=False
        )
    except Exception:  # noqa: BLE001 - clipboard access is best-effort
        return ""
    value = result.stdout.strip()
    if value.startswith(("http://", "https://")) and "code=" in value:
        return value
    return ""


def _run(args: argparse.Namespace, *, open_browser: bool = False) -> None:
    config = _load(args.config)
    if args.web_port:
        from dataclasses import replace

        config = replace(
            config,
            gateway=replace(
                config.gateway,
                web_port=args.web_port,
                web_host=args.web_host or config.gateway.web_host,
            ),
        )
    elif args.web_host:
        from dataclasses import replace

        config = replace(config, gateway=replace(config.gateway, web_host=args.web_host))
    setup_logging(config.gateway.log_level)

    from homekit_one.app import Gateway

    gateway = Gateway(config, no_homekit=args.no_homekit)
    web_host = config.gateway.web_host
    if web_host in ("0.0.0.0", "::"):
        web_host = "127.0.0.1"
    url = f"http://{web_host}:{config.gateway.web_port}/setup"
    print(f"Web 配置页: {url}")
    print(f"HomeKit 配对码: homekit-one pairing   或打开 {url.replace('/setup', '/pairing')}")
    if open_browser:

        async def _serve() -> None:
            async def _open() -> None:
                await asyncio.sleep(1.2)
                try:
                    await asyncio.to_thread(webbrowser.open, url)
                except Exception:  # noqa: BLE001 - opening a browser is best-effort
                    pass

            asyncio.create_task(_open())
            await gateway.serve()

        try:
            asyncio.run(_serve())
        except KeyboardInterrupt:
            pass
        return

    try:
        asyncio.run(gateway.serve())
    except KeyboardInterrupt:
        pass


async def _discover(timeout: float) -> None:
    from homekit_one.providers.mijia.discovery import discover

    devices = await discover(duration=timeout)
    if not devices:
        print("没有发现设备。确认 Mac 与设备在同一网段，且设备已配网。")
        print("提示：在系统设置 → 隐私与安全性 → 本地网络里允许 Python/终端。")
        return
    print(f"{'IP':<16} {'did':<12} {'hex':<12} token")
    for device in devices:
        print(
            f"{device.host:<16} {device.did:<12} {device.hex_id:<12} "
            f"{device.token or '(未暴露，需小米云导入)'}"
        )
    print()
    print("下一步：打开 Web 配置页的「米家局域网设备」，点「填入表单」并选择型号。")
    print("如果不方便开 Web，也可以把 IP/did/token 填进 config.toml 的 providers.mijia.devices。")


async def _mijia_import(args: argparse.Namespace) -> None:
    config = _load(args.config)
    setup_logging("INFO")
    from homekit_one.registry import DeviceRegistry
    from homekit_one.secrets import SecretsStore

    secrets = SecretsStore(config.state_dir / "secrets.json")
    oauth_tokens = secrets.section("mijia_oauth")
    use_oauth = bool(
        args.oauth
        or (
            not args.username
            and not args.password
            and (oauth_tokens.get("access_token") or oauth_tokens.get("refresh_token"))
        )
    )

    if use_oauth:
        if not (oauth_tokens.get("access_token") or oauth_tokens.get("refresh_token")):
            print(
                "还没有小米 OAuth 授权。先运行 homekit-one mijia-oauth-login，"
                "或在 Web 配置页完成授权。",
                file=sys.stderr,
            )
            raise SystemExit(1)
        from homekit_one.providers.mijia.token_import import import_devices_from_oauth
        from homekit_one.providers.mijia_cloud.client import MijiaCloudError
        from homekit_one.providers.mijia_cloud.oauth import XiaomiOAuthClient

        country = (
            args.country
            or secrets.get("mijia_cloud", "country")
            or config.mijia_cloud.country
            or "cn"
        ).lower()
        client = XiaomiOAuthClient(
            country,
            config.state_dir,
            tokens=oauth_tokens,
            on_tokens=lambda values: secrets.set_many("mijia_oauth", values),
        )
        try:
            await client.login()
            found, total = await import_devices_from_oauth(client)
        except MijiaCloudError as exc:
            print(f"导入失败: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        finally:
            await client.close()
        print(f"小米 OAuth 账号下共 {total} 台设备，其中 {len(found)} 台返回了局域网 token")
    else:
        from homekit_one.providers.mijia.token_import import TokenImportError, import_devices

        username = args.username or input("小米账号: ").strip()
        password = args.password or getpass.getpass("密码: ")
        country = args.country or "cn"
        try:
            found = await asyncio.to_thread(
                import_devices, username, password, country
            )
        except TokenImportError as exc:
            print(f"导入失败: {exc}", file=sys.stderr)
            print(
                "提示：小米已限制旧版密码登录。可以先用 homekit-one mijia-oauth-login "
                "授权，再用 homekit-one mijia-import --oauth。",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc

    state_dir = config.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    registry = DeviceRegistry(state_dir / "devices.json")
    tokens = dict(secrets.get("mijia", "tokens", default={}) or {})
    for item in found:
        token = item.pop("token", None)
        registry.upsert_mijia(item)
        if token:
            tokens[str(item["id"])] = token
    secrets.set("mijia", "tokens", tokens)
    if found:
        from homekit_one.config import set_provider_enabled

        set_provider_enabled(config.path, "mijia", True)
    from homekit_one.providers.mijia.models import MODELS

    supported = [item for item in found if str(item.get("model") or "") in MODELS]
    unsupported = [item for item in found if str(item.get("model") or "") not in MODELS]
    print(f"已导入 {len(found)} 台设备到 {registry.path}")
    for item in supported:
        print(f"  ✓ {item['id']}  {item['name']}  {item['model']}  {item.get('host', '')}")
    for item in unsupported:
        print(f"  · {item['id']}  {item['name']}  {item['model'] or '(未知型号)'}  （暂不支持）")
    if unsupported:
        print(
            f"\n{len(unsupported)} 台型号暂未支持，记录已保存但不会出现在 HomeKit；"
            "可在 src/homekit_one/providers/mijia/models.py 补映射。"
        )
    if not found:
        print("没有设备返回局域网 token；可以在 Web 配置页手动填写 IP + token。")
    print("重启网关后生效。")


async def _pairing(config_path: str | None) -> None:
    config = _load(config_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    from homekit_one.core.manager import DeviceManager
    from homekit_one.homekit.bridge import HomeKitBridge

    manager = DeviceManager(config, [])
    bridge = HomeKitBridge(config, manager)
    info = bridge.pairing_info()
    print(f"名称: {info['name']}")
    print(f"配对码: {info['pincode']}")
    print(f"URI: {info['uri']}")
    print(f"端口: {info['port']}")
    print(f"状态: {'已配对' if info['paired'] else '未配对'}")
    await bridge.stop()


def _doctor(args: argparse.Namespace) -> None:
    problems: list[str] = []
    config = _load(args.config)
    print(f"配置: {config.path or '(未找到，使用默认值)'}")
    print(f"状态目录: {config.state_dir}")
    try:
        config.state_dir.mkdir(parents=True, exist_ok=True)
        probe = config.state_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        print("状态目录: 可写")
    except OSError as exc:
        problems.append(f"状态目录不可写: {exc}")

    for label, port in (("Web", config.gateway.web_port), ("HomeKit", config.gateway.homekit_port)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((config.gateway.web_host if label == "Web" else "0.0.0.0", port))
            print(f"{label} 端口 {port}: 可用")
        except OSError as exc:
            problems.append(f"{label} 端口 {port} 不可用: {exc}")
        finally:
            sock.close()

    state = config.state_dir / "accessory.state"
    if state.exists():
        try:
            data = json.loads(state.read_text("utf-8"))
            paired = bool(data.get("paired_clients"))
            print(f"HomeKit 配对: {'已配对' if paired else '未配对'}")
        except (OSError, json.JSONDecodeError):
            problems.append("accessory.state 存在但无法解析；删除后重新配对")
    else:
        print("HomeKit 配对: 尚未生成（首次启动时生成）")

    from homekit_one.registry import DeviceRegistry
    from homekit_one.secrets import SecretsStore

    secrets = SecretsStore(config.state_dir / "secrets.json")
    registry = DeviceRegistry(config.state_dir / "devices.json")

    print("Provider:")
    checks = _provider_checks(config, secrets, registry)
    enabled_names: list[str] = []
    for check in checks:
        if check["enabled"]:
            enabled_names.append(check["name"])
        status = "已启用" if check["enabled"] else "未启用"
        print(f"  {check['label']}: {status} · {check['credentials']}")
        if check["enabled"] and not check["ready"]:
            problems.append(f"{check['label']} 已启用但{check['credentials']}")
        if not check["enabled"] and check["has_credentials"]:
            print(
                f"    提示: 凭据已保存；在 Web 配置页点「启用已保存的{check['label']}」，"
                "或在 config.toml 里设置 enabled = true。"
            )

    if secrets.path.exists():
        mode = secrets.path.stat().st_mode & 0o777
        if mode != 0o600:
            problems.append(
                f"secrets.json 权限是 {oct(mode)}，建议 chmod 600 {secrets.path}"
            )
        else:
            print("secrets.json: 权限 0600")
    if config.fake.enabled and enabled_names:
        print("  演示 provider: 已启用（真实 provider 同时存在时会多出 4 台虚拟设备）")

    if problems:
        print("\n问题:")
        for problem in problems:
            print(f"  - {problem}")
        if not args.probe:
            print("\n检查未通过。修复后可加 --probe 实际登录验证。")
        raise SystemExit(1)

    print("\n检查通过。")
    if args.probe:
        print()
        asyncio.run(_doctor_probe(config, secrets, registry, enabled_names))
    else:
        print("提示: 加 --probe 会实际登录每个已启用 provider 并列出设备。")


def _provider_checks(config, secrets, registry) -> list[dict[str, object]]:
    mijia_entries = registry.mijia_entries()
    oauth = secrets.section("mijia_oauth")
    checks = [
        {
            "name": "mijia",
            "label": "米家局域网",
            "enabled": bool(config.mijia.enabled or mijia_entries),
            "has_credentials": bool(mijia_entries or config.mijia.devices),
            "ready": bool(mijia_entries or config.mijia.devices),
            "credentials": (
                f"已添加 {len(mijia_entries) + len(config.mijia.devices)} 台设备"
                if (mijia_entries or config.mijia.devices)
                else "没有添加设备"
            ),
        },
        {
            "name": "mijia_cloud",
            "label": "米家云",
            "enabled": bool(config.mijia_cloud.enabled),
            "has_credentials": bool(
                oauth.get("refresh_token")
                or oauth.get("access_token")
                or (config.mijia_cloud.username or secrets.get("mijia_cloud", "username"))
            ),
            "ready": bool(
                oauth.get("refresh_token")
                or oauth.get("access_token")
                or (
                    (config.mijia_cloud.username or secrets.get("mijia_cloud", "username"))
                    and (
                        config.mijia_cloud.password
                        or secrets.get("mijia_cloud", "password")
                    )
                )
            ),
            "credentials": (
                "OAuth 已授权"
                if oauth.get("refresh_token") or oauth.get("access_token")
                else "已保存账号密码（密码登录可能被 403 拒绝，建议 OAuth）"
                if (config.mijia_cloud.username or secrets.get("mijia_cloud", "username"))
                else "没有凭据"
            ),
        },
        {
            "name": "treeow",
            "label": "树新风 Treeow",
            "enabled": bool(config.treeow.enabled),
            "has_credentials": bool(
                config.treeow.account or secrets.get("treeow", "account")
            ),
            "ready": bool(
                (config.treeow.account or secrets.get("treeow", "account"))
                and (config.treeow.password or secrets.get("treeow", "password"))
            ),
            "credentials": (
                "已保存账号密码"
                if (config.treeow.account or secrets.get("treeow", "account"))
                else "没有凭据"
            ),
        },
    ]
    from homekit_one.providers.plugins import plugins

    for plugin in plugins():
        if plugin.builtin:
            continue
        missing = [
            field.label
            for field in plugin.setup_fields
            if field.required
            and not secrets.get(plugin.name, field.key)
            and not (config.provider_data.get(plugin.name, {}) or {}).get(field.key)
        ]
        has_credentials = not plugin.setup_fields or any(
            secrets.get(plugin.name, field.key)
            or (config.provider_data.get(plugin.name, {}) or {}).get(field.key)
            for field in plugin.setup_fields
        )
        checks.append(
            {
                "name": plugin.name,
                "label": plugin.label,
                "enabled": plugin.is_enabled(config, registry),
                "has_credentials": has_credentials,
                "ready": not missing,
                "credentials": (
                    f"缺少：{'、'.join(missing)}"
                    if missing
                    else "凭据已保存"
                    if has_credentials
                    else "没有凭据"
                ),
            }
        )
    return checks


async def _doctor_probe(config, secrets, registry, names: list[str]) -> None:
    from homekit_one.providers.onboarding import probe_provider

    for name in names:
        report = await probe_provider(
            name, config, secrets, config.state_dir, registry
        )
        mark = {"ok": "✓", "warn": "!", "bad": "✗"}.get(report.level, "?")
        print(f"{mark} {report.label}: {report.summary}")
        if report.hint:
            print(f"    {report.hint}")
        for warning in report.warnings:
            print(f"    ! {warning}")
        for device in report.devices:
            state = (
                "状态正常"
                if device.reachable
                else "读取失败"
                if device.reachable is False
                else "未读取"
            )
            print(
                f"    - {device.name}  id={device.id}  {device.kind}"
                f"  {device.model}  {state}"
            )
            if device.error:
                print(f"      {device.error}")
    print()


async def _mijia_cloud_list(args: argparse.Namespace) -> None:
    config = _load(args.config)
    from homekit_one.providers.mijia_cloud.client import (
        MijiaCloudAuthError,
        MijiaCloudError,
        XiaomiCloudClient,
    )
    from homekit_one.providers.mijia_cloud.spec import build_cloud_spec
    from homekit_one.secrets import SecretsStore

    secrets = SecretsStore(config.state_dir / "secrets.json")
    country = (
        args.country
        or secrets.get("mijia_cloud", "country")
        or config.mijia_cloud.country
        or "cn"
    ).lower()
    oauth_tokens = secrets.section("mijia_oauth")
    use_oauth = bool(
        args.oauth
        or oauth_tokens.get("access_token")
        or oauth_tokens.get("refresh_token")
    )
    setup_logging("WARNING")
    if use_oauth:
        from homekit_one.providers.mijia_cloud.oauth import XiaomiOAuthClient

        client = XiaomiOAuthClient(
            country,
            config.state_dir,
            tokens=oauth_tokens,
            on_tokens=lambda values: secrets.set_many("mijia_oauth", values),
        )
    else:
        username = args.username or secrets.resolve(
            "mijia_cloud",
            "username",
            config.mijia_cloud.username,
            "HOMEKIT_ONE_MIJIA_CLOUD_USERNAME",
        )
        password = args.password or secrets.resolve(
            "mijia_cloud",
            "password",
            config.mijia_cloud.password,
            "HOMEKIT_ONE_MIJIA_CLOUD_PASSWORD",
        )
        if not username:
            username = input("小米账号: ").strip()
        if not password:
            password = getpass.getpass("密码: ")
        client = XiaomiCloudClient(username, password, country, config.state_dir)
    try:
        await client.login()
        records = await client.get_device_records()
        output: list[dict[str, object]] = []
        for record in records:
            did = str(record.get("did") or "")
            urn = str(record.get("spec_type") or record.get("specType") or "")
            item: dict[str, object] = {
                "did": did,
                "name": record.get("name"),
                "model": record.get("model"),
                "urn": urn,
                "online": record.get("isOnline", record.get("online")),
                "parent_id": record.get("parent_id") or record.get("parentId"),
            }
            spec = None
            if (args.specs or args.props or args.actions) and urn:
                try:
                    spec_data = await client.get_spec(urn)
                except MijiaCloudError as exc:
                    spec_data = None
                    item["spec_error"] = str(exc)
                if spec_data is not None:
                    if args.actions:
                        item["actions"] = _spec_actions(spec_data)
                        item["events"] = _spec_events(spec_data)
                    spec = build_cloud_spec(
                        spec_data, urn, str(record.get("model") or "")
                    )
                if spec is not None:
                    item["kind"] = str(spec.kind)
                    item["capabilities"] = sorted(str(c) for c in spec.capabilities)
                    if args.props:
                        item["properties"] = [
                            {
                                "capability": str(cap),
                                "siid": prop.siid,
                                "piid": prop.piid,
                                "format": prop.format,
                                "access": list(prop.access),
                                "description": prop.description,
                            }
                            for cap, prop in spec.props.items()
                        ]
            output.append(item)
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return
        print(f"区域 {country} · {len(output)} 台设备")
        for item in output:
            print(
                f"{str(item.get('did')):<16} {str(item.get('name') or ''):<18} "
                f"{str(item.get('model') or ''):<28} "
                f"online={item.get('online')} urn={item.get('urn')}"
            )
            if item.get("kind"):
                print(f"    kind={item['kind']} caps={','.join(item['capabilities'])}")
            if item.get("spec_error"):
                print(f"    spec_error={item['spec_error']}")
            for prop in item.get("properties") or []:
                print(
                    f"    {prop['capability']:<22} siid={prop['siid']} piid={prop['piid']} "
                    f"{prop['format']:<8} {','.join(prop['access'])}  {prop['description']}"
                )
            for action in item.get("actions") or []:
                print(
                    f"    action siid={action['siid']} aiid={action['aiid']} "
                    f"{action['type']}  {action['description']}"
                )
            for event in item.get("events") or []:
                print(
                    f"    event  siid={event['siid']} eiid={event['eiid']} "
                    f"{event['type']}  {event['description']}"
                )
    except MijiaCloudAuthError as exc:
        print(f"登录失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except MijiaCloudError as exc:
        print(f"小米云错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await client.close()


async def _mijia_oauth_login(args: argparse.Namespace) -> None:
    config = _load(args.config)
    from homekit_one.config import set_provider_enabled
    from homekit_one.providers.mijia_cloud.client import MijiaCloudAuthError, MijiaCloudError
    from homekit_one.providers.mijia_cloud.oauth import (
        DEFAULT_REDIRECT_URI,
        XiaomiOAuthClient,
        generate_device_id,
        parse_callback,
        state_for,
    )
    from homekit_one.secrets import SecretsStore

    secrets = SecretsStore(config.state_dir / "secrets.json")
    country = (
        args.country
        or secrets.get("mijia_cloud", "country")
        or config.mijia_cloud.country
        or "cn"
    ).lower()
    existing = secrets.section("mijia_oauth")
    device_id = (
        args.device_id or existing.get("device_id") or generate_device_id()
    )
    redirect_uri = (
        args.redirect_uri
        or existing.get("redirect_uri")
        or DEFAULT_REDIRECT_URI
    )
    client = XiaomiOAuthClient(
        country,
        config.state_dir,
        tokens=existing,
        device_id=device_id,
        redirect_uri=redirect_uri,
    )
    url = client.auth_url()
    print("1. 在浏览器打开下面的地址（已尝试自动打开）：")
    print(url)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    print()
    print("2. 用小米账号登录并授权。浏览器会跳转到")
    print(f"   {redirect_uri}")
    print("   页面打不开是正常的；从地址栏复制完整 URL（含 code=...），")
    print("   或直接粘贴 code。")
    if args.code:
        raw = args.code.strip()
    else:
        print("   小技巧：复制回调地址后直接回车，会自动读取 macOS 剪贴板。")
        try:
            raw = input("回调地址或 code: ").strip()
        except EOFError:
            raw = ""
        if not raw:
            raw = _clipboard_callback()
            if raw:
                print(f"已从剪贴板读取: {raw[:80]}{'…' if len(raw) > 80 else ''}")
    try:
        code, state = parse_callback(raw)
    except ValueError as exc:
        print(f"授权失败: {exc}", file=sys.stderr)
        await client.close()
        raise SystemExit(1) from exc
    expected = state_for(device_id)
    if state and state != expected:
        print(
            "state 不匹配：可能粘贴了旧的授权链接。请重新打开上面的地址。",
            file=sys.stderr,
        )
        await client.close()
        raise SystemExit(1)
    try:
        await client.exchange_code(code)
    except (MijiaCloudAuthError, MijiaCloudError) as exc:
        print(f"授权失败: {exc}", file=sys.stderr)
        await client.close()
        raise SystemExit(1) from exc
    tokens = client.tokens()
    secrets.set_many("mijia_oauth", tokens)
    secrets.set("mijia_cloud", "country", country)
    set_provider_enabled(config.path, "mijia_cloud", True)
    print()
    print("OAuth 授权成功，token 已保存到 secrets.json。")
    print(f"区域: {country} · device_id: {device_id}")
    print("下一步：")
    print("  homekit-one mijia-cloud-list --specs --props     # 查看云端设备")
    print("  homekit-one mijia-import --oauth                 # 顺便导入局域网 token")
    print("  或直接打开 Web 配置页，点「验证并列出设备」")
    await client.close()


def _spec_actions(data: dict) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for service in data.get("services") or []:
        siid = service.get("iid")
        for action in service.get("actions") or []:
            result.append(
                {
                    "siid": siid,
                    "aiid": action.get("iid"),
                    "type": action.get("type"),
                    "description": action.get("description"),
                    "in": action.get("in"),
                    "out": action.get("out"),
                }
            )
    return result


def _spec_events(data: dict) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for service in data.get("services") or []:
        siid = service.get("iid")
        for event in service.get("events") or []:
            result.append(
                {
                    "siid": siid,
                    "eiid": event.get("iid"),
                    "type": event.get("type"),
                    "description": event.get("description"),
                    "arguments": event.get("arguments"),
                }
            )
    return result


async def _treeow_list(args: argparse.Namespace) -> None:
    config = _load(args.config)
    from homekit_one.config import set_provider_enabled
    from homekit_one.providers.treeow.api import TreeowAPI, TreeowAuthError, TreeowError
    from homekit_one.providers.treeow.mapping import decode_props
    from homekit_one.providers.treeow.provider import _extract_props
    from homekit_one.secrets import SecretsStore

    secrets = SecretsStore(config.state_dir / "secrets.json")
    account = args.account or secrets.resolve(
        "treeow", "account", config.treeow.account, "HOMEKIT_ONE_TREOW_ACCOUNT"
    )
    password = args.password or (
        ""
        if args.relogin
        else secrets.resolve(
            "treeow",
            "password",
            config.treeow.password,
            "HOMEKIT_ONE_TREOW_PASSWORD",
        )
    )
    if not account:
        account = input("Treeow 账号: ").strip()
    if not password:
        password = getpass.getpass("密码: ")
    setup_logging("WARNING")

    api = TreeowAPI(account, password)
    try:
        await api.login()
        output: list[dict[str, object]] = []
        for home in await api.get_homes():
            for group in home.get("homeGroups") or []:
                group_id = str(group.get("id") or "") if isinstance(group, dict) else ""
                if not group_id:
                    continue
                for device in await api.get_device_list(group_id):
                    serial = str(device.get("deviceSerial") or "")
                    if not serial:
                        continue
                    info = await api.get_device_info(serial)
                    props = _extract_props(info)
                    mapped = {
                        str(key): value for key, value in decode_props(props).items()
                    }
                    output.append(
                        {
                            "serial": serial,
                            "name": device.get("deviceName")
                            or device.get("nickName")
                            or device.get("name"),
                            "model": device.get("model")
                            or device.get("productName"),
                            "group_id": group_id,
                            "props": props,
                            "mapped": mapped,
                        }
                    )
        if args.save:
            secrets.set_many(
                "treeow", {"account": account, "password": password}
            )
            set_provider_enabled(config.path, "treeow", True)
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return
        print(f"Treeow 账号 {account} · {len(output)} 台设备")
        for item in output:
            print(
                f"{item['serial']:<24} {str(item['name'] or ''):<18} "
                f"{str(item['model'] or ''):<20} group={item['group_id']}"
            )
            if item["mapped"]:
                print(
                    "    mapped="
                    + ", ".join(f"{key}={value}" for key, value in item["mapped"].items())
                )
            print(
                "    raw="
                + json.dumps(item["props"], ensure_ascii=False, sort_keys=True)
            )
        if args.save:
            print("账号已保存，重启网关后生效。")
    except (TreeowAuthError, TreeowError) as exc:
        print(f"Treeow 错误: {exc}", file=sys.stderr)
        print(
            "提示：如果最近在 Treeow App 改过密码，请在 Web 配置页重新填写密码，"
            "或用 homekit-one treeow-list -u 账号 -p 重新登录。",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    finally:
        await api.close()
