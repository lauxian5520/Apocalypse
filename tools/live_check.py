"""Opt-in real-provider acceptance. Persistent cap: <=10 attempts, <=1024 output tokens each."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import httpx
from check_support import ROOT, server, test_env, login
from agent_http_check import events
sys.path.insert(0,str(ROOT/'backend'))
from core.config import get_settings
from core.providers import provider_config, require_configured
from harness.llm.pricing import estimate_cost
from harness.llm.base import LLMUsage


def configuration_summary(settings, cfg):
    model=settings.harness_model or cfg['model']
    return {'provider':settings.ai_provider,'model':model,
            'endpoint_host':urlsplit(cfg['url']).hostname,
            'price_configured':estimate_cost(LLMUsage(model=model)) is not None,
            'usage_note':'Observed terminal usage only; interrupted responses may not report billable usage. Cost uses configured rates, not a provider invoice.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'artifacts/live')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    ledger=(args.out/'budget.json').resolve()
    if ledger.exists() and json.loads(ledger.read_text())['requests']:
        parser.exit(1,'已有真实调用记录；禁止自动重跑重置预算。请先审阅 results.json。\n')
    settings=get_settings();cfg=provider_config();require_configured(cfg)
    configuration=configuration_summary(settings,cfg)
    results=[];usage=[]
    def record(name,status,detail=''):
        results.append({'stage':name,'status':status,'detail':detail})
        (args.out/'results.json').write_text(json.dumps({**configuration,'budget':json.loads(ledger.read_text()) if ledger.exists() else {'requests':0},'results':results,'harness_usage':usage},ensure_ascii=False,indent=2)+'\n')
        print(name,status,detail,flush=True)
    with tempfile.TemporaryDirectory(prefix='apocalypse-live-') as tmp:
        env=test_env(tmp,AI_PROVIDER=settings.ai_provider,CUSTOM_API_URL=settings.custom_api_url,CUSTOM_API_KEY=settings.custom_api_key,CUSTOM_MODEL=settings.custom_model,
                     HARNESS_MODEL=settings.harness_model,HARNESS_MAX_TOKENS='1024',HARNESS_MAX_STEPS='3',HARNESS_SUBAGENT_ENABLED='false',HARNESS_AUTO_TITLE='false',HARNESS_WRITE_APPROVAL='true',
                     MODEL_REQUEST_LEDGER=str(ledger),MODEL_REQUEST_LIMIT='10',MODEL_OUTPUT_LIMIT='1024')
        with server(root=tmp,env=env) as (base,_,_), closing(login(base,'admin')) as client:
            client.timeout=httpx.Timeout(180)
            try:
                response=client.post('/api/ai/chat',json={'messages':[{'role':'user','content':'这是合成验收请求。请只回复：验收成功。'}]})
                frames=events(response)
                assert any(f.get('delta') for f in frames) and not any(f.get('error') for f in frames)
                record('plain_stream','passed')
            except Exception as exc:
                record('plain_stream','failed',type(exc).__name__)
                for name in ('read_tool','approved_write','interrupt_recovery'):record(name,'not_run','provider preflight failed')
                raise SystemExit(1)
            sid=client.post('/api/harness/sessions',json={'preset':'minimal','title':'synthetic acceptance'}).json()['id']
            path=f'/api/harness/sessions/{sid}'
            client.post(path+'/attachments',files={'file':('fixture.txt',b'synthetic acceptance fixture','text/plain')}).raise_for_status()
            def send(text):
                frames=events(client.post(path+'/messages',json={'text':text}))
                if any(f.get('error') or f.get('type')=='agent/error' for f in frames):raise RuntimeError('agent failed')
                return frames
            try:
                frames=send('请只调用一次 read 工具读取 fixture.txt，然后用一句话说明内容，不调用其他工具。')
                assert any(f.get('type')=='tool/result' and f['data'].get('name')=='read' and not f['data'].get('is_error') for f in frames)
                record('read_tool','passed')
            except Exception as exc:record('read_tool','failed',type(exc).__name__)
            try:
                frames=send('请只调用一次 write 工具，在工作区创建 result.txt，内容必须是 synthetic result。等待人工批准，完成后只回复完成。')
                approval=next(f for f in frames if f.get('type')=='tool/approval' and f['data'].get('name')=='write')
                frames=events(client.post(path+'/approvals/'+approval['data']['tool_call_id'],json={'approved':True}))
                assert any(f.get('type')=='tool/result' and not f['data'].get('is_error') for f in frames)
                file=client.get(path+'/files/result.txt');assert file.status_code==200 and file.text.strip()=='synthetic result'
                record('approved_write','passed')
            except Exception as exc:record('approved_write','failed',type(exc).__name__)
            try:
                started=threading.Event();done=[]
                def long_request():
                    try:
                        with client.stream('POST',path+'/messages',json={'text':'请连续输出从 1 到 500 的数字，用空格分隔，不调用工具。'}) as response:
                            for line in response.iter_lines():
                                if line.startswith('data: ') and 'assistant/chunk' in line:started.set()
                        done.append(True)
                    except Exception:done.append(False)
                worker=threading.Thread(target=long_request,daemon=True);worker.start()
                if not started.wait(90):raise RuntimeError('no interruptible stream')
                with closing(login(base,'admin')) as refreshed:
                    assert refreshed.get(path+'/events').status_code==200
                    refreshed.post(path+'/interrupt').raise_for_status()
                worker.join(timeout=60)
                assert not worker.is_alive()
                history=client.get(path+'/events').json()
                assert any(f['type']=='agent/interrupt' for f in history)
                frames=send('新的合成验收轮次。请只回复：恢复成功。不调用工具。')
                assert any(f.get('type')=='assistant/message' for f in frames)
                record('interrupt_recovery','passed')
            except Exception as exc:record('interrupt_recovery','failed',type(exc).__name__)
            detail=client.get(path).json()
            usage.append(detail.get('usage',{}))
            record('history_persistence','passed' if detail.get('id')==sid else 'failed')
    if any(r['status']!='passed' for r in results):raise SystemExit(1)


if __name__=='__main__':main()
