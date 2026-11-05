#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
CONFIG="${HOMEKIT_ONE_CONFIG:-${HOMEONE_CONFIG:-$HOME/.config/homekit-one/config.toml}}"
if [[ ! -f "$CONFIG" && -f "$HOME/.config/homeone/config.toml" ]]; then
  CONFIG="$HOME/.config/homeone/config.toml"
fi
LOG="$HOME/Library/Logs/homekit-one.log"
PLIST="$HOME/Library/LaunchAgents/com.homekitone.gateway.plist"
LABEL="com.homekitone.gateway"
OLD_PLIST="$HOME/Library/LaunchAgents/com.homeone.gateway.plist"
OLD_LABEL="com.homeone.gateway"
DOMAIN="gui/$(id -u)"

if [[ ! -x "$PYTHON" ]]; then
  echo "==> 创建 Python 3.12 虚拟环境"
  (cd "$REPO" && uv venv --python 3.12)
fi

echo "==> 安装依赖"
(cd "$REPO" && uv pip install --python "$PYTHON" -e .)
# pip does not remove console scripts from the pre-rename install.
rm -f "$REPO/.venv/bin/homeone"

WORKDIR="$REPO"
if [[ "$REPO" == /Volumes/* ]]; then
  # launchd agents cannot read external volumes without Full Disk Access.
  # Run the service from a copy on the boot volume instead.
  RUNTIME="${HOMEKIT_ONE_RUNTIME:-$HOME/Library/Application Support/HomeKitOne/runtime}"
  echo "==> 复制运行环境到 $RUNTIME"
  mkdir -p "$RUNTIME"
  rsync -a --exclude '__pycache__' "$REPO/.venv/" "$RUNTIME/.venv/"
  PYTHON="$RUNTIME/.venv/bin/python"
  SITE="$("$PYTHON" -c 'import site; print(site.getsitepackages()[0])')"
  rm -f "$SITE"/_editable_impl_homekit_one.pth "$SITE"/_editable_impl_homeone.pth
  rm -rf "$SITE/homekit_one"
  cp -R "$REPO/src/homekit_one" "$SITE/homekit_one"
  find "$SITE/homekit_one" -name '__pycache__' -type d -prune -exec rm -rf {} +
  WORKDIR="$RUNTIME"
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "==> 生成默认配置 $CONFIG"
  mkdir -p "$(dirname "$CONFIG")"
  cp "$REPO/config.example.toml" "$CONFIG"
fi

mkdir -p "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"

echo "==> 写入 LaunchAgent $PLIST"
sed \
  -e "s|__PYTHON__|$PYTHON|g" \
  -e "s|__CONFIG__|$CONFIG|g" \
  -e "s|__REPO__|$WORKDIR|g" \
  -e "s|__LOG__|$LOG|g" \
  "$REPO/scripts/com.homekitone.gateway.plist.template" > "$PLIST"

echo "==> 重新加载服务"
# Remove the pre-rename LaunchAgent so two gateways do not fight over port 51234.
launchctl bootout "$DOMAIN/$OLD_LABEL" 2>/dev/null || true
rm -f "$OLD_PLIST"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo
echo "已安装。Web 配置台: http://127.0.0.1:$(grep -E '^web_port' "$CONFIG" | head -1 | grep -oE '[0-9]+' || echo 8080)"
echo "日志: $LOG"
echo "首次使用：打开配置台 → 选择品牌登录（保存时会立即验证并列出设备）→"
echo "在页面顶部点「立即重启」→ 到 HomeKit 页扫码配对。"
echo "也可以随时手动重启服务："
echo "  launchctl kickstart -k $DOMAIN/$LABEL"
echo
echo "macOS 可能询问“本地网络”权限。若家庭 App 搜不到网关，请在"
echo "系统设置 → 隐私与安全性 → 本地网络 中允许 Python/终端，或先在终端前台运行一次"
echo "  $PYTHON -m homekit_one run --config \"$CONFIG\""
