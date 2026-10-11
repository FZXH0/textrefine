"""GNOME shortcut editing with conflict checks and rollback; no tray dependency."""
import ast
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA = 'org.gnome.settings-daemon.plugins.media-keys'
CUSTOM_SCHEMA = SCHEMA + '.custom-keybinding'
ACTIONS = {
    'capture': ('处理所选文字（默认提示词）', '--capture', 'Ctrl+Alt+E'),
    'menu': ('处理所选文字（选择提示词）', '--menu', 'Ctrl+Alt+Q'),
    'clipboard': ('处理剪贴板（默认提示词）', '--clipboard', 'Ctrl+Alt+Z'),
    'show': ('显示窗口', '--show', ''),
    'regenerate': ('重新生成上一次任务', '--regenerate', ''),
}
DEFAULTS = {name: info[2] for name, info in ACTIONS.items()}
PREFIX = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/textrefine-'
ALIASES = {'control':'Ctrl', 'ctrl':'Ctrl', 'primary':'Ctrl', 'alt':'Alt', 'shift':'Shift', 'super':'Super'}
KEYS = {k.lower():k for k in ('Space','Return','Escape','Tab','BackSpace','Delete','Home','End','Insert','PageUp','PageDown','Left','Right','Up','Down')}
KEYS.update({'enter':'Return','esc':'Escape','page_up':'PageUp','page_down':'PageDown','space':'Space'})


def normalize(value):
    if not isinstance(value, str):
        raise ValueError('快捷键必须是文字。')
    if not value.strip(): return ''
    parts = re.sub(r'<([^>]+)>', r'\1+', value.strip()).split('+')
    modifiers, key = set(), None
    for part in parts:
        name = part.strip().lower()
        if name in ALIASES:
            if key is not None: raise ValueError('请把 Ctrl / Alt / Shift / Super 放在按键前。')
            modifiers.add(ALIASES[name])
        elif key is None:
            if re.fullmatch(r'[a-z0-9]', name): key = name.upper()
            elif re.fullmatch(r'f(?:[1-9]|1[0-2])', name): key = name.upper()
            elif name in KEYS: key = KEYS[name]
            else: raise ValueError('不支持这个按键，请使用字母、数字、F1–F12 或常用导航键。')
        else: raise ValueError('每个快捷键只能有一个主按键。')
    if key is None or not modifiers.intersection({'Ctrl','Alt','Super'}):
        raise ValueError('全局快捷键至少需要 Ctrl、Alt 或 Super；留空可禁用。')
    return '+'.join([m for m in ('Ctrl','Alt','Shift','Super') if m in modifiers] + [key])


def accelerator(value):
    value = normalize(value)
    if not value: return ''
    parts = value.split('+')
    return ''.join('<' + ('Control' if p == 'Ctrl' else p) + '>' for p in parts[:-1]) + ('space' if parts[-1] == 'Space' else parts[-1])


def validate(values):
    if not isinstance(values, dict) or set(values) - set(ACTIONS):
        raise ValueError('快捷键设置格式不正确。')
    result = {name:normalize(values.get(name, DEFAULTS[name])) for name in ACTIONS}
    used = {}
    for name, binding in result.items():
        if binding and binding in used:
            raise ValueError(f'“{ACTIONS[name][0]}”与“{ACTIONS[used[binding]][0]}”的快捷键重复。')
        if binding: used[binding] = name
    return result


def call(*args):
    try:
        return subprocess.check_output(['gsettings', *args], text=True, stderr=subprocess.PIPE, timeout=5).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('无法读取或修改 GNOME 快捷键，请检查桌面设置服务。') from exc


def variant(raw):
    if raw.startswith('@as '): raw = raw[4:]
    return ast.literal_eval(raw)


def available():
    return bool(shutil.which('gsettings') and SCHEMA in call('list-schemas').splitlines())


