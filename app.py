#!/usr/bin/env python3
"""TextRefine: desktop text transformation utility for Ubuntu 22.04+."""
import argparse
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import core
import extras
import hotkeys
import workspace

MAX_IPC = 1_000_000


def runtime_directory():
    base = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    directory = base / ("textrefine-" + str(os.getuid()))
    if directory.is_symlink():
        raise core.AppError("运行目录不能是符号链接。")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.getuid():
        raise core.AppError("运行目录不属于当前用户。")
    os.chmod(directory, 0o700)
    return directory


def send_command(path, command):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(path))
            connection.sendall(json.dumps(command, ensure_ascii=False).encode() + b"\n")
            return connection.recv(16) == b"OK"
    except (OSError, ValueError):
        return False


class LocalServer:
    def __init__(self, path, inbox):
        self.path = path
        self.inbox = inbox
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # The caller owns a flock, so a leftover socket is safe to remove.
        if path.exists():
            if not path.is_socket():
                raise core.AppError("运行路径被其他文件占用：" + str(path))
            path.unlink()
        self.socket.bind(str(path))
        os.chmod(path, 0o600)
        self.socket.listen(8)
        self.socket.settimeout(1)
        self.stopped = threading.Event()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(3)
                data = bytearray()
                try:
                    while len(data) <= MAX_IPC:
                        chunk = connection.recv(8192)
                        if not chunk:
                            break
                        data.extend(chunk)
                        if b"\n" in chunk:
                            break
                    if len(data) > MAX_IPC:
                        continue
                    command = json.loads(data.split(b"\n", 1)[0])
                    if isinstance(command, dict) and command.get("action") in ("show", "menu", "capture", "regenerate"):
                        self.inbox.put(("command", command))
                        connection.sendall(b"OK")
                except (OSError, ValueError):
                    continue

    def close(self):
        self.stopped.set()
        self.socket.close()
        if self.path.exists():
            self.path.unlink()


