# HomeOne

在 Mac mini 上常驻运行的 **HomeKit 网关**，把三类设备统一接入 Apple「家庭」：

- **米家 Wi-Fi 设备**：局域网 miIO / MIoT（UDP 54321），不需要小米云参与日常控制。
- **米家云端设备**：小米云 `/app/v2/miotspec` 读写，覆盖中枢下的 Zigbee / BLE Mesh 子设备。
- **TCL Home 家电**：TCL 云端（账号 → SaaS token → AWS IoT shadow），首版支持分体空调。
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
│ Mijia │ │ TCL Cloud │ │  Treeow  │ │
│ LAN   │ │ AWS IoT   │ │  Cloud   │ │
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
src/homeone/
  app.py                  # Gateway 编排：providers -> manager -> HomeKit + Web
  cli.py                  # run / discover / mijia-import / pairing / doctor
  config.py               # TOML 配置
  secrets.py              # 0600 secrets.json
  registry.py             # Web UI 添加的米家设备
  core/manager.py         # 轮询、命令、状态缓存、监听器
  core/model.py           # Capability / Device / Command
  providers/mijia/        # 局域网 miIO：加密、UDP 客户端、发现、型号映射、token 导入
  providers/mijia_cloud/  # 小米云：登录、设备列表、MIoT-Spec 解析、属性读写
  providers/tcl/          # 登录 / cloud URLs / Cognito / AWS IoT shadow / 分体空调映射
  providers/treeow/       # 登录 / 设备列表 / 属性读写
  homekit/bridge.py       # HAP 驱动、配对、二维码、重置
  homekit/accessories.py  # capability -> HAP service/characteristic
  web/                    # FastAPI + Jinja2 配置台
scripts/                  # launchd 安装/卸载
tests/                    # 48 个单元测试
```

## 快速开始

要求：macOS 15+（已在 macOS 27 / Apple Silicon 验证），`uv`，Python 3.12（uv 会自动下载）。

```bash
cd ~/PersonalGit/home-one
uv venv --python 3.12
uv pip install -e ".[dev]"

# 第一次先无配置启动：4 台演示设备 + Web + HomeKit 二维码
.venv/bin/homeone run
```

打开 <http://127.0.0.1:8080>：

1. 「配置」页添加米家云账号（中枢子设备）、TCL / 树新风账号，或米家局域网设备。
2. 点「重启网关」。
3. 「HomeKit」页用 iPhone「家庭」App 扫码配对。

没有真实设备时，删除配置文件并重启即可回到演示模式。

## 配置

```bash
mkdir -p ~/.config/homeone
cp config.example.toml ~/.config/homeone/config.toml
```

状态目录默认 `~/Library/Application Support/HomeOne`：

| 文件 | 内容 |
| --- | --- |
| `secrets.json` | TCL / Treeow 账号密码、米家 token、米家 OAuth token（0600） |
| `devices.json` | Web UI 添加的米家设备（0600） |
| `accessory.state` | HomeKit 配对客户端与私钥（0600）；删除后需要重新配对 |
| `pairing.json` | 稳定的 HomeKit 配对码（0600）；与 `accessory.state` 一起删除会重新生成 |
| `tcl_session.json` | TCL token / AWS 临时凭据缓存（0600） |

凭据优先级：环境变量 > `secrets.json` > `config.toml`。

### 米家云（中枢子设备）

`[providers.mijia_cloud]` 用于没有 IP/token 的中枢子设备。它按设备列表里的
MIoT-Spec URN 自动识别常见属性（开关、亮度、色温、模式、风速、温度、湿度、
PM2.5、滤芯、童锁），不需要为每个型号写属性表。

**推荐先做 OAuth 授权**（小米已限制旧版密码登录，即使没开两步验证也可能返回 403）：

```bash
homeone mijia-oauth-login
```

流程：

1. 终端会打印并在浏览器打开小米授权页；
2. 用小米账号登录并授权（支持两步验证）；
3. 浏览器会跳转到 `http://homeassistant.local:8123`，页面打不开是正常的；
4. 复制地址栏完整 URL（含 `code=...`）或直接粘贴 code 到终端；
5. token 保存到 `secrets.json`，之后 `homeone mijia-cloud-list` 和网关自动使用。

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
多键开关首版只接第一个按键；窗帘、除湿机、扫地机、人体/门窗传感器等首版跳过。
摄像头、音箱、体脂秤、扫地机、宠物喂食器/饮水机没有对应的 HomeKit 服务，
需要一个设备一个策略；用 `mijia-cloud-list --props --actions` 可以看到它们的
属性和可调用动作，再决定是否值得加专用开关/按钮。

