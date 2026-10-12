"""Cross-process request budget for bounded acceptance; no prompts or secrets stored."""
import fcntl
import json
import os
from pathlib import Path
from core.config import get_settings
from core.errors import ConfigurationError


def reserve_request(payload):
    settings = get_settings()
    if settings.model_output_limit:
        payload['max_tokens'] = min(payload.get('max_tokens', settings.model_output_limit), settings.model_output_limit)
        if 'options' in payload:
            payload['options']['num_predict'] = settings.model_output_limit
    if not settings.model_request_limit:
        return
    if not settings.model_request_ledger:
        raise ConfigurationError('限额调用必须配置 MODEL_REQUEST_LEDGER')
    path=Path(settings.model_request_ledger)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as ledger:
        fcntl.flock(ledger,fcntl.LOCK_EX)
        ledger.seek(0)
        raw=ledger.read()
        state=json.loads(raw) if raw else {'requests':0}
        if state['requests'] >= settings.model_request_limit:
            raise ConfigurationError('模型验收请求额度已耗尽')
        state['requests'] += 1
        state['output_limit'] = settings.model_output_limit
        ledger.seek(0);ledger.truncate()
        json.dump(state,ledger)
        ledger.flush();os.fsync(ledger.fileno())


def record_usage(raw):
    """Best-effort observed usage; interrupted requests may lack terminal usage."""
    settings=get_settings()
    if not raw or not settings.model_request_limit or not settings.model_request_ledger:
        return
    with Path(settings.model_request_ledger).open('r+') as ledger:
        fcntl.flock(ledger,fcntl.LOCK_EX)
        state=json.load(ledger)
        usage=state.setdefault('observed_usage',{'responses':0,'prompt_tokens':0,'completion_tokens':0})
        usage['responses']+=1
        usage['prompt_tokens']+=int(raw.get('prompt_tokens') or 0)
        usage['completion_tokens']+=int(raw.get('completion_tokens') or 0)
        ledger.seek(0);ledger.truncate();json.dump(state,ledger);ledger.flush();os.fsync(ledger.fileno())
