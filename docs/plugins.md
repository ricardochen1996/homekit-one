# Provider 插件

内置 provider 在 `src/homekit_one/providers/builtin.py` 里通过
`ProviderPlugin` 注册。第三方包可以在 `pyproject.toml` 声明入口点，无需修改
HomeKit One 主仓库：

```toml
[project.entry-points."homekit_one.providers"]
my_provider = "my_package.plugin:PLUGIN"
```

入口点可以返回一个 `ProviderPlugin`，也可以返回一个无参数、返回
`ProviderPlugin` 的工厂函数。

## 示例

```python
from pathlib import Path

from homekit_one.providers.base import Provider
from homekit_one.providers.plugins import ProviderPlugin, SetupField


class MyProvider(Provider):
    name = "my_provider"

    async def list_devices(self):
        ...

    async def refresh(self, device):
        ...

    async def execute(self, device, command):
        ...


def build(config, secrets, state_dir: Path, registry):
    section = secrets.section("my_provider")
    return MyProvider(section, state_dir)


PLUGIN = ProviderPlugin(
    name="my_provider",
    label="我的品牌",
    description="接入示例品牌的云端设备。",
    setup_fields=(
        SetupField("username", "账号", required=True),
        SetupField("password", "密码", secret=True, required=True),
    ),
    factory=build,
)
```

## 配置和凭据

- `setup_fields` 会变成 Web「插件 Provider」卡片里的表单。保存后字段写入
  `secrets.json` 的 `my_provider` section，并自动把
  `[providers.my_provider] enabled` 改为 `true`。
- 插件也可以直接读取 `config.provider_data["my_provider"]`，它对应
  `config.toml` 里原始的 `[providers.my_provider]`。
- `factory` 接收 `(config, secrets, state_dir, registry)`。返回 `None` 表示本次不启用。
- `Provider` 只需要实现 `list_devices()`、`refresh(device)`、`execute(device, command)`；
  `start()` / `stop()` 可选。
- 认证失败时抛 `ProviderError` / `AuthError`，Web 验证页会显示摘要和提示。

## 设备映射

Provider 只需要把自家属性翻译成 `Capability` / `DeviceKind`。HomeKit 层会按
`homekit_one/homekit/accessories.py` 的映射生成服务；如果新增了 HomeKit 不支持
的设备类型，使用 `Switch`、`Valve`、`Sensor` 等最接近的服务做降级映射。