支持的米家局域网型号与属性表在 `src/homeone/providers/mijia/models.py`：

| 型号 | 类型 | 暴露能力 |
| --- | --- | --- |
| `zhimi.airpurifier.rma1` | 净化器 | 开关、自动/手动、风速（最爱档）、PM2.5、温湿度、滤芯、童锁、显示屏 |
| `dmaker.fan.p28` | 循环扇 | 开关、风速 1-100、摆风、温湿度、童锁、指示灯 |
| `chuangmi.plug.m3` | 插座 | 开关、用电状态、温度、指示灯 |
| `yeelink.light.lamp4` | 灯 | 开关、亮度、色温 2600-5000K |

TCL 首版仅启用 **Split AC / Split AC Fresh air**（其他 TCL 品类字段不同，暂不发送猜测命令）。暴露：开关、模式（自动/制冷/制热/除湿/送风）、目标温度、当前温度、风速、上下摆风。
`workMode` 数值在不同固件上可能不同，可在配置里覆盖：

```toml
[providers.tcl.work_modes]
auto = 0
cool = 1
dry = 2
fan = 3
heat = 4
```

树新风首版暴露：开关、自动/手动/极净/睡眠、风速 7 档、PM2.5、室温、湿度、滤芯寿命、童锁、显示屏。

## 米家云诊断（在外面也能用）

```bash
# 1. 先做一次 OAuth 授权（只需一次，token 会自动刷新）
.venv/bin/homeone mijia-oauth-login

# 2. 列出账号下所有设备（含中枢子设备）
.venv/bin/homeone mijia-cloud-list

# 3. 同时读取 MIoT-Spec 映射、原始属性和 actions/events，便于确认为什么某台设备没被接进来
.venv/bin/homeone mijia-cloud-list --specs --props --actions

# JSON 输出
.venv/bin/homeone mijia-cloud-list --specs --json
```

只需要笔记本能上外网，不要求在家里的局域网。设备本身要在线。

如果 OAuth token 失效，重新运行 `homeone mijia-oauth-login` 即可。
只有在你明确想用旧版登录时才需要 `-u/--username`；密码登录近年经常被小米以 403 拒绝。

## 树新风 / TCL 真机诊断

两个 provider 都支持从终端登录、列设备、看原始字段，`--save` 会顺便把账号
保存到 `secrets.json` 并启用对应 provider（密码不会打印）。

### 树新风 Treeow

```bash
# 列出家庭/分组下的设备、原始属性、映射后的 capability
.venv/bin/homeone treeow-list -u 你的Treeow手机号 --save
```

重点看 `mapped=` 这一行是否包含 `power/mode/fan_speed/pm25/temperature/humidity/filter_life`。
如果某台设备 `raw=` 里的字段名和预期不同，把这一行发我，我补映射。

### TCL

```bash
# 列出账号下所有 TCL 设备（不只分体空调）和 shadow 字段
.venv/bin/homeone tcl-list -u 你的TCL账号 --save

# 只看某台，避免输出太长
.venv/bin/homeone tcl-list -u 你的TCL账号 --device 1234567890
```

- 分体空调会显示 `mapped=`，可直接被 HomeKit 接入。
- 除湿机、Breeva 净化器、移动/窗机等会显示 `type=` 和 `reported=`/`delta=`，
  首版还没有映射；把对应输出发我，我按字段补。
- `workMode` 数值不对时，用 `[providers.tcl.work_modes]` 覆盖。

`--save` 后重启网关即可在 HomeKit 里看到已支持的设备。

## TCL 国内版 TCL+（电视/空调）

