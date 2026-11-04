"""Command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import socket
import sys
import webbrowser

from homeone import __version__
from homeone.config import ConfigError, load_config
from homeone.log import setup_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="homeone", description="米家 / TCL / 树新风 HomeKit 网关")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="启动网关")
    run.add_argument("--config", help="配置文件路径")
    run.add_argument("--no-homekit", action="store_true", help="只启动 Web UI，不启动 HomeKit")
    run.add_argument("--web-port", type=int, help="覆盖 Web 端口")
    run.add_argument("--web-host", help="覆盖 Web 监听地址")

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

    treeow = sub.add_parser(
        "treeow-list", help="登录 Treeow 云，列出设备、原始属性和映射结果"
    )
    treeow.add_argument("--config", help="配置文件路径")
    treeow.add_argument("--account", "-u", help="Treeow Home 账号（手机号）")
    treeow.add_argument("--password", "-p", help="留空则交互式输入")
    treeow.add_argument("--save", action="store_true", help="保存账号并启用 treeow provider")
    treeow.add_argument("--json", action="store_true", help="输出 JSON")

    tcl = sub.add_parser(
        "tcl-list", help="登录 TCL 云，列出设备类型、shadow 字段和映射结果"
    )
    tcl.add_argument("--config", help="配置文件路径")
    tcl.add_argument("--username", "-u", help="TCL Home 账号")
    tcl.add_argument("--password", "-p", help="留空则交互式输入")
    tcl.add_argument("--device", help="只看某个 TCL device_id")
    tcl.add_argument("--no-shadow", action="store_true", help="只列设备，不读 shadow")
    tcl.add_argument("--save", action="store_true", help="保存账号并启用 tcl provider")
    tcl.add_argument("--json", action="store_true", help="输出 JSON")

    tclplus = sub.add_parser(
        "tcl-plus-list",
        help="登录 TCL+（国内版/微信小程序），列出电视/空调等设备和属性",
    )
    tclplus.add_argument("--config", help="配置文件路径")
    tclplus.add_argument("--phone", "-u", help="TCL+ 手机号")
    tclplus.add_argument("--password", "-p", help="留空则交互式输入")
    tclplus.add_argument("--sms", action="store_true", help="使用短信验证码登录")
    tclplus.add_argument("--account-id", help="抓包得到的 account_id（token 登录）")
    tclplus.add_argument("--refresh-token", help="抓包得到的 refreshToken（token 登录）")
    tclplus.add_argument("--device", help="只看某个 deviceId")
    tclplus.add_argument("--panel", action="store_true", help="显示面板属性定义")
    tclplus.add_argument("--save", action="store_true", help="保存凭据并启用 tcl_plus provider")
    tclplus.add_argument("--json", action="store_true", help="输出 JSON")

    imp = sub.add_parser("mijia-import", help="从小米云导入设备 token")
    imp.add_argument("--config", help="配置文件路径")
    imp.add_argument("--username", "-u")
    imp.add_argument("--password", "-p")
    imp.add_argument("--country", default="cn")

    pair = sub.add_parser("pairing", help="显示 HomeKit 配对码和 URI")
    pair.add_argument("--config", help="配置文件路径")

    doctor = sub.add_parser("doctor", help="检查配置、端口和状态目录")
    doctor.add_argument("--config", help="配置文件路径")

    args = parser.parse_args(argv)

    if args.command == "run":
        _run(args)
    elif args.command == "discover":
        asyncio.run(_discover(args.timeout))
    elif args.command == "mijia-cloud-list":
        asyncio.run(_mijia_cloud_list(args))
    elif args.command == "mijia-oauth-login":
        asyncio.run(_mijia_oauth_login(args))
    elif args.command == "treeow-list":
        asyncio.run(_treeow_list(args))
    elif args.command == "tcl-list":
        asyncio.run(_tcl_list(args))
    elif args.command == "tcl-plus-list":
        asyncio.run(_tcl_plus_list(args))
    elif args.command == "mijia-import":
        _mijia_import(args)
    elif args.command == "pairing":
        asyncio.run(_pairing(args.config))
    elif args.command == "doctor":
        _doctor(args.config)


def _load(path: str | None):
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _run(args: argparse.Namespace) -> None:
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

    from homeone.app import Gateway

    gateway = Gateway(config, no_homekit=args.no_homekit)
    try:
        asyncio.run(gateway.serve())
    except KeyboardInterrupt:
        pass


async def _discover(timeout: float) -> None:
    from homeone.providers.mijia.discovery import discover

    devices = await discover(duration=timeout)
    if not devices:
        print("没有发现设备。确认 Mac 与设备在同一网段，且设备已配网。")
        return
    print(f"{'IP':<16} {'did':<12} {'hex':<12} token")
    for device in devices:
        print(
            f"{device.host:<16} {device.did:<12} {device.hex_id:<12} "
            f"{device.token or '(未暴露，需小米云导入)'}"
        )


def _mijia_import(args: argparse.Namespace) -> None:
    config = _load(args.config)
    username = args.username or input("小米账号: ").strip()
    password = args.password or getpass.getpass("密码: ")
    setup_logging("INFO")
    from homeone.providers.mijia.token_import import TokenImportError, import_devices
    from homeone.registry import DeviceRegistry
    from homeone.secrets import SecretsStore

    try:
        found = import_devices(username, password, args.country)
    except TokenImportError as exc:
        print(f"导入失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    state_dir = config.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    registry = DeviceRegistry(state_dir / "devices.json")
    secrets = SecretsStore(state_dir / "secrets.json")
    tokens = dict(secrets.get("mijia", "tokens", default={}) or {})
    for item in found:
        token = item.pop("token", None)
        registry.upsert_mijia(item)
        if token:
            tokens[str(item["id"])] = token
    secrets.set("mijia", "tokens", tokens)
    if found:
        from homeone.config import set_provider_enabled

        set_provider_enabled(config.path, "mijia", True)
    print(f"已导入 {len(found)} 台设备到 {registry.path}")
    for item in found:
        print(f"  {item['id']}  {item['name']}  {item['model']}  {item.get('host', '')}")
    print("把型号加入配置/确认支持后，重启网关。")


async def _pairing(config_path: str | None) -> None:
    config = _load(config_path)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    from homeone.core.manager import DeviceManager
    from homeone.homekit.bridge import HomeKitBridge

    manager = DeviceManager(config, [])
    bridge = HomeKitBridge(config, manager)
    info = bridge.pairing_info()
    print(f"名称: {info['name']}")
    print(f"配对码: {info['pincode']}")
    print(f"URI: {info['uri']}")
    print(f"端口: {info['port']}")
    print(f"状态: {'已配对' if info['paired'] else '未配对'}")
    await bridge.stop()


def _doctor(config_path: str | None) -> None:
    problems: list[str] = []
    config = _load(config_path)
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

    print(
        "Provider: "
        + ", ".join(
            name
            for name, enabled in (
                ("mijia", config.mijia.enabled),
                ("mijia_cloud", config.mijia_cloud.enabled),
                ("tcl", config.tcl.enabled),
                ("tcl_plus", config.tcl_plus.enabled),
                ("treeow", config.treeow.enabled),
                ("fake", config.fake.enabled),
            )
            if enabled
        )
    )
    if problems:
        print("\n问题:")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("\n检查通过。")


async def _mijia_cloud_list(args: argparse.Namespace) -> None:
    config = _load(args.config)
    from homeone.providers.mijia_cloud.client import (
        MijiaCloudAuthError,
        MijiaCloudError,
        XiaomiCloudClient,
    )
    from homeone.providers.mijia_cloud.spec import build_cloud_spec
    from homeone.secrets import SecretsStore

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
        from homeone.providers.mijia_cloud.oauth import XiaomiOAuthClient

        client = XiaomiOAuthClient(
            country,
            config.state_dir,
            tokens=oauth_tokens,
            on_tokens=lambda values: secrets.set_many("mijia_oauth", values),
        )
    else:
        username = (
            args.username
            or config.mijia_cloud.username
            or secrets.get("mijia_cloud", "username")
            or os.environ.get("HONE_MIJIA_CLOUD_USERNAME")
        )
        password = (
            args.password
            or config.mijia_cloud.password
            or secrets.get("mijia_cloud", "password")
            or os.environ.get("HONE_MIJIA_CLOUD_PASSWORD")
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
    from homeone.config import set_provider_enabled
    from homeone.providers.mijia_cloud.client import MijiaCloudAuthError, MijiaCloudError
    from homeone.providers.mijia_cloud.oauth import (
        DEFAULT_REDIRECT_URI,
        XiaomiOAuthClient,
        generate_device_id,
        parse_callback,
        state_for,
    )
    from homeone.secrets import SecretsStore

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
    try:
        raw = input("回调地址或 code: ").strip()
    except EOFError:
        raw = ""
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
    print("现在可以运行：homeone mijia-cloud-list --specs --props")
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
    from homeone.config import set_provider_enabled
    from homeone.providers.treeow.api import TreeowAPI, TreeowAuthError, TreeowError
    from homeone.providers.treeow.mapping import decode_props
    from homeone.providers.treeow.provider import _extract_props
    from homeone.secrets import SecretsStore

    secrets = SecretsStore(config.state_dir / "secrets.json")
    account = (
        args.account
        or config.treeow.account
        or secrets.get("treeow", "account")
        or os.environ.get("HONE_TREOW_ACCOUNT")
    )
    password = (
        args.password
        or config.treeow.password
        or secrets.get("treeow", "password")
        or os.environ.get("HONE_TREOW_PASSWORD")
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
        raise SystemExit(1) from exc
    finally:
        await api.close()


async def _tcl_list(args: argparse.Namespace) -> None:
    config = _load(args.config)
    from homeone.config import set_provider_enabled
    from homeone.providers.tcl.cloud import TclAuthError, TclCloud, TclError
    from homeone.providers.tcl.mapping import decode_shadow, is_ac_device
    from homeone.secrets import SecretsStore

    secrets = SecretsStore(config.state_dir / "secrets.json")
    username = (
        args.username
        or config.tcl.username
        or secrets.get("tcl", "username")
        or os.environ.get("HONE_TCL_USERNAME")
    )
    password = (
        args.password
        or config.tcl.password
        or secrets.get("tcl", "password")
        or os.environ.get("HONE_TCL_PASSWORD")
    )
    if not username:
        username = input("TCL 账号: ").strip()
    if not password:
        password = getpass.getpass("密码: ")
    setup_logging("WARNING")

    cloud = TclCloud(username, password, config.state_dir)
    try:
        things = await cloud.get_things()
        output: list[dict[str, object]] = []
        for thing in things:
            device_id = str(thing.get("device_id") or thing.get("deviceId") or "")
            if not device_id:
                continue
            if args.device and device_id != str(args.device):
                continue
            item: dict[str, object] = {
                "device_id": device_id,
                "name": thing.get("nick_name") or thing.get("nickName"),
                "type": thing.get("device_name") or thing.get("deviceName"),
                "category": thing.get("category"),
                "product_key": thing.get("product_key") or thing.get("productKey"),
                "online": thing.get("is_online", thing.get("isOnline")),
            }
            if not args.no_shadow:
                try:
                    shadow = await cloud.get_shadow(device_id)
                    state = shadow.get("state") or {}
                    reported = state.get("reported") or {}
                    delta = state.get("delta") or {}
                    item["reported"] = reported
                    item["delta"] = delta
                    if is_ac_device(str(item.get("type") or "")):
                        item["mapped"] = {
                            str(key): value
                            for key, value in decode_shadow(
                                {**reported, **delta}, config.tcl.work_modes
                            ).items()
                        }
                except TclError as exc:
                    item["shadow_error"] = str(exc)
            output.append(item)
        if args.save:
            secrets.set_many("tcl", {"username": username, "password": password})
            set_provider_enabled(config.path, "tcl", True)
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return
        print(f"TCL 账号 {username} · {len(output)} 台设备")
        for item in output:
            print(
                f"{item['device_id']:<14} {str(item['name'] or ''):<16} "
                f"type={item.get('type')} category={item.get('category')} "
                f"online={item.get('online')}"
            )
            if item.get("mapped"):
                print(
                    "    mapped="
                    + ", ".join(
                        f"{key}={value}" for key, value in item["mapped"].items()
                    )
                )
            if item.get("reported"):
                print(
                    "    reported="
                    + json.dumps(item["reported"], ensure_ascii=False, sort_keys=True)
                )
            if item.get("delta"):
                print(
                    "    delta="
                    + json.dumps(item["delta"], ensure_ascii=False, sort_keys=True)
                )
            if item.get("shadow_error"):
                print(f"    shadow_error={item['shadow_error']}")
        if args.save:
            print("账号已保存，重启网关后生效。")
    except (TclAuthError, TclError) as exc:
        print(f"TCL 错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await cloud.close()


async def _tcl_plus_list(args: argparse.Namespace) -> None:
    config = _load(args.config)
    from homeone.config import set_provider_enabled
    from homeone.providers.tcl_plus.client import (
        TclPlusAuthError,
        TclPlusClient,
        TclPlusError,
    )
    from homeone.secrets import SecretsStore

    secrets = SecretsStore(config.state_dir / "secrets.json")
    phone = args.phone or secrets.get("tcl_plus", "phone") or ""
    password = args.password or secrets.get("tcl_plus", "password") or ""
    account_id = args.account_id or secrets.get("tcl_plus", "account_id") or ""
    refresh_token = args.refresh_token or secrets.get("tcl_plus", "refresh_token") or ""

    if not (account_id and refresh_token):
        if not phone:
            phone = input("TCL+ 手机号: ").strip()
        if not password and not args.sms:
            password = getpass.getpass("TCL+ 密码: ")

    setup_logging("WARNING")
    client = TclPlusClient(
        phone=phone,
        password=password,
        account_id=account_id,
        refresh_token=refresh_token,
        state_dir=config.state_dir,
    )
    try:
        if account_id and refresh_token:
            await client.login_with_refresh_token()
        elif args.sms:
            await client.send_sms_captcha(phone)
            code = input("短信验证码: ").strip()
            await client.login_by_sms(phone, code)
        else:
            await client.login_by_password(phone, password)

        user = await client.get_user_info()
        devices = await client.get_devices()
        output: list[dict[str, object]] = []
        for device in devices:
            device_id = str(device.get("deviceId") or "")
            if not device_id:
                continue
            if args.device and device_id != str(args.device):
                continue
            item: dict[str, object] = {
                "device_id": device_id,
                "name": device.get("nickName") or device.get("deviceName"),
                "category": device.get("category"),
                "product_key": device.get("productKey"),
                "online": device.get("isOnline"),
                "wechat_control": device.get("weChatControl"),
                "raw": device,
            }
            try:
                item["status"] = await client.get_snapshot(device_id)
            except TclPlusError as exc:
                item["status_error"] = str(exc)
            if args.panel and item.get("product_key"):
                try:
                    item["panel"] = await client.get_digital_model(
                        str(item["product_key"])
                    )
                except TclPlusError as exc:
                    item["panel_error"] = str(exc)
            output.append(item)

        if args.save:
            tokens = {
                "phone": phone,
                "account_id": client.account_id,
                "access_token": client.access_token,
                "refresh_token": client.refresh_token,
            }
            if password:
                tokens["password"] = password
            secrets.set_many("tcl_plus", tokens)
            set_provider_enabled(config.path, "tcl_plus", True)

        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return
        print(
            f"TCL+ 账号 {user.get('mobile') or phone} · "
            f"{len(output)} 台设备"
        )
        for item in output:
            print(
                f"{item['device_id']:<20} {str(item.get('name') or ''):<16} "
                f"category={item.get('category')} productKey={item.get('product_key')} "
                f"online={item.get('online')} weChatControl={item.get('wechat_control')}"
            )
            status = item.get("status") or {}
            if status:
                print(
                    "    status="
                    + json.dumps(status, ensure_ascii=False, sort_keys=True)
                )
            if item.get("status_error"):
                print(f"    status_error={item['status_error']}")
            panel = item.get("panel") or []
            for attr in panel:
                print(
                    f"    panel {attr.get('identifier')} type={attr.get('type')} "
                    f"title={attr.get('title')} specs={attr.get('specs')}"
                )
            if item.get("panel_error"):
                print(f"    panel_error={item['panel_error']}")
        if args.save:
            print("凭据已保存，重启网关后生效。")
    except TclPlusAuthError as exc:
        print(f"TCL+ 登录失败: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except TclPlusError as exc:
        print(f"TCL+ 错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        await client.close()
