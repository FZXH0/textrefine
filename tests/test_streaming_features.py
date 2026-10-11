import io,json,threading,unittest,tempfile,copy
from pathlib import Path
from unittest.mock import patch
import core,extras
import test_improvements

class StreamReply(io.BytesIO):
 headers={'Content-Type':'text/event-stream'}
class StreamOpener:
 def __init__(self,events):self.events=events;self.request=None
 def open(self,request,timeout):
  self.request=request
  return StreamReply(''.join('data: '+(json.dumps(e) if isinstance(e,dict) else e)+'\n\n' for e in self.events).encode())
def chunk(text='',finish=None):return {'choices':[{'index':0,'delta':{'content':text},'finish_reason':finish}]}
class StreamingTests(unittest.TestCase):
 def setUp(self):self.config=copy.deepcopy(core.DEFAULTS)
 def test_chat_delta_and_completion(self):
  o=StreamOpener([chunk('你'),chunk('好'),chunk(finish='stop'),'[DONE]']);parts=[]
  self.assertEqual(core.transform(self.config,'fake','润色','原文',o,on_delta=parts.append),'你好')
  self.assertEqual(parts,['你','好']);self.assertTrue(json.loads(o.request.data)['stream'])
 def test_disconnect_not_complete(self):
  with self.assertRaisesRegex(core.AppError,'未确认'):core.transform(self.config,'fake','润色','原文',StreamOpener([chunk('partial')]),on_delta=lambda x:None)
 def test_truncation_rejected(self):
  with self.assertRaisesRegex(core.AppError,'截断'):core.transform(self.config,'fake','润色','原文',StreamOpener([chunk('partial'),chunk(finish='length'),'[DONE]']),on_delta=lambda x:None)
 def test_cancel_after_delta(self):
  event=threading.Event()
  with self.assertRaisesRegex(core.AppError,'取消'):core.transform(self.config,'fake','润色','原文',StreamOpener([chunk('one'),chunk('two'),chunk(finish='stop'),'[DONE]']),on_delta=lambda x:event.set(),cancel_event=event)
 def test_responses_stream(self):
  self.config['protocol']='responses'
  o=StreamOpener([{'type':'response.output_text.delta','delta':'结果'}, {'type':'response.completed','response':{'status':'completed'}}])
  self.assertEqual(core.transform(self.config,'fake','润色','原文',o,on_delta=lambda x:None),'结果')
 def test_failed_responses(self):
  self.config['protocol']='responses'
  with self.assertRaises(core.AppError):core.transform(self.config,'fake','润色','原文',StreamOpener([{'type':'response.incomplete'}]),on_delta=lambda x:None)
 def test_local_requires_no_key(self):
  self.config['base_url']='http://localhost:11434/v1';self.config['timeout']=0
  o=StreamOpener([chunk('local'),chunk(finish='stop'),'[DONE]'])
  self.assertEqual(core.transform(self.config,'','润色','原文',o,on_delta=lambda x:None),'local')
  self.assertIsNone(o.request.get_header('Authorization'))
 def test_nonloopback_still_requires_key(self):
  self.config['base_url']='http://example.com:11434/v1'
  with self.assertRaises(core.AppError):core.transform(self.config,'','润色','原文',StreamOpener([]))
class ExportTests(unittest.TestCase):
 def test_csv_formula_escaped(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'h.csv';extras.export_history(p,[{'original':'=HYPERLINK("x")','result':'你好'}])
   self.assertIn("'=HYPERLINK",p.read_text(encoding='utf-8-sig'));self.assertEqual(p.stat().st_mode&0o777,0o600)
 def test_backup_strips_secret(self):
  v=copy.deepcopy(core.DEFAULTS);v['api_key']='sk-no-export';v['prompt_options']={'中文润色':{'model':'special','preserve_language':True,'secret':'bad'}}
  c=extras.validate_backup(v);self.assertNotIn('api_key',c);self.assertNotIn('secret',c['prompt_options']['中文润色'])
 def test_invalid_backup_fails(self):
  v=copy.deepcopy(core.DEFAULTS);v['prompt_options']={'中文润色':{'model':4}}
  with self.assertRaises(core.AppError):extras.validate_backup(v)
 def test_diff_ranges(self):
  removed,added=extras.diff_ranges('hello world','hello earth')
  self.assertEqual(removed,[(6,11)]);self.assertEqual(added,[(6,11)])
class NewUiTests(test_improvements.UiTests):
 def test_per_prompt_model(self):
  self.ui.key.set('fake');self.ui.input_text.insert('1.0','原文');self.ui.config['prompt_options']={'优化 AI 提示词':{'model':'special'}}
  with patch.object(core,'transform',return_value='结果') as t:
   self.ui.generate()
   import time
   deadline=time.monotonic()+2
   while self.ui.busy and time.monotonic()<deadline:self.root.update();time.sleep(.02)
   self.assertEqual(t.call_args.args[0]['model'],'special')
 def test_partial_cannot_be_accepted(self):
  self.ui.output_text.insert('1.0','partial');self.ui.result_complete=False
  with patch.object(core,'copy_clipboard') as t:self.ui.accept();t.assert_not_called()
 def test_temp_instruction_does_not_change_library(self):
  self.ui.use_temp.set(True);self.ui.temp_prompt.set('改成正式语气');self.ui.key.set('fake');self.ui.input_text.insert('1.0','原文')
  before=copy.deepcopy(self.ui.config['prompts'])
  with patch.object(core,'transform',return_value='结果') as t:
   self.ui.generate()
   import time
   deadline=time.monotonic()+2
   while self.ui.busy and time.monotonic()<deadline:self.root.update();time.sleep(.02)
   self.assertEqual(t.call_args.args[2],'改成正式语气');self.assertEqual(self.ui.config['prompts'],before)
