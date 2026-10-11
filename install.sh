#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [[ "$(id -u)" -eq 0 ]]; then
  echo '请使用普通桌面用户执行：bash install.sh。脚本只在 apt 安装时使用 sudo。' >&2
  exit 1
fi
sudo apt-get update
sudo apt-get install -y python3 python3-tk wl-clipboard xclip xdotool libsecret-tools
python3 "$APP_DIR/setup_desktop.py"
python3 "$APP_DIR/setup_hotkeys.py"
echo '安装完成。运行 python3 app.py，或从应用菜单打开 TextRefine。'