def existing_paths(raw):
    value = variant(raw)
    if not isinstance(value,list) or any(not isinstance(p,str) for p in value):
        raise ValueError('系统自定义快捷键列表格式不正确。')
    return value


def paths():
    return existing_paths(call('get', SCHEMA, 'custom-keybindings'))


def read_current(fallback):
    result = validate(fallback)
    if not available(): return result
    current = paths()
    for name in ACTIONS:
        path = PREFIX + name + '/'
        if path in current:
            raw = variant(call('get',CUSTOM_SCHEMA+':'+path,'binding'))
            try: result[name] = normalize(raw)
            except ValueError: result[name] = raw
    return result


def conflicts(values, current_paths):
    requested = {binding:name for name,binding in values.items() if binding}
    owned = {PREFIX + name + '/' for name in ACTIONS}
    # Keep already registered combinations usable; only new/reassigned combinations
    # are rejected against system bindings (GNOME also lists VT switch F-keys).
    for name, binding in values.items():
        path = PREFIX + name + '/'
        if path in current_paths and binding:
            try: previous = normalize(variant(call('get', CUSTOM_SCHEMA+':'+path, 'binding')))
            except (ValueError, SyntaxError): continue
            if previous == binding: requested.pop(binding, None)
    entries = []
    for path in current_paths:
        if path in owned: continue
        schema = CUSTOM_SCHEMA + ':' + path
        entries.append((variant(call('get',schema,'name')), variant(call('get',schema,'binding'))))
    schemas = set(call('list-schemas').splitlines())
    for schema in (SCHEMA, 'org.gnome.desktop.wm.keybindings', 'org.gnome.shell.keybindings',
                   'org.gnome.mutter.keybindings', 'org.gnome.mutter.wayland.keybindings'):
        if schema not in schemas: continue
        for line in call('list-recursively',schema).splitlines():
            fields = line.split(None,2)
            if len(fields) != 3: continue
            try: bindings = variant(fields[2])
            except (ValueError,SyntaxError): continue
            if isinstance(bindings,list): entries.extend((fields[1],b) for b in bindings if isinstance(b,str))
    for label,binding in entries:
        try: binding = normalize(binding)
        except ValueError: continue
        if binding and binding in requested:
            raise ValueError(f'{binding} 已被“{label}”使用，请换一个组合。')


def apply(values, app_path=None):
    values = validate(values)
    if not available(): raise ValueError('此功能需要 GNOME 桌面；可在系统键盘设置中手动添加快捷键。')
    current = paths()
    conflicts(values,current)
    changes, new_paths = [], list(current)
    app_path = Path(app_path or Path(__file__).with_name('app.py')).resolve()
    launcher = app_path.with_name('shortcut_launcher.py')
    launch_path = launcher if launcher.is_file() else app_path
    for name,(label,option,_) in ACTIONS.items():
        path = PREFIX + name + '/'
        schema = CUSTOM_SCHEMA + ':' + path
        if path not in new_paths: new_paths.append(path)
        for key,value in (('name','TextRefine: '+label),('command',shlex.join([sys.executable,str(launch_path),option])),('binding',accelerator(values[name]))):
            changes.append((schema,key,repr(value),call('get',schema,key)))
    applied = []
    try:
        # Disable owned bindings before replacing them, allowing swaps without transient conflicts.
        for name in ACTIONS:
            schema = CUSTOM_SCHEMA+':'+PREFIX+name+'/'
            old = call('get',schema,'binding')
            call('set',schema,'binding',repr(''))
            applied.append((schema,'binding',old))
        for schema,key,new,old in changes:
            call('set',schema,key,new)
            applied.append((schema,key,old))
        call('set',SCHEMA,'custom-keybindings',repr(new_paths))
    except ValueError:
        for schema,key,old in reversed(applied):
            try: call('set',schema,key,old)
            except ValueError: pass
        try: call('set',SCHEMA,'custom-keybindings',repr(current))
        except ValueError: pass
        raise
    return values
