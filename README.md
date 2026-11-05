# HomeKit One

在 Mac mini 上常驻运行的 **HomeKit 网关**，把三类设备统一接入 Apple「家庭」：

- **米家 Wi-Fi 设备**：局域网 miIO / MIoT（UDP 54321），不需要小米云参与日常控制。
- **米家云端设备**：小米云 `/app/v2/miotspec` 读写，覆盖中枢下的 Zigbee / BLE Mesh 子设备，
  也自动识别摄像头、扫地机、宠物喂食器/饮水机、音箱等 MIoT 设备。
- **树新风 Treeow 净化器**：Treeow 云端属性接口，首版支持净化器。

> 说明：`Lakr233/TreeNewBee` 是一个「讲文明 树新风」的玩梗仓库，不是设备集成。树新风的实际协议参考的是
> `hlhk2017/treeow-homeassistant`，走 `eziotes.treeow.com.cn` 云 API。

---

## 架构

```text
iPhone / Siri / 家庭 App
        │  HomeKit HAP（mDNS + TCP 51234）
┌───────▼─────────┐
│  HomeKitBridge  │  pyhap：Bridge + 每台设备一个 Accessory
│  (HAP-python)   │  状态变化 → Characteristic.set_value
└───────┬─────────┘
        │ 统一 capability / command
┌───────▼─────────┐
│  DeviceManager  │  设备注册、轮询、命令串行化、乐观更新/回滚
└───┬───────┬─────┴───────┬──────────┐
    │       │             │          │
┌───▼───┐ ┌─▼─────────┐ ┌─▼────────┐ │
│ Mijia │ │  Treeow   │ │          │ │
│ LAN   │ │  Cloud    │ │          │ │
├───────┤ └───────────┘ └──────────┘ │
│ Mijia │                            │
│ Cloud │                            │
└───────┘                            │
                                     │
                             ┌───────▼────────┐
                             │ Web UI / REST  │  127.0.0.1:8080
                             └────────────────┘
```

- **统一模型**：`Capability`（power / mode / target_temperature / fan_speed / pm25 / filter_life …）。
  Provider 只负责把自家协议翻译成 capability；HomeKit 层只认 capability，因此新增品牌不用改 HAP 代码。
- **单进程单事件循环**：HAP、Web、轮询、云请求都在同一个 asyncio loop；boto3 调用丢到线程池。
- **静态配件集**：设备在启动时构建；新增/删除设备后重启进程（launchd 会自动拉起）。

## 目录

```text
src/homekit_one/
  app.py                  # Gateway 编排：providers -> manager -> HomeKit + Web
  cli.py                  # setup / run / discover / import / OAuth / pairing / doctor
  config.py               # TOML 配置
  secrets.py              # 0600 secrets.json
  registry.py             # Web UI 添加的米家设备
  core/manager.py         # 轮询、命令、状态缓存、监听器
  core/model.py           # Capability / Device / Command
  providers/onboarding.py # 保存即验证：登录、列设备、读状态、失败提示
  providers/plugins.py    # Provider 插件注册/发现/构建
  providers/builtin.py    # 内置 Provider 插件声明
  providers/mijia/        # 局域网 miIO：加密、UDP 客户端、发现、型号映射、token 导入
  providers/mijia_cloud/  # 小米云：OAuth、设备列表、MIoT-Spec 解析、属性读写
  providers/treeow/       # 登录 / 设备列表 / 属性读写
  homekit/bridge.py       # HAP 驱动、配对、二维码、重置
  homekit/accessories.py  # capability -> HAP service/characteristic
  web/                    # FastAPI + Jinja2 配置台
scripts/                  # launchd 安装/卸载
tests/                    # 104 个单元测试
```

## 快速开始

要求：macOS 15+（已在 macOS 27 / Apple Silicon 验证），`uv`，Python 3.12（uv 会自动下载）。

