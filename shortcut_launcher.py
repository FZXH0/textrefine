#!/usr/bin/env python3
"""Log only shortcut action and timestamp, then run the existing application."""
from datetime import datetime
import os
from pathlib import Path
import sys


def main():
    allowed = {"--capture", "--menu", "--clipboard", "--show", "--regenerate"}
    if len(sys.argv) != 2 or sys.argv[1] not in allowed:
        raise SystemExit(2)
    directory = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / ("textrefine-" + str(os.getuid()))
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_uid != os.getuid():
            raise OSError("invalid runtime directory")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(directory / "shortcut-events.log", flags, 0o600)
        with os.fdopen(fd, "w") as log:
            log.write(datetime.now().isoformat(timespec="seconds") + " " + sys.argv[1] + "\n")
    except OSError:
        pass
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve().with_name("app.py")), sys.argv[1]])


if __name__ == "__main__":
    main()
