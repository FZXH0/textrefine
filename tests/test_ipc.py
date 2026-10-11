import queue
import socket
import tempfile
from pathlib import Path
import unittest

from app import LocalServer, send_command


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets unavailable")
class IpcTests(unittest.TestCase):
    def test_selected_text_reaches_existing_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = queue.Queue()
            path = Path(directory) / "app.sock"
            server = LocalServer(path, inbox)
            try:
                command = {"action": "menu", "text": "优化这句话", "window": "123"}
                self.assertTrue(send_command(path, command))
                kind, received = inbox.get(timeout=3)
                self.assertEqual(kind, "command")
                self.assertEqual(received, command)
            finally:
                server.close()
            self.assertFalse(path.exists())

    def test_absent_instance_returns_false(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(send_command(Path(directory) / "absent.sock", {"action": "show"}))


if __name__ == "__main__":
    unittest.main()