```bash
cd ~/PersonalGit/homekit-one
uv venv --python 3.12
uv pip install -e ".[dev]"

# 第一次先无配置启动：4 台演示设备 + Web + HomeKit 二维码
.venv/bin/homekit-one run

# 或者直接启动并打开接入配置页（推荐第一次使用）
.venv/bin/homekit-one setup
```

> 旧版命令 `homeone` 已更名为 `homekit-one`，Python 包名改为 `homekit_one`。
> 旧的配置/状态目录和 `HOMEONE_CONFIG`、`HONE_*` 环境变量仍会自动兼容。
> 如果旧 venv 里还留着失效的 `.venv/bin/homeone`，直接删除即可。

打开 <http://127.0.0.1:8080>：

1. 「配置」页选择品牌：保存账号时会**立即登录验证并列出可接入设备**，不需要先重启再翻日志。
2. 页面顶部出现「配置已保存，等待重启」后点「立即重启」。
3. 「HomeKit」页用 iPhone「家庭」App 扫码配对。

没有真实设备时，删除配置文件并重启即可回到演示模式。

## Web 接入向导

配置页（`/setup`）把各品牌的接入步骤收敛到同一条路径：**登录 → 验证 → 选设备 → 重启 → 配对**。

| 品牌 | 页面内可完成 | 说明 |
| --- | --- | --- |
| 米家云（中枢子设备） | 小米 OAuth 授权、验证并列出设备 | 不再需要开终端执行 `mijia-oauth-login`；支持两步验证 |
| 米家局域网 | 扫描局域网、选型号、填 token、保存即验证 | 扫描结果可一键填入表单；OAuth 授权后还能直接导入 token |
| 树新风 Treeow | 账号密码、保存即验证 | 直接列出账号下可接入的净化器 |

验证结果会显示每个设备的类型、在线/读取状态和当前属性；失败时给出下一步提示
（例如小米 403 会直接建议改用 OAuth，设备离线会提示检查电源和网络）。
验证后可以在页面上勾选“只接入哪些设备”，不需要手改 TOML 白名单。
保存凭据后页面顶部会出现重启提示，点一下即可生效。

首页每台设备有「详情/配置」入口。Home App 默认只保留主控和只读参数，
UV/负离子/地毯增压/自动集尘等高级开关统一放到设备详情页，避免家庭 App 里
出现大量同名或低频开关。台灯和电暖毯保留完整 HomeKit 控制。

配置页和设备详情页的表单都通过 AJAX 提交：保存凭据、验证、开关和数值设置
只做局部刷新并显示结果提示，不再整页跳转或直接把 JSON 显示在浏览器里。

终端命令仍然保留，适合脚本、排障和没有浏览器的场景：

- `homekit-one setup`：启动网关并自动打开配置页。
- `homekit-one doctor --probe`：检查配置/端口/凭据，并实际登录每个已启用 provider 列出设备。
- `homekit-one mijia-import --oauth`：用已保存的 OAuth 授权导入局域网 token，无需小米密码。

## 配置

> 日常接入建议直接用 Web 配置页：它会把凭据写入 `secrets.json`，并自动把对应
> provider 的 `enabled` 改为 `true`。下面的 TOML 适合需要手动控制白名单、轮询间隔
> 或设备白名单的场景。

```bash
mkdir -p ~/.config/homekit-one
cp config.example.toml ~/.config/homekit-one/config.toml
```

状态目录默认 `~/Library/Application Support/HomeKit One`：

| 文件 | 内容 |
| --- | --- |
| `secrets.json` | Treeow 账号密码、米家 token、米家 OAuth token（0600） |
| `devices.json` | Web UI 添加的米家设备（0600） |
| `accessory.state` | HomeKit 配对客户端与私钥（0600）；删除后需要重新配对 |
| `pairing.json` | 稳定的 HomeKit 配对码（0600）；与 `accessory.state` 一起删除会重新生成 |