class Application:
    def __init__(self, root, config, inbox):
        self.root, self.config, self.inbox = root, config, inbox
        self.server = None
        self.generation = 0
        self.busy = False
        self.target_window = ""
        self.last_original = ""
        self.versions = []
        self.version_index = -1
        self.last_request = None
        self.started_at = 0
        self.model_request = 0
        self.model_candidates = []
        self.session_keys = {}
        self.cancel_event = threading.Event()
        self.result_complete = False
        self.stream_buffer = []
        self.last_stream_flush = time.monotonic()
        self.temp_prompt = tk.StringVar()
        self.use_temp = tk.BooleanVar(value=False)
        self.stream_enabled = tk.BooleanVar(value=config.get("stream_enabled", True))
        self.diff_enabled = tk.BooleanVar(value=config.get("diff_enabled", True))
        self.hide_on_close = tk.BooleanVar(value=config.get("hide_on_close", False))
        root.title("TextRefine · AI 文本工具")
        root.geometry("1100x760")
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TButton", padding=(10, 6))
        style.configure("TNotebook.Tab", padding=(12, 8))
        root.minsize(1000, 740)
        self.status = tk.StringVar(value="准备就绪。选中文字后使用 GNOME 快捷键，或先复制再读取剪贴板。")
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=12, pady=12)
        work, settings, prompts, history = (ttk.Frame(tabs, padding=10) for _ in range(4))
        for frame, label in zip((work, settings, prompts, history), ("处理文字", "API 设置", "自定义提示词", "历史")):
            tabs.add(frame, text=label)
        self.tabs = tabs
        toolbar = ttk.Frame(work)
        toolbar.pack(fill="x", pady=(0, 8))
        self.prompt_name = tk.StringVar(value=config["default_prompt"])
        self.prompt_combo = ttk.Combobox(toolbar, textvariable=self.prompt_name, state="readonly", width=24,
                                         values=list(config["prompts"]))
        self.prompt_combo.pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="读取剪贴板", command=self.read_clipboard).pack(side="left", padx=4)
        self.run_button = ttk.Button(toolbar, text="生成 · Ctrl+Enter", command=self.generate)
        self.run_button.pack(side="left", padx=4)
        self.cancel_button = ttk.Button(toolbar, text="取消", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=4)
        ttk.Button(toolbar, text="清空", command=self.clear_work).pack(side="left", padx=4)
        quick = ttk.Frame(work)
        quick.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(quick, text="本次临时指令", variable=self.use_temp).pack(side="left")
        ttk.Entry(quick, textvariable=self.temp_prompt).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Checkbutton(quick, text="高亮改动", variable=self.diff_enabled, command=self.highlight_diff).pack(side="left")
        ttk.Button(quick, text="隐藏", command=root.withdraw).pack(side="left", padx=4)
        ttk.Button(quick, text="退出", command=self.close).pack(side="left", padx=4)
        self.progress = ttk.Progressbar(work, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 8))
        pane = ttk.Panedwindow(work, orient="horizontal")
        pane.pack(fill="both", expand=True)
        self.input_text = self.text_panel(pane, "原文")
        self.output_text = self.text_panel(pane, "结果 · 可编辑", copy_output=True)
        self.input_text.tag_configure("removed", background="#ffe0df", foreground="#6f1d1b")
        self.output_text.tag_configure("added", background="#d9f0df", foreground="#14532d")
        self.output_text.tag_configure("search", background="#ffe08a", foreground="#222222")
        self.output_text.tag_raise("sel")
        self.find_query = tk.StringVar()
        search = ttk.Frame(work)
        search.pack(fill="x", before=self.progress, pady=(0, 6))
        ttk.Label(search, text="查找输出").pack(side="left")
        self.find_entry = ttk.Entry(search, textvariable=self.find_query, width=18)
        self.find_entry.pack(side="left", padx=6)
        self.find_entry.bind("<Return>", lambda e: self.shortcut(self.find_next))
        ttk.Button(search, text="下一个", command=self.find_next, width=7).pack(side="left")
        ttk.Button(search, text="清除高亮", command=self.clear_search, width=8).pack(side="left", padx=4)
        self.previous_version_button = ttk.Button(search, text="上一版", command=lambda: self.switch_version(-1), width=6)
        self.previous_version_button.pack(side="left", padx=(8, 0))
        self.version_label = tk.StringVar(value="版本 0/0")
        ttk.Label(search, textvariable=self.version_label).pack(side="left", padx=6)
        self.next_version_button = ttk.Button(search, text="下一版", command=lambda: self.switch_version(1), width=6)
        self.next_version_button.pack(side="left")
        self.output_text.bind("<KeyRelease>", lambda e: self.schedule_diff())
        self.input_text.bind("<KeyRelease>", lambda e: self.schedule_diff())
        self.diff_timer = None
        self.counts = tk.StringVar()
        ttk.Label(work, textvariable=self.counts).pack(anchor="w", pady=(6, 2))
        self.hotkey_hint = tk.StringVar()
        ttk.Label(work, textvariable=self.hotkey_hint, wraplength=930).pack(anchor="w", pady=(0, 6))
        actions = ttk.Frame(work)
        actions.pack(fill="x")
        self.regenerate_button = ttk.Button(actions, text="重新生成", command=self.regenerate, state="disabled")
        self.regenerate_button.pack(side="left", padx=4)
        self.accept_button = ttk.Button(actions, text="接受：复制结果", command=self.accept, state="disabled")
        self.accept_button.pack(side="left", padx=4)
        ttk.Button(actions, text="导出 TXT", command=self.export_result).pack(side="left", padx=4)
        ttk.Button(actions, text="恢复上次原文到剪贴板", command=self.restore).pack(side="left", padx=4)
        ttk.Button(actions, text="拒绝：清空结果", command=self.clear_result).pack(side="left", padx=4)
        self.base_url = tk.StringVar(value=config["base_url"])
        self.model = tk.StringVar(value=config["model"])
        self.protocol = tk.StringVar(value=config["protocol"])
        self.timeout = tk.StringVar(value=str(config["timeout"]))
        self.key = tk.StringVar(value=os.environ.get("TEXTREFINE_API_KEY") or core.get_secret(config["base_url"]))
        self.remember_key = tk.BooleanVar(value=config.get("remember_key", True))
        self.history_enabled = tk.BooleanVar(value=config["history_enabled"])
        self.auto_paste = tk.BooleanVar(value=config["auto_paste"] and not core.is_wayland())
        self.session_keys[core.secret_identity(config["base_url"])] = self.key.get()
        self.key_endpoint = config["base_url"]
        self.provider = tk.StringVar(value=core.provider_name(config["base_url"]))
        self.model_hint = tk.StringVar(value="获取模型列表不会发送生成请求；也可直接填写模型 ID。")
        self.show_key = tk.BooleanVar(value=False)
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="平台").grid(row=0, column=0, sticky="w", pady=3)
        provider = ttk.Combobox(settings, textvariable=self.provider, values=list(core.PROVIDERS), state="readonly")
        provider.grid(row=0, column=1, sticky="w", padx=12)
        provider.bind("<<ComboboxSelected>>", self.apply_provider)
        for row, label, variable in ((1, "Base URL", self.base_url), (4, "超时（秒）", self.timeout)):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(settings, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=12)
        ttk.Label(settings, text="模型 ID").grid(row=2, column=0, sticky="w", pady=3)
        model_frame = ttk.Frame(settings)
        model_frame.grid(row=2, column=1, sticky="ew", padx=12)
        self.model_combo = ttk.Combobox(model_frame, textvariable=self.model)
        self.model_combo.pack(side="left", fill="x", expand=True)
        self.models_button = ttk.Button(model_frame, text="获取模型", command=self.fetch_models)
        self.models_button.pack(side="left", padx=(6, 0))
        self.model_combo.bind("<KeyRelease>", self.filter_models)
        ttk.Label(settings, text="API Key").grid(row=3, column=0, sticky="w", pady=3)
        key_frame = ttk.Frame(settings)
        key_frame.grid(row=3, column=1, sticky="ew", padx=12)
        self.key_entry = ttk.Entry(key_frame, textvariable=self.key, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(key_frame, text="显示", variable=self.show_key,
                        command=lambda: self.key_entry.configure(show="" if self.show_key.get() else "•")).pack(side="left", padx=6)
        ttk.Label(settings, text="协议").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Combobox(settings, textvariable=self.protocol, values=("chat", "responses"), state="readonly").grid(row=5, column=1, sticky="w", padx=12)
        ttk.Checkbutton(settings, text="记住 API Key（系统密钥环，重启后自动读取）", variable=self.remember_key).grid(row=6, column=1, sticky="w", pady=3)
        ttk.Checkbutton(settings, text="保存最近 50 条原文与结果（本地）", variable=self.history_enabled).grid(row=7, column=1, sticky="w", pady=3)
        paste_check = ttk.Checkbutton(settings, text="接受时自动粘贴回源窗口（仅 X11）", variable=self.auto_paste)
        paste_check.grid(row=8, column=1, sticky="w", pady=3)
        if core.is_wayland():
            paste_check.state(["disabled"])
        buttons = ttk.Frame(settings)
        buttons.grid(row=9, column=1, sticky="w", padx=8, pady=8)
        ttk.Button(buttons, text="保存设置", command=self.save).pack(side="left", padx=4)
        ttk.Button(buttons, text="删除已保存的 Key", command=self.delete_key).pack(side="left", padx=4)
        ttk.Label(settings, textvariable=self.model_hint, wraplength=740).grid(row=10, column=0, columnspan=2, sticky="w", pady=3)
        ttk.Label(settings, text="OpenRouter 使用完整模型 ID（例如 provider/model）。切换平台后请使用该平台的 Key。\nWayland：先 Ctrl+C，再用剪贴板快捷键；复制结果后回原窗口 Ctrl+V。", wraplength=740).grid(row=11, column=0, columnspan=2, sticky="w", pady=3)
        advanced = ttk.Frame(tabs, padding=16)
        tabs.add(advanced, text="更多设置")
        ttk.Checkbutton(advanced, text="流式显示生成结果", variable=self.stream_enabled).pack(anchor="w", pady=6)
        ttk.Checkbutton(advanced, text="关闭窗口时隐藏，保留后台运行", variable=self.hide_on_close).pack(anchor="w", pady=6)
        ttk.Label(advanced, text="隐藏后从应用菜单或原来的快捷键唤回；工作区“退出”会完全结束程序。\n取消将停止接收后续结果；远端服务器是否停止计费取决于服务商。\n超时可设为 0（不限）；Ollama 本地服务不需要 API Key。", wraplength=780).pack(anchor="w", pady=10)
        ttk.Button(advanced, text="保存设置", command=self.save).pack(anchor="w", pady=8)
        backup_buttons = ttk.Frame(advanced)
        backup_buttons.pack(anchor="w", pady=8)
        ttk.Button(backup_buttons, text="备份设置与提示词", command=self.backup_settings).pack(side="left", padx=4)
        ttk.Button(backup_buttons, text="恢复设置备份", command=self.restore_settings).pack(side="left", padx=4)
        ttk.Label(advanced, text="备份不包含 API Key。恢复后检查平台和模型，再继续使用。", wraplength=780).pack(anchor="w", pady=8)
        drafts = ttk.Frame(advanced)
        drafts.pack(anchor="w", pady=8)
        ttk.Button(drafts, text="保存工作区草稿", command=self.save_draft).pack(side="left", padx=4)
        ttk.Button(drafts, text="恢复草稿", command=self.restore_draft).pack(side="left", padx=4)
        ttk.Button(drafts, text="删除草稿", command=self.delete_draft).pack(side="left", padx=4)
        ttk.Label(advanced, text="草稿手动保存到本地，包含原文、结果和临时指令；不含 API Key。", wraplength=780).pack(anchor="w", pady=4)
        shortcuts = ttk.Frame(tabs, padding=16)
        tabs.add(shortcuts, text="快捷键")
        ttk.Label(shortcuts, text="全局快捷键", font="TkHeadingFont").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(shortcuts, text="点击输入框后直接按组合键；留空表示禁用。应用后立即生效。", wraplength=800).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        try:
            current_hotkeys = hotkeys.read_current(config.get("hotkeys", hotkeys.DEFAULTS))
        except ValueError:
            current_hotkeys = dict(config.get("hotkeys", hotkeys.DEFAULTS))
        self.hotkey_vars = {name: tk.StringVar(value=current_hotkeys.get(name, "")) for name in hotkeys.ACTIONS}
        self.active_hotkeys = dict(current_hotkeys)
        self.config["hotkeys"] = dict(current_hotkeys)
        for row, (name, (label, _, _)) in enumerate(hotkeys.ACTIONS.items(), 2):
            ttk.Label(shortcuts, text=label, wraplength=340).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(shortcuts, textvariable=self.hotkey_vars[name], width=18, state="readonly", takefocus=True)
            entry.grid(row=row, column=1, sticky="w", padx=10)
            entry.bind("<KeyPress>", lambda event, action=name: self.record_hotkey(event, action))
            ttk.Button(shortcuts, text="清除", width=5, command=lambda action=name: self.hotkey_vars[action].set("")).grid(row=row, column=2)
        shortcut_buttons = ttk.Frame(shortcuts)
        shortcut_buttons.grid(row=7, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Button(shortcut_buttons, text="应用快捷键", command=self.apply_hotkeys).pack(side="left", padx=(0, 8))
        ttk.Button(shortcut_buttons, text="恢复默认（再点应用）", command=self.reset_hotkeys).pack(side="left", padx=8)
        ttk.Label(shortcuts, text="窗口内：Ctrl+Enter 生成 · Ctrl+Shift+Enter 重新生成\nCtrl+Shift+C 复制结果 · Esc 取消", wraplength=800).grid(row=8, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Label(shortcuts, text="Wayland：先 Ctrl+C，再使用剪贴板快捷键。\n重新生成会再次请求模型；显示窗口不会发起请求。", wraplength=800).grid(row=9, column=0, columnspan=3, sticky="w", pady=5)
        self.update_hotkey_hint()
        self.prompt_model = tk.StringVar()
        self.preserve_language = tk.BooleanVar(value=True)
        options = ttk.Frame(prompts)
        options.pack(fill="x", side="bottom", pady=8)
        ttk.Label(options, text="专用模型 ID（留空使用全局模型）").pack(anchor="w")
        self.prompt_model_combo = ttk.Combobox(options, textvariable=self.prompt_model)
        self.prompt_model_combo.pack(fill="x", pady=4)
        ttk.Checkbutton(options, text="保持原文语言", variable=self.preserve_language).pack(anchor="w")
        self.prompt_list = tk.Listbox(prompts, exportselection=False, height=6)
        self.prompt_list.pack(fill="x")
        self.prompt_list.bind("<<ListboxSelect>>", self.load_prompt)
        self.prompt_editor = tk.Text(prompts, wrap="word", height=10)
        self.prompt_editor.pack(fill="both", expand=True, pady=8)
        pbuttons = ttk.Frame(prompts)
        pbuttons.pack(fill="x")
        for label, callback in (("新建", self.new_prompt), ("保存修改", self.save_prompt),
                                ("设为默认", self.default_prompt), ("删除", self.delete_prompt)):
            ttk.Button(pbuttons, text=label, command=callback).pack(side="left", padx=4)
        self.history_list = tk.Listbox(history, exportselection=False, height=7)
        self.history_list.pack(fill="x")
        self.history_list.bind("<<ListboxSelect>>", self.show_history)
        self.history_view = tk.Text(history, wrap="word")
        self.history_view.pack(fill="both", expand=True, pady=8)
        hbuttons = ttk.Frame(history)
        hbuttons.pack(fill="x")
        ttk.Button(hbuttons, text="载入到工作区", command=self.reuse_history).pack(side="left", padx=4)
        ttk.Button(hbuttons, text="导出 CSV / JSON", command=self.export_history).pack(side="left", padx=4)
        ttk.Button(hbuttons, text="清空历史", command=self.clear_history).pack(side="left", padx=4)
        ttk.Label(root, textvariable=self.status, wraplength=970).pack(fill="x", padx=12, pady=(0, 10))
        self.refresh_prompts()
        self.refresh_history()
        root.bind("<Control-Return>", lambda e: self.shortcut(self.generate))
        root.bind("<Control-Shift-Return>", lambda e: self.shortcut(self.regenerate))
        root.bind("<Control-Shift-C>", lambda e: self.shortcut(self.copy_output))
        root.bind("<Escape>", lambda e: self.shortcut(self.cancel))
        self.update_counts()
        self.update_versions()
        root.bind("<Control-f>", lambda e: self.shortcut(self.focus_find))
        root.after(250, self.tick)
        root.protocol("WM_DELETE_WINDOW", self.on_window_close)
        root.after(100, self.poll)

    def record_hotkey(self, event, action):
        if event.keysym in ("Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Super_L", "Super_R", "Meta_L", "Meta_R"):
            return "break"
        if event.keysym == "BackSpace" and not (event.state & (4 | 8 | 64)):
            self.hotkey_vars[action].set("")
            return "break"
        if event.keysym == "Tab" and not (event.state & (4 | 8 | 64)):
            return None
        if event.keysym == "Escape" and not (event.state & (4 | 8 | 64)):
            self.root.focus_set()
            return "break"
        parts = [name for mask, name in ((4,"Ctrl"), (8,"Alt"), (1,"Shift"), (64,"Super")) if event.state & mask]
        try:
            binding = hotkeys.normalize("+".join(parts + [event.keysym]))
            self.hotkey_vars[action].set(binding)
            self.status.set("快捷键已录入；点击“应用快捷键”生效。")
        except ValueError as exc:
            self.status.set(str(exc))
        return "break"

    def reset_hotkeys(self):
        for name, value in hotkeys.DEFAULTS.items(): self.hotkey_vars[name].set(value)
        self.status.set("已填入默认组合，点击“应用快捷键”生效。")

    def apply_hotkeys(self):
        try:
            values = hotkeys.validate({name: variable.get() for name,variable in self.hotkey_vars.items()})
            previous = dict(self.active_hotkeys)
            hotkeys.apply(values)
            try:
                config = dict(self.config, hotkeys=values)
                core.save_config(config)
            except OSError:
                hotkeys.apply(previous)
                raise
            self.config = config
            self.active_hotkeys = values
            for name,value in values.items(): self.hotkey_vars[name].set(value)
            self.update_hotkey_hint()
            self.status.set("快捷键已保存并立即生效。其他应用的自定义快捷键已保留。")
        except (ValueError, OSError, core.AppError) as exc:
            messagebox.showerror("快捷键未应用", str(exc), parent=self.root)

    def update_hotkey_hint(self):
        items = [("选区", "capture"), ("选指令", "menu"), ("剪贴板", "clipboard"), ("显示", "show"), ("重试", "regenerate")]
        global_text = " / ".join(label + " " + self.active_hotkeys[name] for label,name in items if self.active_hotkeys.get(name))
        self.hotkey_hint.set("Ctrl+Enter 生成 · Ctrl+Shift+Enter 重新生成 · Ctrl+Shift+C 复制 · Esc 取消\n全局：" + (global_text or "未启用（可在快捷键页设置）"))

    @staticmethod
    def shortcut(callback):
        callback()
        return "break"

    def text_panel(self, pane, title, copy_output=False):
        frame = ttk.LabelFrame(pane, text=title, padding=6)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text = tk.Text(frame, wrap="word", undo=True, padx=10, pady=10,
                       font="TkTextFont", relief="flat", borderwidth=0)
        text_row = 0
        if copy_output:
            frame.rowconfigure(0, weight=0)
            frame.rowconfigure(1, weight=1)
            header = ttk.Frame(frame)
            header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
            self.copy_output_button = ttk.Button(header, text="复制全部", command=self.copy_output, state="disabled")
            self.copy_output_button.pack(side="right")
            self.copy_selection_button = ttk.Button(header, text="复制选中", command=self.copy_selection, state="disabled")
            self.copy_selection_button.pack(side="right", padx=4)
            text_row = 1
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=text_row, column=0, sticky="nsew")
        scroll.grid(row=text_row, column=1, sticky="ns")
        pane.add(frame, weight=1)
        return text

    def update_counts(self):
        original = self.input_text.get("1.0", "end-1c")
        result = self.output_text.get("1.0", "end-1c")
        self.counts.set(f"原文 {len(original):,} 字符　·　结果 {len(result):,} 字符")
        state = ["!disabled"] if result.strip() and self.result_complete and not self.busy else ["disabled"]
        self.accept_button.state(state)
        self.copy_output_button.state(state)
        self.copy_selection_button.state(state)

    def tick(self):
        self.update_counts()
        if self.busy:
            self.status.set(f"正在生成 · 已等待 {time.monotonic() - self.started_at:.0f} 秒 · 可取消等待")
        self.root.after(250, self.tick)

    def set_busy(self, busy):
        self.busy = busy
        self.run_button.state(["disabled"] if busy else ["!disabled"])
        self.cancel_button.state(["!disabled"] if busy else ["disabled"])
        self.regenerate_button.state(["!disabled"] if self.last_request and not busy else ["disabled"])
        if busy:
            self.started_at = time.monotonic()
            self.progress.start(12)
        else:
            self.progress.stop()
        self.update_counts()
        self.update_versions()

    def clear_result(self):
        if self.busy:
            self.cancel()
        self.output_text.delete("1.0", "end")
        self.result_complete = False
        self.reset_versions()
        self.highlight_diff()
        self.update_counts()

    def clear_work(self):
        if self.busy:
            self.cancel()
        self.input_text.delete("1.0", "end")
        self.output_text.delete("1.0", "end")
        self.last_request = None
        self.reset_versions()
        self.regenerate_button.state(["disabled"])
        self.target_window = ""
        self.update_counts()
        self.result_complete = False
        self.highlight_diff()
        self.input_text.focus_set()

    def export_result(self):
        if self.busy or not self.result_complete:
            return
        text = self.output_text.get("1.0", "end-1c")
        if not text.strip():
            self.status.set("还没有可导出的结果。")
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".txt",
                   initialfile="TextRefine-结果.txt", filetypes=[("文本文件", "*.txt"), ("所有文件", "*")])
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
                self.status.set("结果已导出。")
            except OSError as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.root)

    def apply_provider(self, event=None):
        preset = core.PROVIDERS[self.provider.get()]
        if not preset:
            return
        if core.provider_name(self.base_url.get()) == self.provider.get():
            return
        self.session_keys[core.secret_identity(self.key_endpoint)] = self.key.get()
        self.key_endpoint = preset["base_url"]
        self.base_url.set(preset["base_url"])
        self.protocol.set(preset["protocol"])
        self.model.set("")
        identity = core.secret_identity(self.key_endpoint)
        self.key.set(self.session_keys[identity] if identity in self.session_keys else core.get_secret(self.key_endpoint))
        self.model_request += 1
        self.models_button.state(["!disabled"])
        self.model_candidates = []
        self.model_combo.configure(values=[])
        self.model_hint.set("请填写该平台的 Key，再获取模型；OpenRouter 可直接获取公开模型列表。")

    def fetch_models(self):
        base, key = self.base_url.get().strip(), self.key.get()
        try:
            core.endpoint(base, "chat")
        except core.AppError as exc:
            self.model_hint.set(str(exc))
            return
        self.model_request += 1
        token = self.model_request
        self.models_button.state(["disabled"])
        self.model_hint.set("正在获取模型列表……")
        inbox = self.inbox
        def worker():
            try:
                models = core.list_models(base, key)
                inbox.put(("models", (token, base, models, "")))
            except Exception as exc:
                inbox.put(("models", (token, base, [], core.safe_message(exc, key))))
        threading.Thread(target=worker, daemon=True).start()

    def filter_models(self, event=None):
        query = self.model.get().lower()
        self.model_combo.configure(values=[m for m in self.model_candidates if query in m.lower()])

    def reuse_history(self):
        selected = self.history_list.curselection()
        if not selected or self.busy:
            return
        entry = self.history_entries[selected[0]]
        self.last_request = None
        self.reset_versions()
        self.regenerate_button.state(["disabled"])
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", entry.get("original", ""))
        self.output_text.delete("1.0", "end")
        self.output_text.insert("1.0", entry.get("result", ""))
        self.result_complete = True
        self.highlight_diff()
        if entry.get("prompt") in self.config["prompts"]:
            self.prompt_name.set(entry["prompt"])
        self.target_window = ""
        self.tabs.select(0)
        self.update_counts()
        self.status.set("已载入历史，可编辑、复制或重新生成。")

    def snapshot(self):
        config = dict(self.config)
        config.update(base_url=self.base_url.get().strip(), model=self.model.get().strip(), protocol=self.protocol.get(),
                      timeout=int(self.timeout.get()), history_enabled=self.history_enabled.get(), remember_key=self.remember_key.get(),
                      auto_paste=self.auto_paste.get(), stream_enabled=self.stream_enabled.get(),
                      diff_enabled=self.diff_enabled.get(), hide_on_close=self.hide_on_close.get())
        core.endpoint(config["base_url"], config["protocol"])
        if not config["model"] or not 0 <= config["timeout"] <= 600:
            raise core.AppError("模型不能为空；超时必须在 0 到 600 秒之间（0 表示不限）。")
        return config

    def save(self):
        try:
            config = self.snapshot()
            key = self.key.get().strip()
            if config["remember_key"] and key:
                try:
                    core.store_secret(config["base_url"], key)
                except core.AppError:
                    raise core.AppError("API Key 未能保存到系统密钥环。请解锁系统密钥环并重试；本次输入仍保留在窗口中。") from None
            core.save_config(config)
            self.config = config
            self.key_endpoint = config["base_url"]
            self.session_keys[core.secret_identity(self.key_endpoint)] = self.key.get()
            if config["remember_key"] and key:
                self.status.set("设置和 API Key 已保存到系统密钥环，重启后会自动读取。")
            elif config["remember_key"]:
                self.status.set("设置已保存。Key 为空；已有密钥环记录未删除，新输入的 Key 需再次保存。")
            else:
                self.status.set("设置已保存。本次填写的 Key 未写入密钥环；已有记录需通过“删除已保存的 Key”删除。")
        except (core.AppError, ValueError, OSError) as exc:
            messagebox.showerror("保存失败", core.safe_message(exc, self.key.get()), parent=self.root)

    def delete_key(self):
        try:
            core.delete_secret(self.base_url.get())
            self.key.set("")
            self.session_keys.pop(core.secret_identity(self.base_url.get()), None)
            self.status.set("此地址保存的密钥已删除。")
        except core.AppError as exc:
            messagebox.showerror("删除失败", str(exc))

    def read_clipboard(self):
        try:
            text = core.read_clipboard()
            self.input_text.delete("1.0", "end")
            self.input_text.insert("1.0", text)
            self.target_window = ""
            self.status.set("已读取复制过的文字；请检查原文后生成。")
        except core.AppError as exc:
            messagebox.showerror("剪贴板", str(exc))

    def command(self, command):
        self.root.deiconify()
        self.root.lift()
        self.tabs.select(0)
        if command["action"] == "show":
            return
        if command["action"] == "regenerate":
            self.regenerate()
            return
        if self.busy:
            self.status.set("已有请求正在处理；请等待或取消。")
            return
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", command.get("text", ""))
        window = str(command.get("window", ""))
        self.target_window = window if window.isdecimal() else ""
        if command["action"] == "capture":
            self.use_temp.set(False)
            self.prompt_name.set(self.config["default_prompt"])
            self.generate()
        else:
            self.status.set("已读取所选文字。选择提示词后点击“生成并预览”。")
            self.prompt_combo.focus_set()

    def generate(self):
        if self.busy:
            return
        text = self.input_text.get("1.0", "end-1c")
        name = self.prompt_name.get()
        try:
            config = self.snapshot()
            prompt = config["prompts"][name]
            option = config.get("prompt_options", {}).get(name, {})
            if self.use_temp.get():
                prompt = self.temp_prompt.get().strip()
                name = "临时指令"
                option = {}
                if not prompt:
                    raise core.AppError("请填写本次临时指令。")
            if option.get("model", "").strip():
                config["model"] = option["model"].strip()
            if option.get("preserve_language", False):
                prompt += "\n保持原文语言，不进行翻译。"
            key = self.key.get()
            if not key.strip() and not core.is_local_api(config["base_url"]):
                raise core.AppError("请先在 API 设置中填写 Key。")
            if not text.strip():
                raise core.AppError("请先选择、复制或输入文字。")
        except (core.AppError, ValueError, KeyError) as exc:
            if not self.key.get().strip() and not core.is_local_api(self.base_url.get()):
                self.tabs.select(1)
                self.key_entry.focus_set()
            messagebox.showerror("无法生成", str(exc), parent=self.root)
            return
        self.last_request = (copy.deepcopy(config), name, prompt, text, self.target_window)
        self.start_request(config, key, prompt, text, name)

    def regenerate(self):
        if self.busy:
            self.status.set("正在生成，请等待完成或先取消。")
            return
        if not self.last_request:
            self.status.set("还没有可重新生成的任务；请先按当前原文生成一次。")
            return
        config, name, prompt, text, window = self.last_request
        config = copy.deepcopy(config)
        if core.secret_identity(config["base_url"]) == core.secret_identity(self.base_url.get()):
            key = self.key.get()
        else:
            key = self.session_keys.get(core.secret_identity(config["base_url"]), "") or core.get_secret(config["base_url"])
        if not key.strip() and not core.is_local_api(config["base_url"]):
            messagebox.showerror("无法重新生成", "上次任务使用的平台没有可用 Key；请在 API 设置中切回该平台并填写 Key。", parent=self.root)
            return
        self.input_text.delete("1.0", "end")
        self.input_text.insert("1.0", text)
        self.target_window = window
        self.tabs.select(0)
        self.start_request(config, key, prompt, text, name)

    def start_request(self, config, key, prompt, text, name):
        self.save_version_edits()
        self.version_index = -1
        self.clear_search()
        self.generation += 1
        token = self.generation
        self.cancel_event = threading.Event()
        cancel_event = self.cancel_event
        self.result_complete = False
        self.stream_buffer = []
        self.set_busy(True)
        self.highlight_diff()
        self.output_text.delete("1.0", "end")
        self.status.set("正在生成……取消后将忽略结果，服务器端请求可能继续运行。")
        inbox = self.inbox
        transform = core.transform
        def worker():
            try:
                result = transform(config, key, prompt, text,
                    on_delta=lambda delta: inbox.put(("delta", (token, delta))), cancel_event=cancel_event)
                inbox.put(("result", (token, text, result, name, config)))
            except Exception as exc:
                inbox.put(("error", (token, core.safe_message(exc, key))))
        threading.Thread(target=worker, daemon=True).start()

    def cancel(self):
        self.cancel_event.set()
        self.generation += 1
        self.stream_buffer = []
        if not self.busy:
            return
        self.set_busy(False)
        self.status.set("已取消。当前预览可能不完整，请重新生成；服务器可能仍计费。")

    def poll(self):
        try:
            while True:
                kind, payload = self.inbox.get_nowait()
                if kind == "command":
                    self.command(payload)
                elif kind == "models":
                    token, base, models, error = payload
                    if token != self.model_request:
                        continue
                    self.models_button.state(["!disabled"])
                    if base != self.base_url.get().strip():
                        self.model_hint.set("地址已变更，请重新获取模型。")
                        continue
                    if error:
                        self.model_hint.set(error)
                    else:
                        self.prompt_model_combo.configure(values=models)
                        self.model_candidates = models
                        self.filter_models()
                        self.model_hint.set(f"已获取 {len(models)} 个模型。输入关键词可筛选下拉列表；选择后保存。")
                elif kind == "delta":
                    token, delta = payload
                    if token == self.generation and self.busy:
                        self.stream_buffer.append(delta)
                elif kind == "diff":
                    original, result, removed, added = payload
                    if (not self.busy and self.diff_enabled.get() and
                        original == self.input_text.get("1.0", "end-1c") and
                        result == self.output_text.get("1.0", "end-1c")):
                        for tag, widget, ranges in (("removed", self.input_text, removed), ("added", self.output_text, added)):
                            widget.tag_remove(tag, "1.0", "end")
                            for start, end in ranges:
                                widget.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
                elif kind == "result":
                    token, original, result, name, config = payload
                    if token != self.generation:
                        continue
                    self.stream_buffer = []
                    self.result_complete = True
                    self.set_busy(False)
                    self.last_original = original
                    self.output_text.delete("1.0", "end")
                    self.output_text.insert("1.0", result)
                    self.add_version(original, result, name, config)
                    self.highlight_diff()
                    self.status.set(f"生成完成 · {time.monotonic() - self.started_at:.1f} 秒。检查结果后复制或导出。")
                    self.update_counts()
                    if config["history_enabled"]:
                        try:
                            core.append_history({"time": datetime.now().isoformat(timespec="seconds"), "prompt": name,
                                                 "original": original, "result": result, "model": config["model"]})
                            self.refresh_history()
                        except OSError:
                            self.status.set("生成完成，但历史保存失败。")
                elif kind == "error":
                    token, error = payload
                    if token != self.generation:
                        continue
                    self.stream_buffer = []
                    self.set_busy(False)
                    self.status.set(error)
                    messagebox.showerror("生成失败", error)
        except queue.Empty:
            pass
        if self.stream_buffer and self.busy:
            self.output_text.insert("end", "".join(self.stream_buffer))
            self.stream_buffer = []
            self.output_text.see("end")
        self.root.after(100, self.poll)

    def focus_find(self):
        self.tabs.select(0)
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")

    def clear_search(self):
        self.output_text.tag_remove("search", "1.0", "end")
        self.search_cursor = "1.0"
        self.search_last_query = ""

    def find_next(self):
        query = self.find_query.get()
        if not query:
            self.clear_search()
            return
        if len(query) > 1000:
            self.status.set("查找文字最多 1000 字符。")
            return
        if getattr(self,"search_last_query","") != query:
            self.search_cursor = "1.0"
        self.output_text.tag_remove("search", "1.0", "end")
        cursor = getattr(self,"search_cursor","1.0")
        index = self.output_text.search(query,cursor,stopindex="end",nocase=True,exact=True)
        if not index: index = self.output_text.search(query,"1.0",stopindex="end",nocase=True,exact=True)
        self.search_last_query = query
        if not index:
            self.status.set("输出中未找到这段文字。")
            return
        count = tk.IntVar()
        self.output_text.search(query,index,stopindex="end",nocase=True,exact=True,count=count)
        end = self.output_text.index(f"{index}+{count.get()}c")
        self.output_text.tag_add("search",index,end)
        self.output_text.see(index)
        self.search_cursor = end
        self.status.set("已定位匹配项；再点下一个继续查找，末尾会回到开头。")

    def copy_selection(self):
        if self.busy or not self.result_complete: return
        try: text = self.output_text.get("sel.first","sel.last")
        except tk.TclError:
            self.status.set("请先在输出区选中要复制的文字。")
            return
        try:
            core.copy_clipboard(text)
            self.status.set(f"已复制选中文字（{len(text):,} 字符）。")
        except core.AppError as exc:
            messagebox.showerror("复制失败",str(exc),parent=self.root)

    def reset_versions(self):
        self.clear_search()
        self.versions = []
        self.version_index = -1
        self.update_versions()

    def update_versions(self):
        total = len(self.versions)
        self.version_label.set(f"版本 {self.version_index+1}/{total}" if self.version_index >= 0 else f"版本 —/{total}")
        self.previous_version_button.state(["!disabled"] if not self.busy and total and self.version_index != 0 else ["disabled"])
        self.next_version_button.state(["!disabled"] if not self.busy and 0 <= self.version_index < total-1 else ["disabled"])

    def save_version_edits(self):
        if not self.busy and self.result_complete and 0 <= self.version_index < len(self.versions):
            version = self.versions[self.version_index]
            version["result"] = self.output_text.get("1.0","end-1c")

    def add_version(self, original, result, name, config):
        request = copy.deepcopy(self.last_request)
        self.versions.append({"original":original,"result":result,"name":name,"model":config["model"],"request":request})
        while len(self.versions)>10 or (len(self.versions)>1 and sum(len(v["original"])+len(v["result"]) for v in self.versions)>2_000_000):
            self.versions.pop(0)
        self.version_index = len(self.versions)-1
        self.update_versions()

    def switch_version(self, direction):
        if self.busy or not self.versions: return
        index = len(self.versions)-1 if self.version_index < 0 else self.version_index+direction
        if not 0 <= index < len(self.versions): return
        self.save_version_edits()
        self.version_index = index
        version = self.versions[index]
        self.input_text.delete("1.0","end")
        self.input_text.insert("1.0",version["original"])
        self.output_text.delete("1.0","end")
        self.output_text.insert("1.0",version["result"])
        self.result_complete = True
        self.last_original = version["original"]
        self.last_request = copy.deepcopy(version["request"])
        self.target_window = ""
        if self.last_request:
            self.last_request = (*self.last_request[:4], "")
        self.regenerate_button.state(["!disabled"] if self.last_request else ["disabled"])
        self.clear_search()
        self.highlight_diff()
        self.update_counts()
        self.update_versions()
        self.status.set(f"已切换版本 {index+1}：{version['name']} · {version['model']}。")

    def save_draft(self):
        try:
            workspace.save_draft({"version":1,"original":self.input_text.get("1.0","end-1c"),
                "result":self.output_text.get("1.0","end-1c"),"complete":self.result_complete and not self.busy,
                "prompt":self.prompt_name.get(),"temporary_instruction":self.temp_prompt.get(),"use_temporary":self.use_temp.get()})
            self.status.set("工作区草稿已保存到本地，重启后可从更多设置恢复。")
        except (core.AppError,OSError) as exc:
            messagebox.showerror("草稿保存失败",str(exc),parent=self.root)

    def restore_draft(self):
        if self.busy:
            self.status.set("请先等待完成或取消，再恢复草稿。")
            return
        try:
            draft = workspace.load_draft()
            if (self.input_text.get("1.0","end-1c") or self.output_text.get("1.0","end-1c")) and not messagebox.askyesno("恢复草稿","恢复会替换当前工作区文字，是否继续？",parent=self.root): return
            self.clear_work()
            self.input_text.insert("1.0",draft["original"])
            self.output_text.insert("1.0",draft["result"])
            self.result_complete = draft["complete"]
            if draft["prompt"] in self.config["prompts"]: self.prompt_name.set(draft["prompt"])
            self.use_temp.set(draft["use_temporary"])
            self.temp_prompt.set(draft["temporary_instruction"])
            self.highlight_diff()
            self.update_counts()
            self.tabs.select(0)
            self.status.set("草稿已恢复。模型使用当前设置；可先生成，再重新生成。")
        except (core.AppError,OSError) as exc:
            messagebox.showerror("恢复草稿失败",str(exc),parent=self.root)

    def delete_draft(self):
        if not messagebox.askyesno("删除草稿","删除本地保存的工作区草稿？",parent=self.root): return
        try:
            (core.DATA_DIR/"draft.json").unlink(missing_ok=True)
            self.status.set("本地草稿已删除，当前工作区文字保留。")
        except OSError as exc: messagebox.showerror("删除失败",str(exc),parent=self.root)

    def copy_output(self):
        if self.busy or not self.result_complete:
            self.status.set("请等待生成完成后复制输出。")
            return
        text = self.output_text.get("1.0", "end-1c")
        if not text.strip():
            self.status.set("输出区为空。")
            return
        try:
            core.copy_clipboard(text)
            self.status.set(f"输出已复制（{len(text):,} 字符）；可按 Ctrl+V 粘贴。")
        except core.AppError as exc:
            messagebox.showerror("复制输出失败", str(exc), parent=self.root)

    def accept(self):
        if self.busy or not self.result_complete:
            return
        text = self.output_text.get("1.0", "end-1c")
        if not text.strip():
            return
        try:
            core.copy_clipboard(text)
            if self.auto_paste.get() and self.target_window:
                self.root.iconify()
                core.paste_window(self.target_window)
                self.status.set("已粘贴到原窗口。")
            else:
                self.status.set("结果已复制；回原窗口按 Ctrl+V。")
        except core.AppError as exc:
            messagebox.showerror("复制／替换", str(exc))

    def restore(self):
        if self.last_original:
            try:
                core.copy_clipboard(self.last_original)
                self.status.set("原文已恢复到剪贴板；回原窗口按 Ctrl+V。")
            except core.AppError as exc:
                messagebox.showerror("恢复原文", str(exc))

    def refresh_prompts(self):
        self.prompt_list.delete(0, "end")
        for name in self.config["prompts"]:
            self.prompt_list.insert("end", name)
        names = list(self.config["prompts"])
        self.prompt_combo.configure(values=names)
        if self.prompt_name.get() not in names:
            self.prompt_name.set(self.config["default_prompt"])
        self.prompt_list.selection_set(names.index(self.prompt_name.get()))
        self.load_prompt()

    def selected_prompt(self):
        selection = self.prompt_list.curselection()
        return self.prompt_list.get(selection[0]) if selection else ""

    def load_prompt(self, event=None):
        name = self.selected_prompt()
        self.prompt_editor.delete("1.0", "end")
        self.prompt_editor.insert("1.0", self.config["prompts"].get(name, ""))
        option = self.config.get("prompt_options", {}).get(name, {})
        self.prompt_model.set(option.get("model", ""))
        self.preserve_language.set(option.get("preserve_language", False))

    def new_prompt(self):
        name = simpledialog.askstring("新建提示词", "名称：")
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.config["prompts"]:
            messagebox.showerror("名称重复", "请选择不同的名称。")
            return
        self.config["prompts"][name] = "请在此填写处理输入文字的要求，只输出处理结果。"
        self.prompt_name.set(name)
        self.refresh_prompts()
        core.save_config(self.config)

    def save_prompt(self):
        name = self.selected_prompt()
        text = self.prompt_editor.get("1.0", "end-1c").strip()
        if name and text:
            self.config["prompts"][name] = text
            self.config.setdefault("prompt_options", {})[name] = {
                "model": self.prompt_model.get().strip(), "preserve_language": self.preserve_language.get()}
            core.save_config(self.config)
            self.status.set("提示词已保存。")

    def default_prompt(self):
        name = self.selected_prompt()
        if name:
            self.config["default_prompt"] = name
            self.prompt_name.set(name)
            core.save_config(self.config)
            self.status.set("默认提示词：" + name)

    def delete_prompt(self):
        name = self.selected_prompt()
        if not name or len(self.config["prompts"]) == 1:
            return
        if messagebox.askyesno("删除提示词", "删除“" + name + "”？"):
            del self.config["prompts"][name]
            self.config.get("prompt_options", {}).pop(name, None)
            if self.config["default_prompt"] == name:
                self.config["default_prompt"] = next(iter(self.config["prompts"]))
            self.refresh_prompts()
            core.save_config(self.config)

    def refresh_history(self):
        self.history_entries = core.load_history()
        self.history_list.delete(0, "end")
        for entry in self.history_entries:
            self.history_list.insert("end", entry.get("time", "") + " · " + entry.get("prompt", ""))

    def show_history(self, event=None):
        selection = self.history_list.curselection()
        if selection:
            entry = self.history_entries[selection[0]]
            self.history_view.delete("1.0", "end")
            self.history_view.insert("1.0", "原文：\n" + entry.get("original", "") + "\n\n结果：\n" + entry.get("result", ""))

    def clear_history(self):
        if messagebox.askyesno("清空历史", "删除本机保存的全部历史？"):
            core.atomic_json(core.DATA_DIR / "history.json", [])
            self.refresh_history()
            self.history_view.delete("1.0", "end")

    def schedule_diff(self):
        if self.diff_timer:
            self.root.after_cancel(self.diff_timer)
        self.diff_timer = self.root.after(400, self.highlight_diff)

    def highlight_diff(self):
        self.diff_timer = None
        for widget, tag in ((self.input_text, "removed"), (self.output_text, "added")):
            widget.tag_remove(tag, "1.0", "end")
        if self.busy or not self.diff_enabled.get() or not self.result_complete:
            return
        original = self.input_text.get("1.0", "end-1c")
        result = self.output_text.get("1.0", "end-1c")
        inbox = self.inbox
        def worker():
            removed, added = extras.diff_ranges(original, result)
            inbox.put(("diff", (original, result, removed, added)))
        threading.Thread(target=worker, daemon=True).start()

    def export_history(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile="TextRefine-历史.json",
                       filetypes=[("JSON", "*.json"), ("CSV", "*.csv")])
        if path:
            try:
                extras.export_history(path, self.history_entries)
                self.status.set(f"已导出 {len(self.history_entries)} 条历史记录。")
            except OSError as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.root)

    def backup_settings(self):
        try:
            config = self.snapshot()
            path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile="TextRefine-设置.json",
                              filetypes=[("JSON", "*.json")])
            if path:
                core.atomic_json(path, {k: config[k] for k in core.DEFAULTS})
                self.status.set("设置和提示词已备份，不含 API Key。")
        except (core.AppError, ValueError, OSError) as exc:
            messagebox.showerror("备份失败", str(exc), parent=self.root)

    def restore_settings(self):
        if self.busy:
            self.status.set("请先等待生成完成或取消，再恢复设置。")
            return
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            if Path(path).stat().st_size > 2_000_000:
                raise core.AppError("设置文件过大。")
            config = extras.validate_backup(json.loads(Path(path).read_text(encoding="utf-8")))
            if not messagebox.askyesno("恢复设置", "将替换当前设置与提示词，是否继续？", parent=self.root):
                return
            core.atomic_json(core.CONFIG_DIR / "before-restore.json", {k: self.config[k] for k in core.DEFAULTS})
            core.save_config(config)
            if self.base_url.get().strip().rstrip("/") != config["base_url"].rstrip("/"):
                identity = core.secret_identity(config["base_url"])
                self.key.set(self.session_keys[identity] if identity in self.session_keys else core.get_secret(config["base_url"]))
            self.config = config
            for variable, name in ((self.base_url,"base_url"), (self.model,"model"), (self.protocol,"protocol"),
                 (self.timeout,"timeout"), (self.history_enabled,"history_enabled"), (self.remember_key,"remember_key"), (self.auto_paste,"auto_paste"),
                 (self.stream_enabled,"stream_enabled"), (self.diff_enabled,"diff_enabled"), (self.hide_on_close,"hide_on_close")):
                variable.set(config[name])
            if core.is_wayland(): self.auto_paste.set(False)
            self.key_endpoint = config["base_url"]
            self.provider.set(core.provider_name(config["base_url"]))
            self.model_request += 1
            self.models_button.state(["!disabled"])
            self.model_candidates = []
            self.model_combo.configure(values=[])
            self.prompt_name.set(config["default_prompt"])
            self.refresh_prompts()
            self.highlight_diff()
            self.last_request = None
            self.regenerate_button.state(["disabled"])
            for name,variable in self.hotkey_vars.items(): variable.set(config["hotkeys"].get(name,""))
            self.status.set("备份已恢复。请检查平台、模型和 Key；快捷键需在快捷键页点击应用后生效。")
        except (core.AppError, ValueError, OSError) as exc:
            messagebox.showerror("恢复失败", str(exc), parent=self.root)

    def on_window_close(self):
        if self.hide_on_close.get():
            self.root.withdraw()
        else:
            self.close()

    def close(self):
        self.cancel_event.set()
        self.model_request += 1
        self.generation += 1
        if self.server:
            self.server.close()
        for callback in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", callback)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="TextRefine AI text utility")
    parser.add_argument("--show", action="store_true", help="显示窗口")
    parser.add_argument("--regenerate", action="store_true", help="重新生成本次会话的上一个任务")
    parser.add_argument("--capture", action="store_true", help="读取所选文字并运行默认提示词")
    parser.add_argument("--menu", action="store_true", help="读取所选文字，打开指令选择界面")
    parser.add_argument("--clipboard", action="store_true", help="读取复制过的文字并运行默认提示词")
    args = parser.parse_args()
    try:
        command = {"action": "regenerate" if args.regenerate else "show"}
        if args.capture or args.menu or args.clipboard:
            window = core.active_window()
            try:
                text = core.read_clipboard(primary=not args.clipboard)
            except core.AppError as exc:
                raise core.AppError(str(exc) + "\n若桌面不支持选区读取，请先 Ctrl+C，再用 --clipboard 快捷键。") from exc
            if not text.strip():
                raise core.AppError("没有读取到文字。请先选中文字，或 Ctrl+C 后使用 --clipboard。")
            if len(text) > 100_000:
                raise core.AppError("选中文字过长，请分段处理。")
            command = {"action": "menu" if args.menu else "capture", "text": text, "window": window}
        directory = runtime_directory()
        path = directory / "app.sock"
        if send_command(path, command):
            return
        import fcntl
        with open(directory / "instance.lock", "a", encoding="utf-8") as lock:
            os.chmod(directory / "instance.lock", 0o600)
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise core.AppError("程序正在启动，请稍后重试。") from None
            root = tk.Tk()
            inbox = queue.Queue()
            application = Application(root, core.load_config(), inbox)
            application.server = LocalServer(path, inbox)
            root.after(100, lambda: application.command(command))
            root.mainloop()
    except (core.AppError, OSError) as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("TextRefine", str(exc))
        root.destroy()


if __name__ == "__main__":
    main()
