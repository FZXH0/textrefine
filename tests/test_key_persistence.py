import copy
import queue
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import core
import hotkeys

class KeyPersistenceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patches=[patch.object(core,'CONFIG_DIR',Path(self.tmp.name)/'config'),patch.object(core,'DATA_DIR',Path(self.tmp.name)/'data'),patch.object(core,'get_secret',return_value=''),patch.object(hotkeys,'read_current',side_effect=lambda v:dict(v))]
  for p in self.patches:p.start()
  self.root=tk.Tk();self.root.withdraw();self.ui=app.Application(self.root,copy.deepcopy(core.DEFAULTS),queue.Queue())
 def tearDown(self):
  self.ui.close()
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def test_default_save_and_restart_load(self):
  self.assertTrue(self.ui.remember_key.get());self.ui.key.set('fake-persisted')
  with patch.object(core,'store_secret') as store:self.ui.save()
  store.assert_called_once_with(self.ui.base_url.get(),'fake-persisted')
  config=core.load_config();self.assertTrue(config['remember_key'])
  self.assertNotIn('fake-persisted',(core.CONFIG_DIR/'config.json').read_text())
  self.ui.close()
  self.root=tk.Tk();self.root.withdraw()
  with patch.object(core,'get_secret',return_value='fake-persisted'):
   self.ui=app.Application(self.root,config,queue.Queue())
  self.assertEqual(self.ui.key.get(),'fake-persisted')
 def test_opt_out_is_persisted(self):
  self.ui.key.set('session-only');self.ui.remember_key.set(False)
  with patch.object(core,'store_secret') as store:self.ui.save();store.assert_not_called()
  self.assertFalse(core.load_config()['remember_key'])
  self.assertNotIn('session-only',(core.CONFIG_DIR/'config.json').read_text())
 def test_store_failure_shows_error_and_does_not_report_success(self):
  self.ui.key.set('fake-key')
  with patch.object(core,'store_secret',side_effect=core.AppError('failed')),patch.object(app.messagebox,'showerror') as error,patch.object(core,'save_config') as save:
   self.ui.save();error.assert_called_once();save.assert_not_called()
  self.assertEqual(self.ui.key.get(),'fake-key')
  self.assertIn('未能保存',error.call_args.args[1])
 def test_provider_reads_persisted_key_and_preserves_choice(self):
  self.ui.remember_key.set(True);self.ui.provider.set('OpenRouter')
  with patch.object(core,'get_secret',return_value='router-key') as lookup:self.ui.apply_provider()
  lookup.assert_called_once_with('https://openrouter.ai/api/v1')
  self.assertEqual(self.ui.key.get(),'router-key');self.assertTrue(self.ui.remember_key.get())
 def test_blank_does_not_overwrite_saved_key(self):
  with patch.object(core,'store_secret') as store:self.ui.save();store.assert_not_called()
  self.assertIn('Key 为空',self.ui.status.get())
