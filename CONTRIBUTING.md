# Contributing

欢迎提交 issue 和 pull request。请描述问题、Ubuntu 版本、桌面环境以及复现步骤。
不要上传 API Key、私人文本、配置、历史或草稿。日志请先检查是否包含敏感信息。

代码使用 Python 标准库，兼容 Python 3.10+。在本地运行：

```bash
python3 -m unittest discover -s tests -q
python3 ui_smoke.py
```

无桌面环境时使用 `xvfb-run -a` 运行上述命令。测试应使用 mock 或本地 HTTP 服务，避免调用收费模型或更改真实系统快捷键。