> 从旧版升级：如果新路径不存在，程序会继续使用旧的
> `~/.config/homeone/config.toml` 和 `~/Library/Application Support/HomeOne`，
> 以免丢失凭据和 HomeKit 配对；`HOMEONE_CONFIG` / `HONE_*` 环境变量也仍然兼容。
> 确认新版本运行正常后，可以手动把旧目录移到新路径。

凭据优先级：环境变量 > `secrets.json` > `config.toml`。

### 米家云（中枢子设备）

`[providers.mijia_cloud]` 用于没有 IP/token 的中枢子设备。它按设备列表里的
MIoT-Spec URN 自动识别常见属性（开关、亮度、色温、模式、风速、温度、湿度、
PM2.5、滤芯、童锁），不需要为每个型号写属性表。

**推荐先做 OAuth 授权**（小米已限制旧版密码登录，即使没开两步验证也可能返回 403）。
Web 配置页点「小米 OAuth 授权」即可，不需要终端：

1. 选择账号区域，点「小米 OAuth 授权」；
2. 用小米账号登录并授权（支持两步验证）；
3. 浏览器会跳转到 `http://homeassistant.local:8123`，页面打不开是正常的；
4. 从地址栏复制完整 URL（含 `code=...`），粘贴回配置页；
5. 页面会立即验证并列出账号下的设备，token 保存到 `secrets.json`。

习惯终端时仍可使用：

```bash
homekit-one mijia-oauth-login
```

流程相同；在提示符直接回车会读取 macOS 剪贴板里的回调地址，也可以用
`--code` 直接传入。

> 这里使用的是小米官方 `XiaoMi/ha_xiaomi_home` 集成公开的 OAuth client，
> 授权页显示的应用名可能是 “Xiaomi Home (Home Assistant Integration)”。
> token 只保存在本机 `secrets.json`（0600），不会上传到其他服务。

配置示例：

```toml
[providers.mijia_cloud]
enabled = true
poll_interval = 15
country = "cn"          # 大陆账号 cn；其他地区填 de / us / sg 等
# username = "..."
# password = "..."
# devices = ["did1", "did2"]   # 可选白名单
```

OAuth 授权后无需在配置里写账号密码；也可以保留旧的账号密码作为回退。
云端 provider 与局域网 provider 同时开启时，
同一台 Wi-Fi 设备可能出现两次；建议局域网设备用 `providers.mijia`，
中枢子设备用 `providers.mijia_cloud`。

支持的云端设备类型：灯、插座、开关、空气净化器、风扇、空调、温控（电热毯/取暖器）、
温湿度/PM2.5 传感器。洗碗机等只有开关属性的设备会退化成 HomeKit Switch。
多键开关首版只接第一个按键；窗帘、除湿机、人体/门窗传感器等首版跳过。
其他类型按可用性做了降级映射：

- 摄像头：开关、移动传感器，以及移动侦测/宽动态/全彩夜视/移动追踪/时间水印子开关（不含视频流）。
- 扫地机：开关（开始清扫/停止回充）、电量、滤芯寿命/复位，以及地毯增压/自动集尘/
  自动烘干/请勿打扰/AI 清扫/童锁/报警音子开关。
- 宠物喂食器：开关（打开出粮一份，状态会自动回弹）、童锁。
- 宠物饮水机：开关（开=恒定出水，关=自动模式）、电量、滤芯寿命/复位、童锁、请勿打扰。
- 音箱：音量、静音、播放/暂停开关、睡眠模式。
- 水暖毯：温控、童锁、除螨、报警音。
- 洗碗机：开关映射开始/停止洗涤。
- 体脂秤：暂不支持（MIoT 只上报事件，没有可读的重量属性）。

支持的米家局域网型号与属性表在 `src/homekit_one/providers/mijia/models.py`：

