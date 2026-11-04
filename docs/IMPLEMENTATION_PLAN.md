# 实现计划与验收记录

## 目标

在 Mac mini 上运行一个常驻的 HomeKit 网关：

- 米家 Wi-Fi 设备走局域网 miIO / MIoT。
- TCL Home 分体空调走 TCL 云 + AWS IoT shadow。
- 树新风 Treeow 净化器走 Treeow 云属性接口。
- 北向统一为 Apple Home（HAP），单进程，launchd 常驻，Web 配置台。

`Lakr233/TreeNewBee` 是玩梗仓库，不含设备协议；树新风实现参考
`hlhk2017/treeow-homeassistant`。

## 架构决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 语言 | Python 3.12 + uv | TCL / Treeow 参考实现都是 Python，可直接复用协议层；HAP-python 成熟 |
| 北向 | HomeKit HAP | 与 MijiaLAN 形态一致，iPhone/Siri 零额外 App |
| 中间层 | `Capability` 统一模型 | Provider 只翻译协议，HomeKit 层只认 capability，新增品牌不改 HAP |
| 米家 | 自研 miIO 客户端 | 避免 python-miio 老依赖；协议短、可测试、可控制超时与重试 |
| TCL | 移植云流程 | 登录 / cloud URLs / Cognito / AWS IoT shadow，与 HA 集成同源 |
| Treeow | 移植云 API | 登录 / 设备列表 / `otap/prop` 读写 |
| 配置 | TOML + `secrets.json` 0600 | 后台 launchd 无法可靠解锁钥匙串；明确用文件权限而不是伪加密 |
| 设备集 | 启动时固定 | HAP bridge 动态增删配件需要重新发布 `/accessories`，v1 用重启规避 |

## 里程碑

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 项目骨架、配置、统一模型、Manager、Fake provider、HomeKit 桥、Web 骨架 | ✅ |
| M1 | 米家：miIO 加密/发现/MIoT 读写、4 个型号映射、小米云 token 导入 | ✅ |
| M2 | TCL：登录/cloud URLs/Cognito/AWS IoT shadow、分体空调映射、命令等待 | ✅ |
| M3 | Treeow：登录/设备列表/属性读写、净化器映射 | ✅ |
| M4 | Web 配置台、配对二维码/重置、launchd 安装、doctor/CLI、文档 | ✅ |
| M5 | 小米云 provider：OAuth 授权、设备列表、MIoT-Spec 解析、属性读写、`mijia-cloud-list` | ✅ |
| M6 | 真机联调：米家 token、米家云/中枢子设备、TCL 账号、Treeow 账号、iPhone 配对 | ⏳ 待用户设备 |
| M7 | 树新风/TCL 终端诊断（`treeow-list` / `tcl-list`）与字段校验 | ✅ |
| M8 | TCL 国内版 TCL+ 客户端（密码/短信/token 登录、设备/状态/面板诊断） | ✅ |
| M9 | TCL+ TV 映射与 HomeKit Television（电源/音量/静音/信源） | ✅ |
| M10 | TCL+ TV 真机联调（在线状态、weChatControl、遥控键） | ⏳ 待电视在线 |

## 验收标准

- `homeone run` 在无配置时能用 4 台演示设备启动，打印 HomeKit 二维码。
- `homeone discover` 能广播发现 miIO 设备；`homeone mijia-import` 能写入 registry + secrets。
- Web `/api/health`、`/api/devices`、设备 set/refresh、配对 QR 均可用。
- HomeKit bridge 序列化出 AirPurifier / HeaterCooler / Fanv2 / Outlet / AirQualitySensor /
  FilterMaintenance / TemperatureSensor / HumiditySensor / Switch 服务。
- Provider 失败时设备标记离线，命令失败回滚乐观更新。
- `pytest` 71 个测试通过，`ruff check` 通过。
- 配对码跨进程稳定；`accessory.state` / `secrets.json` / `pairing.json` 为 0600。

## 验证记录

已执行：

```text
pytest -q            -> 71 passed
ruff check src tests -> All checks passed
homeone doctor       -> 状态目录可写、端口可用、配置检查通过
homeone run          -> Web + HomeKit bridge 启动；QR/PNG 正常
homeone pairing      -> 与运行中进程的配对码一致
米家不可达设备       -> 首次轮询后 online=false，error 记录握手超时
```

未执行（需要真实账号/设备）：

- 真实米家设备 token 读写。
- 真实小米云 OAuth 授权、云端设备列表、MIoT-Spec 解析、属性读写（含中枢子设备）。
- 真实 TCL 账号登录、shadow 读取/写入、模式数值确认。
- 真实 Treeow 账号登录、属性读写。
- iPhone「家庭」App 实际配对与控制。
- launchd 安装（需要用户确认后再执行 `scripts/install.sh`）。

## 真机联调清单

1. 米家局域网：先 `homeone discover` 确认 IP/did；再用 Web 或 `homeone mijia-import` 导入 token。
2. 米家云/中枢子设备：先 `homeone mijia-oauth-login` 完成 OAuth，再用
   `homeone mijia-cloud-list --specs --props` 验证设备与属性；最后启用
   `[providers.mijia_cloud]`。
3. TCL：`homeone tcl-list -u 账号 --save`，确认设备类型和 shadow 字段；
   `workMode` 数值（默认 auto=0/cool=1/dry=2/fan=3/heat=4）不对时用配置覆盖。
4. Treeow：`homeone treeow-list -u 账号 --save`，确认 `mapped=` 里的能力齐全。
5. 重启网关，打开 `/pairing`，用 iPhone「家庭」扫码。
6. 逐台验证：开关、模式、温度、风速、摆风、PM2.5、滤芯、童锁。
7. 若云端字段与预期不同，用 `mijia-cloud-list --props` 或日志中的原始 shadow/props 修正映射。

## 风险与后续

- TCL / Treeow 云接口可能随 App 更新失效；协议层已隔离在各自 provider。
- TCL 账号存在受限风险；建议单独账号 + 设备共享。
- 米家型号覆盖有限；新增型号只需在 `models.py` 增加 `ModelSpec`。
- HomeKit 设备集变更需要重启；后续可在重启前重建 bridge 或实现动态 `/accessories` 重发。
- 可扩展：MQTT / REST 北向、更多 TCL 品类（除湿机/净化器）、Treeow 更多属性
  （anion/UV/AAL）、能源统计。
