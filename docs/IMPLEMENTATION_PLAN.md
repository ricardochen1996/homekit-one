# 实现计划与验收记录

## 目标

在 Mac mini 上运行一个常驻的 HomeKit 网关：

- 米家 Wi-Fi 设备走局域网 miIO / MIoT。
- 树新风 Treeow 净化器走 Treeow 云属性接口。
- 北向统一为 Apple Home（HAP），单进程，launchd 常驻，Web 配置台。

`Lakr233/TreeNewBee` 是玩梗仓库，不含设备协议；树新风实现参考
`hlhk2017/treeow-homeassistant`。

## 架构决策

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 语言 | Python 3.12 + uv | Treeow 参考实现是 Python，可直接复用协议层；HAP-python 成熟 |
| 北向 | HomeKit HAP | 与 MijiaLAN 形态一致，iPhone/Siri 零额外 App |
| 中间层 | `Capability` 统一模型 | Provider 只翻译协议，HomeKit 层只认 capability，新增品牌不改 HAP |
| 米家 | 自研 miIO 客户端 | 避免 python-miio 老依赖；协议短、可测试、可控制超时与重试 |
| Treeow | 移植云 API | 登录 / 设备列表 / `otap/prop` 读写 |
| 配置 | TOML + `secrets.json` 0600 | 后台 launchd 无法可靠解锁钥匙串；明确用文件权限而不是伪加密 |
| 设备集 | 启动时固定 | HAP bridge 动态增删配件需要重新发布 `/accessories`，v1 用重启规避 |

## 里程碑

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 项目骨架、配置、统一模型、Manager、Fake provider、HomeKit 桥、Web 骨架 | ✅ |
| M1 | 米家：miIO 加密/发现/MIoT 读写、4 个型号映射、小米云 token 导入 | ✅ |
| M3 | Treeow：登录/设备列表/属性读写、净化器映射 | ✅ |
| M4 | Web 配置台、配对二维码/重置、launchd 安装、doctor/CLI、文档 | ✅ |
| M5 | 小米云 provider：OAuth 授权、设备列表、MIoT-Spec 解析、属性读写、`mijia-cloud-list` | ✅ |
| M6 | 真机联调：米家 token、米家云/中枢子设备、Treeow 账号、iPhone 配对 | ⏳ 待用户设备 |
| M7 | 树新风终端诊断（`treeow-list`）与字段校验 | ✅ |
| M11 | 接入体验：Web 内 OAuth/短信/局域网发现、保存即验证、设备列表/白名单、失败提示、`doctor --probe`、OAuth token 导入 | ✅ |

## 验收标准

- `homekit-one run` 在无配置时能用 4 台演示设备启动，打印 HomeKit 二维码。
- `homekit-one discover` 能广播发现 miIO 设备；`homekit-one mijia-import` 能写入 registry + secrets。
- Web `/api/health`、`/api/devices`、设备 set/refresh、配对 QR 均可用。
- HomeKit bridge 序列化出 AirPurifier / HeaterCooler / Fanv2 / Outlet / AirQualitySensor /
  FilterMaintenance / TemperatureSensor / HumiditySensor / Switch 服务。
- Provider 失败时设备标记离线，命令失败回滚乐观更新。
- `pytest` 71 个测试通过，`ruff check` 通过。
- 配对码跨进程稳定；`accessory.state` / `secrets.json` / `pairing.json` 为 0600。

## 验证记录

已执行：

```text
pytest -q            -> 104 passed
ruff check src tests -> All checks passed
homekit-one doctor       -> 状态目录可写、端口可用、provider 凭据检查通过
homekit-one run          -> Web + HomeKit bridge 启动；QR/PNG 正常
homekit-one pairing      -> 与运行中进程的配对码一致
Web /setup           -> 品牌表单、OAuth 页、局域网扫描结果、验证报告正常渲染
米家不可达设备       -> 首次轮询后 online=false，error 记录握手超时
```

未执行（需要真实账号/设备）：

- 真实米家设备 token 读写。
- 真实小米云 OAuth 授权、云端设备列表、MIoT-Spec 解析、属性读写（含中枢子设备）。
- 真实 Treeow 账号登录、属性读写。
- iPhone「家庭」App 实际配对与控制。
- launchd 安装（需要用户确认后再执行 `scripts/install.sh`）。

## 真机联调清单

推荐全部在 Web 配置页完成（保存即验证，并列出可接入设备）；下面同时给出终端命令。

1. 米家局域网：Web 点「扫描局域网」→ 选型号 → 保存；或先 `homekit-one discover`
   确认 IP/did，再用 `homekit-one mijia-import --oauth` 导入 token。
2. 米家云/中枢子设备：Web 点「小米 OAuth 授权」，粘贴回调地址后会自动验证并列出设备；
   终端等价命令是先 `homekit-one mijia-oauth-login`，再
   `homekit-one mijia-cloud-list --specs --props`。
4. Treeow：Web 填账号密码保存验证；终端等价命令是
   `homekit-one treeow-list -u 账号 --save`。
5. 在验证报告里勾选要接入的设备（不勾选 = 全部），重启网关。
6. 打开 `/pairing`，用 iPhone「家庭」扫码。
7. 逐台验证：开关、模式、温度、风速、摆风、PM2.5、滤芯、童锁。
8. 若云端字段与预期不同，用 `mijia-cloud-list --props`、`doctor --probe` 或日志中的
   原始 shadow/props 修正映射。

## 风险与后续

- 树新风云接口可能随 App 更新失效；协议层已隔离在 provider。

- 米家型号覆盖有限；新增型号只需在 `models.py` 增加 `ModelSpec`。
- HomeKit 设备集变更需要重启；后续可在重启前重建 bridge 或实现动态 `/accessories` 重发。
- 可扩展：MQTT / REST 北向、更多 Treeow 属性
  （anion/UV/AAL）、能源统计。
