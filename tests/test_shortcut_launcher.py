import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import shortcut_launcher

class LauncherTests(unittest.TestCase):
 def test_action_only_log_and_forward(self):
  with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'XDG_RUNTIME_DIR':directory}),patch.object(sys,'argv',['launcher','--menu']),patch.object(os,'execv') as execute:
   shortcut_launcher.main()
   path=Path(directory)/('textrefine-'+str(os.getuid()))/'shortcut-events.log'
   self.assertEqual(path.stat().st_mode&0o777,0o600)
   self.assertTrue(path.read_text().endswith(' --menu\n'))
   self.assertEqual(execute.call_args.args[1][-1],'--menu')
   self.assertTrue(execute.call_args.args[1][-2].endswith('/app.py'))
 def test_invalid_action_rejected(self):
  with patch.object(sys,'argv',['launcher','--unknown']),patch.object(os,'execv') as execute:
   with self.assertRaises(SystemExit):shortcut_launcher.main()
   execute.assert_not_called()
 def test_log_failure_does_not_block_launch(self):
  with patch.object(sys,'argv',['launcher','--show']),patch.object(os,'open',side_effect=OSError('failed')),patch.object(os,'execv') as execute:
   shortcut_launcher.main();execute.assert_called_once()