TCL 有两套完全不同的云：

- **国际版 TCL Home**：`pa.account.tcl.com` / AWS IoT，原来的 `tcl-list` 走这条；
- **国内版 TCL+（微信小程序 / 国内 App）**：`cn.account.tcl.com` / `io.zx.tcljd.com`。
  国内手机号在国际版会报 `Invalid phone number`。

国内版用这个诊断命令：

```bash
# 手机号 + 密码（RSA 加密，和 TCL+ App 一致）
.venv/bin/homeone tcl-plus-list -u 13800138000 --panel

# 或者短信验证码
.venv/bin/homeone tcl-plus-list -u 13800138000 --sms --panel

# 或者用抓包得到的 token
.venv/bin/homeone tcl-plus-list --account-id xxx --refresh-token yyy --panel
```

输出重点看：

```text
deviceId  name  category=... productKey=... online=... weChatControl=1
    status={"power":..., "volume":...}
    panel power type=bool title=电源 specs=...
```

- `weChatControl=1` 表示小程序可控；0 的设备无法通过这条云链路控制。
- `status` 是设备当前属性，`panel` 是面板定义（`identifier/type/specs`）。
- 把电视那台的 `status` + `panel` 发我，我按实际字段接 HomeKit `Television`（电源/音量/信源/遥控键，取决于字段）。