| 型号 | 类型 | 暴露能力 |
| --- | --- | --- |
| `zhimi.airpurifier.rma1` | 净化器 | 开关、自动/手动、风速（最爱档）、PM2.5、温湿度、滤芯、童锁、显示屏 |
| `dmaker.fan.p28` | 循环扇 | 开关、风速 1-100、摆风、温湿度、童锁、指示灯 |
| `chuangmi.plug.m3` | 插座 | 开关、用电状态、温度、指示灯 |
| `yeelink.light.lamp4` | 灯 | 开关、亮度、色温 2600-5000K |

树新风暴露：开关、自动/手动/极净/睡眠、风速 7 档、PM2.5、甲醛/VOC、室温、湿度、滤芯寿命/剩余天数、UV 杀菌、负离子、提示音、智能托管、夜间智能、童锁、显示屏和滤芯复位。

## 米家云诊断（在外面也能用）

```bash
# 1. 先做一次 OAuth 授权（只需一次，token 会自动刷新）
.venv/bin/homekit-one mijia-oauth-login

# 2. 列出账号下所有设备（含中枢子设备）
.venv/bin/homekit-one mijia-cloud-list

# 3. 同时读取 MIoT-Spec 映射、原始属性和 actions/events，便于确认为什么某台设备没被接进来
.venv/bin/homekit-one mijia-cloud-list --specs --props --actions

# JSON 输出
.venv/bin/homekit-one mijia-cloud-list --specs --json
```

只需要笔记本能上外网，不要求在家里的局域网。设备本身要在线。

如果 OAuth token 失效，重新运行 `homekit-one mijia-oauth-login` 即可。
只有在你明确想用旧版登录时才需要 `-u/--username`；密码登录近年经常被小米以 403 拒绝。

## 树新风真机诊断

支持从终端登录、列设备、看原始字段，`--save` 会顺便把账号保存到
`secrets.json` 并启用 provider（密码不会打印）。

### 树新风 Treeow

```bash
# 列出家庭/分组下的设备、原始属性、映射后的 capability
.venv/bin/homekit-one treeow-list -u 你的Treeow手机号 --save

# 如果 App 里改过密码，忽略已保存密码并重新输入：
.venv/bin/homekit-one treeow-list --relogin --save
```

重点看 `mapped=` 这一行是否包含 `power/mode/fan_speed/pm25/temperature/humidity/filter_life`。
如果某台设备 `raw=` 里的字段名和预期不同，把这一行发我，我补映射。

## 米家 token

```bash
# 发现局域网设备（新固件通常只返回 IP / did，token 需要云导入）
.venv/bin/homekit-one discover

# 已做过小米 OAuth 授权：直接导入 token，不需要密码（推荐）
.venv/bin/homekit-one mijia-import --oauth

# 旧版账号密码导入（两步验证账号可能失败）
.venv/bin/homekit-one mijia-import -u 你的小米账号
```

Web 配置页也提供同样的入口：先完成小米 OAuth 授权，再点「从已授权的小米账号导入 token」。
扫描结果可以一键填入添加表单。token 错误时保存验证会直接提示，日志里也会显示
`token 校验失败`。

## HomeKit

- 配对码默认随机生成并写入 `pairing.json`，重启后保持不变；也可以在配置里固定：
  ```toml
  [gateway]
  homekit_pincode = "031-45-678"
  ```
- HomeKit 默认自动使用物理局域网地址（避开 VPN/utun），监听 `0.0.0.0:51234`。
  如果 Mac 有多个网段导致手机连不上，可以显式指定：
  ```toml
  [gateway]
  homekit_address = "192.168.1.86"
  ```
- 查看配对码 / URI：`homekit-one pairing`，或 Web「HomeKit」页。
- 重置配对：Web 页「清除所有配对并重启」，或删除 `accessory.state` 后重启。
- 外网访问需要 HomePod / Apple TV 作为家居中枢；Mac 休眠时设备会显示无响应。
- 如果修改配件结构或网络后家庭 App 全部显示“无响应”：先完全退出家庭 App 再打开，
  并重启 HomePod / Apple TV 中枢；仍不恢复时，在家庭 App 中删除 HomeKit One 桥接，
  再打开 `/pairing` 重新扫码配对。

