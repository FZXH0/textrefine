import copy
from pathlib import Path
import queue
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

import app
import core

with tempfile.TemporaryDirectory() as directory:
    with patch.object(core, "CONFIG_DIR", Path(directory) / "config"), patch.object(core, "DATA_DIR", Path(directory) / "data"), patch.object(core, "get_secret", return_value=""):
        root = tk.Tk()
        root.withdraw()
        application = app.Application(root, copy.deepcopy(core.DEFAULTS), queue.Queue())
        application.key.set("test-key")
        application.input_text.insert("1.0", "帮我写请假邮件")
        with patch.object(core, "transform", return_value="请撰写一封简洁的请假邮件。"):
            application.generate()
            deadline = time.monotonic() + 3
            while application.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(0.02)
        assert application.output_text.get("1.0", "end-1c") == "请撰写一封简洁的请假邮件。"
        assert application.input_text.get("1.0", "end-1c") == "帮我写请假邮件"
        with patch.object(core, "copy_clipboard") as clipboard:
            application.accept()
            clipboard.assert_called_once_with("请撰写一封简洁的请假邮件。")
        application.inbox.put(("result", (application.generation, "原文", "迟到结果", "优化 AI 提示词", copy.deepcopy(core.DEFAULTS))))
        application.cancel()
        application.output_text.delete("1.0", "end")
        application.poll()
        assert application.output_text.get("1.0", "end-1c") == ""
        application.close()
print("Tk GUI smoke: construction, generation, accept, stale-result cancellation OK")
