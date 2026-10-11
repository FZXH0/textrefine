# TextRefine

**Ubuntu 桌面 AI 文本处理工具**：选中文字、选择指令、预览改写结果，再复制到需要的位置。

TextRefine 支持提示词优化、润色、翻译与精简，兼容 OpenAI 风格接口和本地 Ollama。界面使用中文，基于 Python 标准库和 Tk/ttk。本项目提供独立实现的 Linux 桌面文本处理工作流。

## 功能

- GNOME 全局快捷键：读取选区、选择提示词，或处理剪贴板。
- OpenRouter、DeepSeek、自定义 API 和 Ollama 预设；获取并筛选模型列表。
- Chat Completions 与 Responses 协议、流式输出、取消、超时设置。
- 原文与结果并排预览、改动高亮、可编辑输出、完整或选中内容复制、TXT 导出。
- 重新生成、最近 10 个会话内输出版本切换、输出文字查找。
- 自定义提示词、每条提示词专用模型、保持原文语言、临时指令。
- API Key 存入系统密钥环，重启后读取。
- 可选最近 50 条本地历史及 CSV/JSON 导出。
- 设置与提示词备份、手动保存和恢复工作区草稿。
- 可配置全局快捷键、隐藏窗口、单实例运行。

## 系统要求

主要目标环境为 Ubuntu 22.04、GNOME、Python 3.10+。支持 Wayland 和 X11，其他 Linux 桌面可手动配置快捷键。暂不提供 Windows/macOS 适配或托盘扩展。

Python 模块使用标准库，无需 `pip install`。桌面依赖：`python3-tk`、`wl-clipboard`、`xclip`、`xdotool`、`libsecret-tools`。

## 安装

下载仓库 ZIP 并解压，或使用 Git 克隆。把目录放在固定位置，在目录内运行：

```bash
bash install.sh
python3 app.py
```

安装脚本通过 `sudo apt-get` 安装系统依赖，然后创建应用菜单入口和 GNOME 快捷键。请使用普通桌面用户执行，避免以 root 运行程序。程序直接从源码目录启动；移动目录后需重建入口：

```bash
python3 setup_desktop.py
python3 setup_hotkeys.py
```

可选开机启动：`python3 setup_desktop.py --autostart`。关闭程序后从应用菜单或快捷键重新启动即可。

不使用安装脚本时，可先自行安装依赖，再运行 `python3 app.py`。

## 配置模型

在“API 设置”选择平台，填写 Base URL、模型 ID 和对应 API Key，然后点击“保存设置”。

| 平台 | Base URL | 协议 |
| --- | --- | --- |
| OpenRouter | `https://openrouter.ai/api/v1` | `chat` |
| DeepSeek | `https://api.deepseek.com/v1` | `chat` |
| Ollama | `http://localhost:11434/v1` | `chat` |
| 自定义 | 服务商提供的 Base URL | `chat` 或 `responses` |

不要在 Base URL 中加入 `/chat/completions`、`/responses` 或 `/models` 后缀。模型名称以对应服务商的准确 ID 为准。Ollama 需要另行安装、启动并准备模型；本机 loopback 地址允许不填 Key。

“记住 API Key”默认启用，保存到桌面 Secret Service（例如 GNOME Keyring），不会写入配置或设置备份。取消勾选只阻止写入当前 Key；删除已有记录请使用“删除已保存的 Key”。切换服务商时使用该平台对应的 Key。

也可通过 `TEXTREFINE_API_KEY` 环境变量提供 Key。请勿将密钥写入源码或提交到 Git。

## 快捷键与使用

全局默认快捷键：

| 快捷键 | 操作 |
| --- | --- |
| Ctrl+Alt+E | 读取选区，使用默认提示词生成 |
| Ctrl+Alt+Q | 读取选区，打开提示词选择界面 |
| Ctrl+Alt+Z | 读取剪贴板，使用默认提示词生成 |

在“快捷键”页点击输入框后按组合键，点击“应用快捷键”保存并生效。显示窗口和重新生成的全局快捷键默认留空，可自行启用。已有用户沿用其实际绑定。

窗口内快捷键：

| 快捷键 | 操作 |
| --- | --- |
| Ctrl+Enter | 使用当前输入与设置生成 |
| Ctrl+Shift+Enter | 沿用上次原文、指令与模型重新生成 |
| Ctrl+Shift+C | 复制全部输出 |
| Ctrl+F | 查找输出文字 |
| Esc | 取消当前生成 |

Wayland 推荐：**选择文字 → Ctrl+C → Ctrl+Alt+Z → 检查结果 → 复制 → 回原窗口 Ctrl+V**。选区读取依赖应用及桌面支持，若 Ctrl+Alt+E/Q 读不到文字，使用剪贴板流程。

X11 可选自动粘贴回源窗口。生成未完成时不能接受、复制或导出结果。重新生成会发起一次新的模型请求；取消不能保证远端服务器停止生成或计费。

## 版本、历史与草稿

- 输出版本只保留在本次会话，最多最近 10 个；大量文本时可能提前淘汰旧版。切换前保留手动编辑，清空或载入历史后重置版本。
- 历史默认关闭，开启后最多记录 50 条原文和结果，可以导出 CSV/JSON。
- “更多设置”中的草稿由用户手动保存；包含原文、结果、提示词名称、临时指令和完成状态。恢复使用当前模型配置，不恢复 Key 或会话版本。
- 设置备份包含平台地址、模型和提示词，不包含 Key；恢复后需手动应用备份中的快捷键。

## 隐私与限制

云端生成会把原文和指令发送给你配置的服务商，费用与数据政策由该服务商决定。Responses 发送 `store=false`，是否执行由服务商决定。

配置、历史和草稿使用 0600 权限；本地 IPC 目录 0700、socket 0600。快捷键诊断日志只记录动作和时间，不记录文本或 Key。API 客户端不跟随重定向，不自动重试收费请求。

配置、历史、草稿和 IPC 使用 `textrefine` 命名空间：

- 配置：`~/.config/textrefine/config.json`
- 历史：`~/.local/share/textrefine/history.json`
- 草稿：`~/.local/share/textrefine/draft.json`

遵循 `XDG_CONFIG_HOME` / `XDG_DATA_HOME`。单次输入最多 100,000 字符，API 返回最多 8 MB；极长文本可能跳过差异高亮。流式服务应返回标准 SSE，支持普通 JSON 回退。

## 故障排查

- 快捷键无反应：检查“快捷键”页及 GNOME 设置 → 键盘 → 自定义快捷键。重新应用绑定；必要时重启快捷键服务：`systemctl --user restart org.gnome.SettingsDaemon.MediaKeys.target`。
- Key 重启后为空：勾选“记住 API Key”并保存，确保系统密钥环已解锁，确认保存成功提示。
- 401：认证错误；402：余额不足；404：接口或模型不存在；429：频率或额度限制。
- 选区读取失败：先 Ctrl+C，使用 Ctrl+Alt+Z。
- 复制失败：确认 `wl-copy`（Wayland）或 `xclip`（X11）可用。

## 开发与测试

```bash
python3 -m unittest discover -s tests -q
python3 ui_smoke.py
```

无桌面环境时，用 `xvfb-run -a` 包装以上命令。GitHub Actions 在 Ubuntu 上运行测试。测试使用 mock 和本地 HTTP 服务，不需要真实 API Key 或收费模型。

主要文件：`app.py`（界面）、`core.py`（API/平台/持久化）、`extras.py`（流式/差异/导出）、`hotkeys.py`（快捷键）、`workspace.py`（草稿）。

欢迎贡献，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT](LICENSE)。