## launchd 常驻

```bash
./scripts/install.sh     # 创建 venv、安装依赖、写入 LaunchAgent、启动
launchctl kickstart -k gui/$(id -u)/com.homekitone.gateway   # 重启
./scripts/uninstall.sh   # 停止并移除
tail -f ~/Library/Logs/homekit-one.log
```

如果项目位于 `/Volumes/...` 外置磁盘，launchd 默认没有该磁盘的 TCC 权限，Python 会在启动
阶段卡住。`install.sh` 会自动把 venv 和 `homekit_one` 包复制到本机磁盘的
`~/Library/Application Support/HomeKitOne/runtime`，LaunchAgent 从那里运行。
修改代码后同步并重启服务：

```bash
./scripts/sync-local-runtime.sh
```

macOS 的「本地网络」权限：首次运行建议先在终端前台跑一次 `homekit-one run`，允许系统弹窗；
launchd 后台进程如果被拦截，家庭 App 会搜不到网关。必要时在
**系统设置 → 隐私与安全性 → 本地网络** 里允许 Python / 终端。

## HomeKit 服务映射

HomeKit 没有通用的「扫地机 / 洗碗机 / 喂食器 / 体脂秤 / 摄像头视频流」服务，因此按最接近的
语义做映射：

| 设备/功能 | HomeKit service | 说明 |
| --- | --- | --- |
| 空调 | `HeaterCooler` | 模式、目标温度、风速、摆风 |
| 净化器 | `AirPurifier` + `AirQualitySensor` + `FilterMaintenance` + 温湿度 | 原生净化器服务 |
| 灯 | `Lightbulb` | 开关、亮度、色温 |
| 插座 | `Outlet` | 开关、用电状态、温度 |
| 风扇 | `Fanv2` | 开关、风速、摆风 |
| 温控/水暖毯 | `Thermostat` | 目标温度、当前温度、开关 |
| 电视 | `Television` + `TelevisionSpeaker` + `InputSource` | 电源、音量、静音、信源 |
| 音箱 | `Speaker` + `Switch` | 音量、静音；Switch 做播放/暂停 |
| 摄像头 | `Switch` + `MotionSensor` | 没有视频流；只有电源和移动侦测 |
| 扫地机 | `Switch` + `BatteryService` + `FilterMaintenance` | 开关=开始清扫/停止回充 |
| 宠物喂食器 | `Switch` | 打开=出粮一份 |
| 宠物饮水机 | `Valve`（水龙头类型）+ `BatteryService` + `FilterMaintenance` | 开=恒定出水，关=自动模式（设备无硬关断） |
| 洗碗机 | `Switch` | 开关=开始/停止洗涤 |
| UV/负离子/提示音/显示屏等 | `Switch` | HomeKit 没有专用服务，只能降级为开关 |
| 体脂秤 | — | 没有可读重量属性，也没有对应服务 |

额外开关必须是**独立配件**才能有可读名字；同一配件里的多个 switch 会共用配件名。
默认 `extra_switches = "minimal"`：HomeKit 只保留原生服务和只读参数，额外开关仅对
台灯/电暖毯开放；`auto` 会为所有设备暴露少量常用开关，`all` 暴露全部，`none` 全部关闭。
其余功能在 Web 设备详情页配置。

只读参数会尽量聚合：`AirQualitySensor` 本身承载 AirQuality/PM2.5/VOC；温度、湿度、
电量、滤芯、移动传感器等 service 会 `linked` 到主 service，Home App 可以把它们收在
同一个配件下。HomeKit 没有通用的多值传感器，如果 Home App 仍拆成独立卡片，可以关闭
「显示为独立卡片」。Web 设备页则统一按「只读 / 可控」两组展示。

## 命令行

