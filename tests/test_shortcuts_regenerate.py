import copy
import queue
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import app
import core
import hotkeys

class HotkeyTests(unittest.TestCase):
    def test_normalize_equivalence(self):
        self.assertEqual(hotkeys.normalize('<Primary><Alt>f8'), 'Ctrl+Alt+F8')
        self.assertEqual(hotkeys.accelerator('Alt+Ctrl+f8'), '<Control><Alt>F8')
        self.assertEqual(hotkeys.normalize(''), '')
    def test_invalid_and_duplicate(self):
        for binding in ('A', 'Shift+A', 'Ctrl', 'Ctrl+A+B', 'Ctrl+;', 'Ctrl+NoSuchKey'):
            with self.assertRaises(ValueError): hotkeys.normalize(binding)
        with self.assertRaisesRegex(ValueError,'重复'):
            hotkeys.validate(dict(hotkeys.DEFAULTS, show='Alt+Ctrl+E'))
    def test_other_shortcut_conflict_before_writes(self):
        calls=[]
        def call(*args):
            calls.append(args)
            if args[:2] == ('get',hotkeys.SCHEMA): return "['/other/']"
            if args[0]=='list-schemas': return hotkeys.SCHEMA
            if args[0]=='list-recursively': return ''
            if args[-1]=='binding': return "'<Control><Alt>e'"
            if args[-1]=='name': return "'Other app'"
            raise AssertionError(args)
        with patch.object(hotkeys,'available',return_value=True), patch.object(hotkeys,'call',side_effect=call):
            with self.assertRaisesRegex(ValueError,'Other app'): hotkeys.apply(hotkeys.DEFAULTS)
        self.assertFalse(any(c[0]=='set' for c in calls))
    def test_system_conflict(self):
        with patch.object(hotkeys,'call',side_effect=lambda *a: hotkeys.SCHEMA if a[0]=='list-schemas' else hotkeys.SCHEMA+" screenshot ['<Control><Alt>e']"):
            with self.assertRaisesRegex(ValueError,'screenshot'): hotkeys.conflicts(hotkeys.DEFAULTS,[])
    def test_existing_binding_can_be_retained(self):
        current = hotkeys.PREFIX+'capture/'
        def call(*args):
            if args[0]=='get': return "'<Control><Alt>F8'" if args[-1]=='binding' else "'owned'"
            if args[0]=='list-schemas': return hotkeys.SCHEMA
            return hotkeys.SCHEMA+" switch-to-session-8 ['<Control><Alt>F8']"
        values={name:'' for name in hotkeys.ACTIONS};values['capture']='Ctrl+Alt+F8'
        with patch.object(hotkeys,'call',side_effect=call):hotkeys.conflicts(values,[current])
    def fake_store(self, fail=False):
        state={(hotkeys.SCHEMA,'custom-keybindings'):"['/other/']"}
        for name in hotkeys.ACTIONS:
            schema=hotkeys.CUSTOM_SCHEMA+':'+hotkeys.PREFIX+name+'/'
            for key in ('name','command','binding'): state[(schema,key)]=repr('old-'+key)
        before=copy.deepcopy(state); failed=[False]
        def call(*args):
            action,schema,key,*value=args
            if action=='get':return state[(schema,key)]
            if fail and key=='command' and not failed[0]:
                failed[0]=True
                raise ValueError('fake failure')
            state[(schema,key)]=value[0]
            return ''
        return state,before,call
    def test_preserves_other_paths_and_disabled(self):
        state,_,call=self.fake_store()
        with patch.object(hotkeys,'available',return_value=True), patch.object(hotkeys,'conflicts'), patch.object(hotkeys,'call',side_effect=call):
            hotkeys.apply(hotkeys.DEFAULTS, '/tmp/app with space.py')
        self.assertIn('/other/',hotkeys.existing_paths(state[(hotkeys.SCHEMA,'custom-keybindings')]))
        schema=hotkeys.CUSTOM_SCHEMA+':'+hotkeys.PREFIX+'show/'
        self.assertEqual(hotkeys.variant(state[(schema,'binding')]),'')
        self.assertIn("'/tmp/app with space.py'",hotkeys.variant(state[(schema,'command')]))
    def test_failure_rolls_back(self):
        state,before,call=self.fake_store(fail=True)
        with patch.object(hotkeys,'available',return_value=True), patch.object(hotkeys,'conflicts'), patch.object(hotkeys,'call',side_effect=call):
            with self.assertRaises(ValueError):hotkeys.apply(hotkeys.DEFAULTS)
        self.assertEqual(state,before)

class RegenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patches=[patch.object(core,'CONFIG_DIR',Path(self.tmp.name)/'config'),patch.object(core,'DATA_DIR',Path(self.tmp.name)/'data'),patch.object(core,'get_secret',return_value=''),patch.object(hotkeys,'read_current',side_effect=lambda v:dict(v))]
        for p in self.patches:p.start()
        self.root=tk.Tk();self.root.withdraw();self.ui=app.Application(self.root,copy.deepcopy(core.DEFAULTS),queue.Queue());self.ui.key.set('fake-key')
    def tearDown(self):
        self.ui.close()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def wait(self):
        deadline=time.monotonic()+3
        while self.ui.busy and time.monotonic()<deadline:self.root.update();time.sleep(.01)
        self.assertFalse(self.ui.busy)
    def test_retry_uses_original_snapshot_and_current_key(self):
        self.ui.input_text.insert('1.0','原文');self.ui.use_temp.set(True);self.ui.temp_prompt.set('临时要求')
        with patch.object(core,'transform',return_value='结果') as transform:
            self.ui.generate();self.wait();first=transform.call_args.args
            self.ui.input_text.delete('1.0','end');self.ui.input_text.insert('1.0','新原文')
            self.ui.temp_prompt.set('新要求');self.ui.model.set('new-model');self.ui.key.set('new-key')
            self.ui.regenerate();self.wait();retry=transform.call_args.args
        self.assertEqual(retry[0],first[0]);self.assertEqual(retry[1],'new-key');self.assertEqual(retry[2:4],('临时要求','原文'))
        self.assertNotIn('fake-key',repr(self.ui.last_request))
        self.assertEqual(self.ui.input_text.get('1.0','end-1c'),'原文')
    def test_cancel_stale_result_then_retry(self):
        self.ui.input_text.insert('1.0','原文');gate=threading.Event()
        def old(*a,**kw):gate.wait(2);return '迟到结果'
        with patch.object(core,'transform',side_effect=old):
            self.ui.generate();self.ui.cancel()
        with patch.object(core,'transform',return_value='新结果'):
            self.ui.regenerate();self.wait();gate.set();time.sleep(.02);self.ui.poll()
        self.assertEqual(self.ui.output_text.get('1.0','end-1c'),'新结果')
    def test_retry_after_failure(self):
        self.ui.input_text.insert('1.0','原文')
        with patch.object(core,'transform',side_effect=core.AppError('failure')),patch.object(app.messagebox,'showerror'):
            self.ui.generate();self.wait()
        self.assertFalse(self.ui.regenerate_button.instate(['disabled']))
        with patch.object(core,'transform',return_value='重试结果'):
            self.ui.regenerate();self.wait()
        self.assertEqual(self.ui.output_text.get('1.0','end-1c'),'重试结果')
    def test_busy_and_clear_guards(self):
        with patch.object(self.ui,'start_request') as start:
            self.ui.regenerate();start.assert_not_called()
            self.ui.last_request=(copy.deepcopy(core.DEFAULTS),'name','prompt','text','');self.ui.set_busy(True)
            self.ui.regenerate();start.assert_not_called();self.ui.clear_work()
            self.ui.regenerate();start.assert_not_called()
    def test_capture_keys_prevents_generation_shortcut(self):
        event=SimpleNamespace(keysym='Return',state=5)
        self.assertEqual(self.ui.record_hotkey(event,'show'),'break')
        self.assertEqual(self.ui.hotkey_vars['show'].get(),'Ctrl+Shift+Return')
    def test_hotkey_apply_rollback_on_save_failure(self):
        old=dict(self.ui.active_hotkeys);self.ui.hotkey_vars['show'].set('Ctrl+Alt+G')
        with patch.object(hotkeys,'apply') as apply,patch.object(core,'save_config',side_effect=OSError('disk')),patch.object(app.messagebox,'showerror'):
            self.ui.apply_hotkeys()
        self.assertEqual(apply.call_count,2);self.assertEqual(apply.call_args.args[0],old)
        self.assertEqual(self.ui.active_hotkeys,old)
    def test_regenerate_ipc(self):
        with tempfile.TemporaryDirectory() as d:
            inbox=queue.Queue();path=Path(d)/'app.sock';server=app.LocalServer(path,inbox)
            try:
                self.assertTrue(app.send_command(path,{'action':'regenerate'}))
                self.assertEqual(inbox.get(timeout=2),('command',{'action':'regenerate'}))
            finally:server.close()
