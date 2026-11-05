#!/bin/bash
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.homekitone.gateway.plist"
LABEL="com.homekitone.gateway"
OLD_PLIST="$HOME/Library/LaunchAgents/com.homeone.gateway.plist"
OLD_LABEL="com.homeone.gateway"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
# Clean up the pre-rename service as well.
launchctl bootout "$DOMAIN/$OLD_LABEL" 2>/dev/null || true
rm -f "$OLD_PLIST"
echo "已停止并移除 $LABEL。"
echo "配置和状态仍保留在 ~/.config/homekit-one 与 ~/Library/Application Support/HomeKit One。"
echo "旧版本目录（如果存在）同样未被删除：~/.config/homeone 与 ~/Library/Application Support/HomeOne。"
echo "如需彻底删除："
echo "  rm -rf ~/.config/homekit-one \"$HOME/Library/Application Support/HomeKit One\""
echo "  rm -rf ~/.config/homeone \"$HOME/Library/Application Support/HomeOne\""