```bash
homekit-one setup [--config PATH] [--no-homekit] [--web-port 8080]   # 启动并打开配置页
homekit-one run [--config PATH] [--no-homekit] [--web-port 8080] [--open]
homekit-one discover [--timeout 3]
homekit-one mijia-oauth-login [--no-browser] [--code CODE|CALLBACK_URL]
homekit-one mijia-cloud-list [--oauth] [--specs] [--props] [--actions] [--json]
homekit-one treeow-list [-u ACCOUNT] [--relogin] [--save] [--json]
homekit-one mijia-import [--oauth]
homekit-one mijia-import -u USER [-p PASS] [--country cn]
homekit-one pairing [--config PATH]
homekit-one doctor [--config PATH] [--probe]
```

`doctor` 会检查配置、状态目录可写性、Web/HomeKit 端口占用、配对状态、`secrets.json`
权限和每个 provider 的凭据完整性；加 `--probe` 会实际登录并列出设备。

`mijia-oauth-login` 的提示符支持直接回车读取 macOS 剪贴板里的回调地址；
也可以用 `--code` 直接传入。

## 测试与开发

```bash
.venv/bin/pytest -q          # 104 passed
.venv/bin/ruff check src tests
```

测试覆盖：miIO 加密/解密与发现、四个米家型号的读写映射、小米云设备列表与 MIoT-Spec
解析、云端属性读写、Treeow 属性映射、配置校验、设备注册、
Manager 轮询/回滚、HomeKit 服务构建与读写链路、Web API，以及接入向导的
provider 探测、OAuth 回调、短信登录和局域网发现。

调试：

```bash
# 只启动 Web，不占用 HomeKit 端口
homekit-one run --no-homekit

# 用临时状态目录，避免污染真实配对
homekit-one run --config /tmp/homekit-one-test.toml
```

## 已知边界

1. **树新风只能走云**。厂商没有公开局域网协议；断网时设备离线，米家仍可用。
   米家云 provider 也依赖小米云；中枢本地 MQTT 控制是后续优化方向。
2. **HomeKit 设备集在启动时固定**。Web 添加设备后需要重启；launchd 会自动拉起，前台运行需手动重启。
3. **米家局域网只支持 4 个静态型号**（见 `models.py`）；其他 Wi-Fi 设备走米家云，
   BLE / Zigbee 子设备需要中枢在线并由米家云接入。
4. **米家云的扩展类型是降级映射**：摄像头暂不接入视频流（小米云返回的 RTSP/HLS
   地址在当前网络下不可用；快照接口可用），只有开关和移动传感器；
   扫地机/喂食器/饮水机是开关级控制，音箱只有音量/静音/播放开关，体脂秤暂不支持。
5. **HomeKit 不是 MFi 认证实现**。HAP-python 能完成配对与控制，但不保证通过 Apple 认证。
6. **树新风云接口可能随 App 更新失效**；日志会保留原始错误，更新对应 client 即可。
7. **Web UI 默认只监听 127.0.0.1**。改成 `0.0.0.0` 等于把账号配置权限开放给整个局域网。

详细里程碑、验收记录和真机联调清单见 [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)。

## 参考与许可

本项目原创代码使用 MIT License。参考项目（均为 MIT）：

- [iPocket-APP/MijiaLAN](https://github.com/iPocket-APP/MijiaLAN)：miIO 报文格式、发现流程、能力/HomeKit 映射思路。
- [hlhk2017/treeow-homeassistant](https://github.com/hlhk2017/treeow-homeassistant)：Treeow 登录、设备列表、属性读写。
- [ikalchev/HAP-python](https://github.com/ikalchev/HAP-python)：HAP 服务端。
- [Squachen/micloud](https://github.com/Squachen/micloud)：小米云 token 导入。

`Lakr233/TreeNewBee` 仅为一个玩笑仓库，未包含设备协议代码，本项目未使用其实现。

本项目与小米、树新风、Apple 均无隶属关系，也未获得 HomeKit / MFi 认证。
