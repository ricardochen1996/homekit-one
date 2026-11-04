#!/bin/bash
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.homeone.gateway.plist"
LABEL="com.homeone.gateway"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "已停止并移除 $LABEL。"
echo "配置和状态仍保留在 ~/.config/homeone 与 ~/Library/Application Support/HomeOne。"
echo "如需彻底删除："
echo "  rm -rf ~/.config/homeone \"$HOME/Library/Application Support/HomeOne\""
