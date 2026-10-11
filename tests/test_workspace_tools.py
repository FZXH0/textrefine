import copy,json,queue,subprocess,tempfile,time,tkinter as tk,unittest,os,sys
from pathlib import Path
from unittest.mock import patch
import app,core,workspace,hotkeys

class ClipboardTests(unittest.TestCase):
 def test_copy_does_not_capture_forked_server_pipes(self):
  with patch.object(core,'is_wayland',return_value=True),patch.object(core.shutil,'which',return_value='/usr/bin/wl-copy'),patch.object(core.subprocess,'run') as run:
   core.copy_clipboard('test')
  self.assertEqual(run.call_args.kwargs['stdout'],subprocess.DEVNULL)
  self.assertEqual(run.call_args.kwargs['stderr'],subprocess.DEVNULL)
  self.assertNotIn('capture_output',run.call_args.kwargs)
 def test_forking_helper_returns_without_waiting_for_server(self):
  with tempfile.TemporaryDirectory() as d:
   helper=Path(d)/'wl-copy';helper.write_text('#!/usr/bin/python3\nimport os,time,sys\nsys.stdin.read()\nif os.fork()==0:\n time.sleep(1.5)\n os._exit(0)\n');helper.chmod(0o700)
   with patch.dict(os.environ,{'PATH':d+':'+os.environ['PATH']}),patch.object(core,'is_wayland',return_value=True):
    start=time.monotonic();core.copy_clipboard('text');self.assertLess(time.monotonic()-start,1)
 def test_nonzero_failure(self):
  with patch.object(core.shutil,'which',return_value='tool'),patch.object(core.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['wl-copy'])):
   with self.assertRaises(core.AppError):core.copy_clipboard('text')

class DraftTests(unittest.TestCase):
 def test_allowlist_and_roundtrip(self):
  with tempfile.TemporaryDirectory() as d,patch.object(core,'DATA_DIR',Path(d)):
   workspace.save_draft({'version':1,'original':'原文','result':'结果','complete':True,'api_key':'secret'})
   self.assertEqual(workspace.load_draft()['result'],'结果')
   self.assertNotIn('secret',(Path(d)/'draft.json').read_text())
   self.assertEqual((Path(d)/'draft.json').stat().st_mode&0o777,0o600)
 def test_invalid_drafts(self):
  for v in ([],{}, {'version':1,'complete':'yes'}, {'version':1,'original':3}):
   with self.assertRaises(core.AppError):workspace.validate_draft(v)

class WorkspaceUiTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patches=[patch.object(core,'CONFIG_DIR',Path(self.tmp.name)/'config'),patch.object(core,'DATA_DIR',Path(self.tmp.name)/'data'),patch.object(core,'get_secret',return_value=''),patch.object(hotkeys,'read_current',side_effect=lambda v:dict(v))]
  for p in self.patches:p.start()
  self.root=tk.Tk();self.root.withdraw();self.ui=app.Application(self.root,copy.deepcopy(core.DEFAULTS),queue.Queue());self.ui.key.set('fake')
 def tearDown(self):
  self.ui.close()
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def wait(self):
  end=time.monotonic()+3
  while self.ui.busy and time.monotonic()<end:self.root.update();time.sleep(.01)
  self.assertFalse(self.ui.busy)
 def test_versions_preserve_edits_and_request(self):
  self.ui.input_text.insert('1.0','原文')
  with patch.object(core,'transform',side_effect=['第一版','第二版']):
   self.ui.generate();self.wait();self.ui.output_text.insert('end',' 已编辑')
   self.ui.regenerate();self.wait()
  self.assertEqual(len(self.ui.versions),2);self.ui.switch_version(-1)
  self.assertEqual(self.ui.output_text.get('1.0','end-1c'),'第一版 已编辑')
  self.ui.switch_version(1);self.assertEqual(self.ui.output_text.get('1.0','end-1c'),'第二版')
  self.assertEqual(self.ui.last_request[3],'原文')
  self.ui.clear_work();self.assertEqual(self.ui.versions,[])
 def test_failed_generation_can_return_to_old_version(self):
  self.ui.input_text.insert('1.0','原文')
  with patch.object(core,'transform',return_value='旧版'):self.ui.generate();self.wait()
  with patch.object(core,'transform',side_effect=core.AppError('fail')),patch.object(app.messagebox,'showerror'):self.ui.regenerate();self.wait()
  self.assertEqual(self.ui.version_index,-1);self.ui.switch_version(-1)
  self.assertEqual(self.ui.output_text.get('1.0','end-1c'),'旧版');self.assertTrue(self.ui.result_complete)
 def test_version_limit(self):
  for i in range(15):self.ui.add_version('orig',str(i),'prompt',core.DEFAULTS)
  self.assertEqual(len(self.ui.versions),10);self.assertEqual(self.ui.versions[0]['result'],'5')
 def test_search_unicode_wrap_and_selection(self):
  self.ui.output_text.insert('1.0','你好 WORLD\n你好 world');self.ui.result_complete=True
  self.ui.find_query.set('你好');self.ui.find_next();self.assertEqual(str(self.ui.output_text.tag_ranges('search')[0]),'1.0')
  self.ui.find_next();self.assertEqual(str(self.ui.output_text.tag_ranges('search')[0]),'2.0')
  self.ui.find_next();self.assertEqual(str(self.ui.output_text.tag_ranges('search')[0]),'1.0')
  self.ui.find_query.set('WORLD');self.ui.find_next();self.assertEqual(str(self.ui.output_text.tag_ranges('search')[0]),'1.3')
  self.ui.output_text.tag_add('sel','1.0','1.2')
  with patch.object(core,'copy_clipboard') as c:self.ui.copy_selection();c.assert_called_once_with('你好')
 def test_draft_restore_partial_guard(self):
  self.ui.input_text.insert('1.0','原文');self.ui.output_text.insert('1.0','部分');self.ui.use_temp.set(True);self.ui.temp_prompt.set('指令');self.ui.result_complete=False
  self.ui.save_draft();self.ui.clear_work();self.ui.restore_draft()
  self.assertEqual(self.ui.input_text.get('1.0','end-1c'),'原文');self.assertFalse(self.ui.result_complete)
  self.assertEqual(self.ui.temp_prompt.get(),'指令')
  with patch.object(core,'copy_clipboard') as c:self.ui.copy_output();c.assert_not_called()
 def test_restore_rejected_keeps_current(self):
  self.ui.input_text.insert('1.0','旧草稿');self.ui.save_draft();self.ui.input_text.insert('end','新内容')
  with patch.object(app.messagebox,'askyesno',return_value=False):self.ui.restore_draft()
  self.assertEqual(self.ui.input_text.get('1.0','end-1c'),'旧草稿新内容')
