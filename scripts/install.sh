#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$REPO/.venv/bin/python"
CONFIG="${HOMEONE_CONFIG:-$HOME/.config/homeone/config.toml}"
LOG="$HOME/Library/Logs/homeone.log"
PLIST="$HOME/Library/LaunchAgents/com.homeone.gateway.plist"
LABEL="com.homeone.gateway"
DOMAIN="gui/$(id -u)"

if [[ ! -x "$PYTHON" ]]; then
  echo "==> 创建 Python 3.12 虚拟环境"
  (cd "$REPO" && uv venv --python 3.12)
fi

echo "==> 安装依赖"
(cd "$REPO" && uv pip install --python "$PYTHON" -e .)

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
  -e "s|__REPO__|$REPO|g" \
  -e "s|__LOG__|$LOG|g" \
  "$REPO/scripts/com.homeone.gateway.plist.template" > "$PLIST"

echo "==> 重新加载服务"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo
echo "已安装。Web 配置台: http://127.0.0.1:$(grep -E '^web_port' "$CONFIG" | head -1 | grep -oE '[0-9]+' || echo 8080)"
echo "日志: $LOG"
echo "首次使用 HomeKit 前，请打开 Web 配置台添加账号/设备，然后重启服务："
echo "  launchctl kickstart -k $DOMAIN/$LABEL"
echo
echo "macOS 可能询问“本地网络”权限。若家庭 App 搜不到网关，请在"
echo "系统设置 → 隐私与安全性 → 本地网络 中允许 Python/终端，或先在终端前台运行一次"
echo "  $PYTHON -m homeone run --config \"$CONFIG\""
