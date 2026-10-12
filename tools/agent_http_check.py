"""Agent HTTP/SSE regression against a fragmented, fault-injecting provider."""
from contextlib import closing
import json
from pathlib import Path
import tempfile
import threading
import time
from check_support import server, test_env, login
from mock_provider import mock_provider


def events(response):
    assert response.status_code==200,response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ') and line[6:]!='[DONE]']


def main():
    with tempfile.TemporaryDirectory() as tmp, mock_provider() as endpoint:
        env=test_env(tmp,CUSTOM_API_URL=endpoint,HARNESS_STREAM_TIMEOUT_SECONDS='1')
        with server(root=tmp,env=env) as (base,_,_), closing(login(base,'admin')) as client:
            sid=client.post('/api/harness/sessions',json={'preset':'minimal','title':'test'}).json()['id']
            path=f'/api/harness/sessions/{sid}'
            def send(text): return events(client.post(path+'/messages',json={'text':text}))
            first=send('mock-write')
            approval=next(e for e in first if e['type']=='tool/approval')
            call_id=approval['data']['tool_call_id']
            approved=events(client.post(path+'/approvals/'+call_id,json={'approved':True}))
            assert any(e['type']=='tool/result' for e in approved)
            read=send('mock-read')
            assert any(e['type']=='tool/result' and 'fixture content' in str(e) for e in read)
            denied=send('mock-write')
            call_id=next(e for e in denied if e['type']=='tool/approval')['data']['tool_call_id']
            refusal=events(client.post(path+'/approvals/'+call_id,json={'approved':False}))
            assert any('拒绝' in str(e) for e in refusal)
            assert any(e['type']=='agent/error' for e in send('mock-error'))
            assert any(e['type']=='agent/error' for e in send('mock-timeout'))
            assert any(e['type']=='turn/end' for e in send('recover'))
            print('PASS fragmented SSE/tool args, approval allow/deny, upstream error/timeout/recovery')
            result=[]
            def slow(): result.extend(send('mock-slow'))
            worker=threading.Thread(target=slow);worker.start()
            with closing(login(base,'admin')) as refreshed:
                for _ in range(50):
                    log=refreshed.get(path+'/events').json()
                    if any(e['type']=='assistant/chunk' and 'tick' in str(e) for e in log): break
                    time.sleep(.1)
                else: raise AssertionError('stream did not start')
                assert refreshed.get(path).json()['status']=='running'
                assert refreshed.delete(path).status_code==400
                assert refreshed.post(path+'/interrupt').status_code==200
            worker.join(timeout=15)
            assert not worker.is_alive()
            assert any(e['type']=='agent/interrupt' for e in result)
            assert any(e['type']=='turn/end' for e in send('after interrupt'))
            print('PASS refreshed client observes events, interrupts, and resumes new turn')


if __name__=='__main__': main()
