"""Local draft validation; only workspace text is persisted, never credentials."""
import core

TEXT_LIMIT = 2_000_000


def validate_draft(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise core.AppError('草稿格式不正确。')
    result = {'version':1}
    for key in ('original','result','prompt','temporary_instruction'):
        text = value.get(key,'')
        if not isinstance(text,str) or len(text)>TEXT_LIMIT:
            raise core.AppError('草稿文字无效或过长。')
        result[key]=text
    for key in ('complete','use_temporary'):
        if type(value.get(key,False)) is not bool: raise core.AppError('草稿状态不正确。')
        result[key]=value.get(key,False)
    return result


def save_draft(value):
    core.atomic_json(core.DATA_DIR/'draft.json',validate_draft(value))


def load_draft():
    import json
    path = core.DATA_DIR/'draft.json'
    if not path.exists(): raise core.AppError('还没有保存的草稿。')
    if path.stat().st_size > 16_000_000: raise core.AppError('草稿文件过大。')
    try: return validate_draft(json.loads(path.read_text(encoding='utf-8')))
    except (ValueError,UnicodeError): raise core.AppError('草稿文件无法读取。') from None