> 国内版协议参考 Apache-2.0 项目 [ndwzy/tcl-ha](https://github.com/ndwzy/tcl-ha)。

### TCL+ 电视接入

`tcl-plus-list` 确认字段后，加 `--save` 保存凭据并启用 `tcl_plus` provider：

```bash
.venv/bin/homeone tcl-plus-list -u 13800138000 --panel --save
```

重启网关后，HomeKit 会出现一个 Television 配件：

- 电源（`powerSwitch`）
- 音量 / 静音（`volume` / `mute`）
- 信源（`source` + `sourceList` → InputSource 服务）
- 遥控按键暂未接入（需要更多 action/key 字段）

注意：

- `weChatControl=0` 或 `online=0` 时，云端控制可能不可用。先把电视开机、联网，
  再运行 `tcl-plus-list` 确认这两个值；如果仍为 0，TCL+ 小程序本身也不可控。
- 电视离线时 provider 会标记离线，不会把最后一次状态当成在线。
- `online=0` 时 `status` 可能是最后一次缓存值，不要据此判断实时状态。

## 米家 token

```bash
# 发现局域网设备（通常只返回 IP / did，token 需要云导入）
.venv/bin/homeone discover

# 从小米云导入（会写入 state_dir/devices.json + secrets.json）
.venv/bin/homeone mijia-import -u 你的小米账号
```

两步验证账号可能无法用 `micloud` 登录；此时在 Web UI 手动填写 IP + 32 位 token。
token 错误时日志会显示 `token 校验失败`。

## HomeKit

- 配对码默认随机生成并写入 `pairing.json`，重启后保持不变；也可以在配置里固定：
  ```toml
  [gateway]
  homekit_pincode = "031-45-678"
  ```
- 查看配对码 / URI：`homeone pairing`，或 Web「HomeKit」页。
- 重置配对：Web 页「清除所有配对并重启」，或删除 `accessory.state` 后重启。
- 外网访问需要 HomePod / Apple TV 作为家居中枢；Mac 休眠时设备会显示无响应。

## launchd 常驻

```bash
./scripts/install.sh     # 创建 venv、安装依赖、写入 LaunchAgent、启动
launchctl kickstart -k gui/$(id -u)/com.homeone.gateway   # 重启
./scripts/uninstall.sh   # 停止并移除
tail -f ~/Library/Logs/homeone.log
```

macOS 的「本地网络」权限：首次运行建议先在终端前台跑一次 `homeone run`，允许系统弹窗；
launchd 后台进程如果被拦截，家庭 App 会搜不到网关。必要时在
**系统设置 → 隐私与安全性 → 本地网络** 里允许 Python / 终端。

## 命令行

```bash
homeone run [--config PATH] [--no-homekit] [--web-port 8080]
homeone discover [--timeout 3]
homeone mijia-oauth-login [--no-browser]
homeone mijia-cloud-list [--oauth] [--specs] [--props] [--actions] [--json]
homeone treeow-list [-u ACCOUNT] [--save] [--json]
homeone tcl-list [-u USER] [--device ID] [--no-shadow] [--save] [--json]
homeone tcl-plus-list [-u PHONE] [--sms] [--account-id ID --refresh-token TOKEN] [--panel] [--save] [--json]
homeone mijia-import -u USER
homeone mijia-import -u USER [-p PASS] [--country cn]
homeone pairing [--config PATH]
homeone doctor [--config PATH]
```

`doctor` 会检查配置、状态目录可写性、Web/HomeKit 端口占用、配对状态和启用的 provider。

## 测试与开发

```bash
.venv/bin/pytest -q          # 71 passed
.venv/bin/ruff check src
```

测试覆盖：miIO 加密/解密与发现、四个米家型号的读写映射、小米云设备列表与 MIoT-Spec
解析、云端属性读写、TCL shadow 编解码、Treeow 属性映射、配置校验、设备注册、
Manager 轮询/回滚、HomeKit 服务构建与读写链路、Web API。

调试：

```bash
# 只启动 Web，不占用 HomeKit 端口
homeone run --no-homekit

# 用临时状态目录，避免污染真实配对
homeone run --config /tmp/homeone-test.toml
```

## 已知边界

1. **TCL / 树新风只能走云**。厂商没有公开局域网协议；断网时这两类设备离线，米家仍可用。
   米家云 provider 也依赖小米云；中枢本地 MQTT 控制是后续优化方向。
2. **TCL 是社区逆向工程**，存在账号被限制的风险。建议注册单独的 TCL Home 账号，把设备共享给它。
3. **HomeKit 设备集在启动时固定**。Web 添加设备后需要重启；launchd 会自动拉起，前台运行需手动重启。
4. **TCL 风速没有“自动”档**。HomeKit HeaterCooler 没有 fan-auto，当前把 `windSpeed=0` 报成 50%；
   手动设风速会切到手动档。模式里的除湿/送风在 HomeKit 侧映射为制冷图标。
5. **TCL `workMode` 数值因固件而异**，默认 0/1/2/3/4 = 自动/制冷/除湿/送风/制热；不对时用配置覆盖。
6. **米家只支持 4 个首版型号**，且仅 Wi-Fi 设备；BLE / Zigbee 子设备不支持。新增型号需要在
   `models.py` 增加 `ModelSpec`（siid/piid 可从 MIoT spec 查）。
7. **HomeKit 不是 MFi 认证实现**。HAP-python 能完成配对与控制，但不保证通过 Apple 认证。
8. **树新风/TCL 云接口可能随 App 更新失效**；日志会保留原始错误，更新对应 client 即可。
9. **Web UI 默认只监听 127.0.0.1**。改成 `0.0.0.0` 等于把账号配置权限开放给整个局域网。

详细里程碑、验收记录和真机联调清单见 [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)。

## 参考与许可

本项目原创代码使用 MIT License。参考项目（均为 MIT）：

- [iPocket-APP/MijiaLAN](https://github.com/iPocket-APP/MijiaLAN)：miIO 报文格式、发现流程、能力/HomeKit 映射思路。
- [nemesa/ha-tcl-home-unofficial-integration](https://github.com/nemesa/ha-tcl-home-unofficial-integration)：TCL 登录、cloud URLs、Cognito、AWS IoT shadow、设备字段。
- [hlhk2017/treeow-homeassistant](https://github.com/hlhk2017/treeow-homeassistant)：Treeow 登录、设备列表、属性读写。
- [ikalchev/HAP-python](https://github.com/ikalchev/HAP-python)：HAP 服务端。
- [Squachen/micloud](https://github.com/Squachen/micloud)：小米云 token 导入。

`Lakr233/TreeNewBee` 仅为一个玩笑仓库，未包含设备协议代码，本项目未使用其实现。

本项目与小米、TCL、树新风、Apple 均无隶属关系，也未获得 HomeKit / MFi 认证。
