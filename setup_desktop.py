#!/usr/bin/env python3
"""Create a launcher. Autostart is opt-in via --autostart."""
import argparse
import os
from pathlib import Path
import sys


def quote_exec(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--autostart", action="store_true")
    args = parser.parse_args()
    app = Path(__file__).resolve().with_name("app.py")
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    applications = data_home / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop = (
        "[Desktop Entry]\nType=Application\nName=TextRefine\n"
        "Comment=AI text rewriting and prompt optimization\n"
        "Exec=" + quote_exec(sys.executable) + " " + quote_exec(str(app)) + "\n"
        "Icon=accessories-text-editor\nTerminal=false\nCategories=Utility;Office;\n"
    )
    (applications / "textrefine.desktop").write_text(desktop, encoding="utf-8")
    if args.autostart:
        autostart = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autostart"
        autostart.mkdir(parents=True, exist_ok=True)
        (autostart / "textrefine.desktop").write_text(desktop, encoding="utf-8")
    print("已添加应用菜单入口。请保留当前代码文件夹位置；移动后重新运行本脚本。")


if __name__ == "__main__":
    main()
