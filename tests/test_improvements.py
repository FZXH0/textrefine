import unittest,copy,io,json,queue,tempfile
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
import core,app
class Opener:
 def __init__(self,payload):self.payload=payload;self.request=None
 def open(self,request,timeout):self.request=request;return io.BytesIO(json.dumps(self.payload).encode())
class ModelTests(unittest.TestCase):
 def test_model_list_does_not_generate(self):
  o=Opener({'data':[{'id':'b'},{'id':'a'},{'id':'b'},None]})
  self.assertEqual(core.list_models('https://example.com/v1','fake',o),['a','b'])
  self.assertEqual(o.request.get_method(),'GET');self.assertIsNone(o.request.data)
 def test_invalid_list(self):
  with self.assertRaises(core.AppError):core.list_models('https://example.com/v1','',Opener({'error':'no'}))
 def test_invalid_endpoint(self):
  with self.assertRaisesRegex(core.AppError,'后缀'):core.endpoint('https://example.com/v1/chat/completions','chat')
class UiTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patchers=[patch.object(core,'CONFIG_DIR',Path(self.tmp.name)/'config'),patch.object(core,'DATA_DIR',Path(self.tmp.name)/'data'),patch.object(core,'get_secret',return_value='')]
  for p in self.patchers:p.start()
  self.root=tk.Tk();self.root.withdraw();self.ui=app.Application(self.root,copy.deepcopy(core.DEFAULTS),queue.Queue())
 def tearDown(self):
  self.ui.close()
  for p in self.patchers:p.stop()
  self.tmp.cleanup()
 def test_switch_provider_isolates_keys(self):
  self.ui.key.set('deepseek-only');self.ui.provider.set('OpenRouter');self.ui.apply_provider();self.assertEqual(self.ui.key.get(),'')
  self.ui.provider.set('DeepSeek 官方');self.ui.apply_provider();self.assertEqual(self.ui.key.get(),'deepseek-only')
 def test_capture_default(self):
  self.ui.prompt_name.set('中文润色')
  with patch.object(self.ui,'generate') as g:
   self.ui.command({'action':'capture','text':'text'});self.assertEqual(self.ui.prompt_name.get(),self.ui.config['default_prompt']);g.assert_called_once()
 def test_busy_copy(self):
  self.ui.output_text.insert('1.0','old');self.ui.set_busy(True)
  with patch.object(core,'copy_clipboard') as c:self.ui.accept();c.assert_not_called()
 def test_stale_models(self):
  token=self.ui.model_request;self.ui.provider.set('OpenRouter');self.ui.apply_provider()
  self.ui.inbox.put(('models',(token,'https://api.deepseek.com/v1',['wrong'],'')));self.ui.poll();self.assertEqual(self.ui.model_candidates,[])
 def test_clear_cancels_queued_result(self):
  self.ui.set_busy(True);self.ui.inbox.put(('result',(self.ui.generation,'orig','late','中文润色',self.ui.config)));self.ui.clear_work();self.ui.poll();self.assertEqual(self.ui.output_text.get('1.0','end-1c'),'')
 def test_export(self):
  self.ui.output_text.insert('1.0','测试结果');self.ui.result_complete=True;p=Path(self.tmp.name)/'out.txt'
  with patch.object(app.filedialog,'asksaveasfilename',return_value=str(p)):self.ui.export_result()
  self.assertEqual(p.read_text(),'测试结果')
