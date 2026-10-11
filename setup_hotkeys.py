#!/usr/bin/env python3
"""Apply saved GNOME shortcuts, preserving other applications' bindings."""
import core
import hotkeys
from hotkeys import existing_paths


def main():
    try:
        values = hotkeys.apply(core.load_config()['hotkeys'])
        for name, binding in values.items():
            print(hotkeys.ACTIONS[name][0] + ': ' + (binding or '未启用'))
    except (core.AppError, ValueError) as exc:
        print(str(exc))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
