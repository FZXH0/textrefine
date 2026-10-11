"""Bounded streaming, local export and comparison helpers; no third-party dependencies."""
import csv
import difflib
import io
import json
import core


def read_stream(response, protocol, on_delta, cancel_event=None):
    parts, data, total = [], [], 0
    completed, finish = False, None
    def process(lines):
        nonlocal completed, finish
        if not lines:
            return
        raw = '\n'.join(lines)
        if raw == '[DONE]':
            if protocol == 'chat' and finish == 'stop':
                completed = True
            return
        try:
            item = json.loads(raw)
        except ValueError:
            raise core.AppError('流式响应包含无效 JSON。') from None
        if not isinstance(item, dict):
            raise core.AppError('流式响应格式不正确。')
        if 'error' in item:
            raise core.AppError(core.api_error(400, item, ''))
        delta = ''
        if protocol == 'responses':
            kind = item.get('type')
            if kind == 'response.output_text.delta':
                delta = item.get('delta', '')
            elif kind == 'response.completed':
                status = item.get('response', {}).get('status')
                if status != 'completed':
                    raise core.AppError('生成未完成，请重新生成。')
                completed = True
            elif kind in ('response.failed', 'response.incomplete', 'error'):
                raise core.AppError('生成失败或被截断，请重新生成。')
        else:
            for choice in item.get('choices', []):
                if choice.get('index', 0) != 0:
                    continue
                delta = choice.get('delta', {}).get('content') or ''
                reason = choice.get('finish_reason')
                if reason is not None:
                    finish = reason
                    if reason != 'stop':
                        raise core.AppError('生成被截断或停止，当前预览不完整。')
        if delta:
            if not isinstance(delta, str):
                raise core.AppError('模型返回的文字格式不正确。')
            parts.append(delta)
            on_delta(delta)
    while True:
        if cancel_event and cancel_event.is_set():
            raise core.AppError('已取消生成。')
        line = response.readline(1_000_001)
        if not line:
            process(data)
            break
        total += len(line)
        if total > 8_000_000 or len(line) > 1_000_000:
            raise core.AppError('API 返回过大，已停止处理。')
        try:
            line = line.decode('utf-8').rstrip('\r\n')
        except UnicodeDecodeError:
            raise core.AppError('流式响应编码无效。') from None
        if not line:
            process(data)
            data = []
            if completed:
                break
        elif line.startswith('data:'):
            data.append(line[5:].lstrip(' '))
    if cancel_event and cancel_event.is_set():
        raise core.AppError('已取消生成。')
    if not completed:
        raise core.AppError('连接已结束，但未确认生成完成；请重新生成。')
    result = ''.join(parts)
    if not result.strip():
        raise core.AppError('模型没有返回文字。')
    return result


def diff_ranges(original, result):
    # Word/whitespace tokens avoid quadratic character comparison on long papers.
    import re
    left = re.findall(r'\s+|\w+|[^\w\s]', original)
    right = re.findall(r'\s+|\w+|[^\w\s]', result)
    if len(left) + len(right) > 12000:
        left, right = original.splitlines(keepends=True), result.splitlines(keepends=True)
    if len(left) + len(right) > 12000:
        return [], []
    lp, rp = [0], [0]
    for t in left: lp.append(lp[-1] + len(t))
    for t in right: rp.append(rp[-1] + len(t))
    removed, added = [], []
    for op, a, b, c, d in difflib.SequenceMatcher(None, left, right, autojunk=True).get_opcodes():
        if op in ('delete', 'replace'): removed.append((lp[a], lp[b]))
        if op in ('insert', 'replace'): added.append((rp[c], rp[d]))
    return removed, added


def export_history(path, records):
    from pathlib import Path
    path = Path(path)
    if path.suffix.lower() == '.csv':
        out = io.StringIO(newline='')
        writer = csv.DictWriter(out, fieldnames=['time', 'prompt', 'model', 'original', 'result'], extrasaction='ignore')
        writer.writeheader()
        for record in records:
            # Neutralize spreadsheet formulas in externally supplied text.
            row = {k: str(record.get(k, '')) for k in writer.fieldnames}
            for k, value in row.items():
                if value.lstrip().startswith(('=', '+', '-', '@')): row[k] = "'" + value
            writer.writerow(row)
        path.write_text(out.getvalue(), encoding='utf-8-sig')
        path.chmod(0o600)
    else:
        core.atomic_json(path, records)


def validate_backup(value):
    if not isinstance(value, dict):
        raise core.AppError('设置备份必须是 JSON 对象。')
    result = {k: value[k] for k in core.DEFAULTS if k in value}
    if not isinstance(result.get('prompts'), dict) or not result['prompts']:
        raise core.AppError('备份中缺少提示词。')
    if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
           for k, v in result['prompts'].items()):
        raise core.AppError('备份中的提示词无效。')
    config = dict(core.DEFAULTS, **result)
    for k in ('base_url', 'model', 'protocol', 'default_prompt'):
        if not isinstance(config[k], str): raise core.AppError('备份设置类型不正确。')
    core.endpoint(config['base_url'], config['protocol'])
    if type(config['timeout']) is not int or not 0 <= config['timeout'] <= 600:
        raise core.AppError('超时设置不正确。')
    for k in ('stream_enabled', 'diff_enabled', 'hide_on_close', 'auto_paste', 'history_enabled', 'remember_key'):
        if type(config[k]) is not bool: raise core.AppError('开关设置不正确。')
    import hotkeys
    try: config['hotkeys'] = hotkeys.validate(config['hotkeys'])
    except ValueError as exc: raise core.AppError(str(exc)) from None
    options = config['prompt_options']
    if not isinstance(options, dict): raise core.AppError('提示词选项不正确。')
    cleaned = {}
    for name, option in options.items():
        if name not in config['prompts']: continue
        if not isinstance(option, dict): raise core.AppError('提示词选项不正确。')
        if not isinstance(option.get('model',''), str) or type(option.get('preserve_language',True)) is not bool:
            raise core.AppError('提示词模型或语言设置不正确。')
        cleaned[name] = {k: option[k] for k in ('model','preserve_language') if k in option}
    config['prompt_options'] = cleaned
    if config['default_prompt'] not in config['prompts']:
        config['default_prompt'] = next(iter(config['prompts']))
    return config
