import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import core
from setup_hotkeys import existing_paths
from setup_desktop import quote_exec


class Reply(io.BytesIO):
    pass


class FakeOpener:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.request = payload, error, None

    def open(self, request, timeout):
        self.request = request
        if self.error:
            raise self.error
        return Reply(json.dumps(self.payload).encode())


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(core.DEFAULTS)

    def test_chat_request_and_result(self):
        opener = FakeOpener({"choices": [{"finish_reason": "stop", "message": {"content": "请撰写一封请假邮件。"}}]})
        result = core.transform(self.config, "fake-key", core.PROMPT_OPTIMIZE, "帮我写请假邮件", opener)
        self.assertEqual(result, "请撰写一封请假邮件。")
        self.assertEqual(opener.request.full_url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer fake-key")
        body = json.loads(opener.request.data)
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["messages"][1]["content"], "帮我写请假邮件")
        self.assertIn("不要完成", body["messages"][0]["content"])

    def test_responses_only_output_text(self):
        self.config["protocol"] = "responses"
        opener = FakeOpener({"status": "completed", "output": [
            {"type": "reasoning", "content": [{"type": "output_text", "text": "不应输出"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "最终文字"}]}]})
        result = core.transform(self.config, "fake-key", "润色", "原文", opener)
        self.assertEqual(result, "最终文字")
        self.assertTrue(opener.request.full_url.endswith("/responses"))
        body = json.loads(opener.request.data)
        self.assertFalse(body["store"])
        self.assertNotIn("messages", body)

    def test_quota_error_is_not_generic_rate_limit(self):
        payload = {"code": "Throttling.AllocationQuota", "message": "Your token-plan quota has been exhausted."}
        error = urllib.error.HTTPError("http://example/v1/responses", 429, "limit", {}, Reply(json.dumps(payload).encode()))
        with self.assertRaisesRegex(core.AppError, "套餐额度已耗尽"):
            core.transform(self.config, "fake-key", "润色", "原文", FakeOpener(error=error))

    def test_error_redacts_key(self):
        key = "sk-secret123"
        payload = {"error": {"message": "invalid key " + key}}
        self.assertNotIn(key, core.api_error(401, payload, key))

    def test_truncated_chat_is_not_accepted(self):
        opener = FakeOpener({"choices": [{"finish_reason": "length", "message": {"content": "不完整"}}]})
        with self.assertRaisesRegex(core.AppError, "截断"):
            core.transform(self.config, "fake-key", "润色", "原文", opener)

    def test_incomplete_responses_is_not_accepted(self):
        self.config["protocol"] = "responses"
        with self.assertRaisesRegex(core.AppError, "未完成"):
            core.transform(self.config, "fake-key", "润色", "原文", FakeOpener({"status": "incomplete"}))

    def test_empty_output_is_not_accepted(self):
        with self.assertRaises(core.AppError):
            core.transform(self.config, "fake-key", "润色", "原文", FakeOpener({"choices": [{"message": {"content": ""}}]}))

    def test_invalid_endpoint_and_empty_input_never_call_api(self):
        for url in ("file:///etc/passwd", "https://user:password@example.com/v1", "https://example.com/v1?x=1"):
            with self.assertRaises(core.AppError):
                core.endpoint(url, "chat")
        opener = FakeOpener({})
        with self.assertRaises(core.AppError):
            core.transform(self.config, "fake-key", "润色", " ", opener)
        self.assertIsNone(opener.request)

    def test_redirect_is_not_followed(self):
        self.assertIsNone(core.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.example"))


class PersistenceTests(unittest.TestCase):
    def test_config_does_not_save_secret_or_modify_defaults(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(core, "CONFIG_DIR", Path(directory)):
            config = core.load_config()
            config["api_key"] = "sk-private"
            config["prompts"]["新指令"] = "新的要求"
            core.save_config(config)
            raw = (Path(directory) / "config.json").read_text(encoding="utf-8")
            self.assertNotIn("sk-private", raw)
            self.assertIn("新指令", core.load_config()["prompts"])
            self.assertNotIn("新指令", core.DEFAULTS["prompts"])

    def test_history_limit_and_clear(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(core, "DATA_DIR", Path(directory)):
            for number in range(55):
                core.append_history({"original": str(number), "result": str(number)})
            self.assertEqual(len(core.load_history()), 50)
            self.assertEqual(core.load_history()[0]["original"], "5")
            core.atomic_json(Path(directory) / "history.json", [])
            self.assertEqual(core.load_history(), [])

    def test_malformed_config_reports_path(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(core, "CONFIG_DIR", Path(directory)):
            (Path(directory) / "config.json").write_text("not json")
            with self.assertRaisesRegex(core.AppError, "设置文件"):
                core.load_config()


class IntegrationHelpersTests(unittest.TestCase):
    def test_gnome_preserves_existing_paths(self):
        self.assertEqual(existing_paths("['/existing/']"), ["/existing/"])
        self.assertEqual(existing_paths("@as []"), [])
        with self.assertRaises(ValueError):
            existing_paths("{'bad': 1}")

    def test_desktop_percent_escaping(self):
        self.assertIn("%%", quote_exec("/home/test/100% folder/app.py"))
        self.assertTrue(quote_exec("/home/test/a b.py").startswith('"'))

    def test_wayland_does_not_attempt_keyboard_injection(self):
        with patch.object(core, "is_wayland", return_value=True), patch.object(core, "run_tool") as tool:
            with self.assertRaisesRegex(core.AppError, "X11"):
                core.paste_window("123")
            tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
