"""Deterministic OpenAI-compatible local server; never forwards a request."""
import contextlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextlib.contextmanager
def mock_provider():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            messages=body.get('messages',[])
            prompt=next((str(m.get('content','')) for m in reversed(messages) if m['role']=='user'),'')
            if 'mock-error' in prompt:
                self.send_response(503);self.end_headers();return
            if 'mock-timeout' in prompt:
                time.sleep(3)
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream' if body.get('stream') else 'application/json')
            self.end_headers()
            try:
                if not body.get('stream'):
                    self.wfile.write(json.dumps({'choices':[{'message':{'content':'mock complete'}}]}).encode());return
                def emit(delta,finish=None):
                    line='data: '+json.dumps({'choices':[{'delta':delta,'finish_reason':finish}]})+'\n\n'
                    raw=line.encode()
                    # Split frames across network writes as real providers do.
                    middle=len(raw)//2
                    self.wfile.write(raw[:middle]);self.wfile.flush()
                    self.wfile.write(raw[middle:]);self.wfile.flush()
                if 'mock-slow' in prompt:
                    for _ in range(100):
                        emit({'content':'tick '});time.sleep(.1)
                elif messages and messages[-1]['role']!='tool' and ('mock-write' in prompt or 'mock-read' in prompt):
                    write='mock-write' in prompt
                    args=json.dumps({'path':'fixture.txt','content':'fixture content'} if write else {'path':'fixture.txt'})
                    emit({'tool_calls':[{'index':0,'id':'call_'+str(len(messages)),'type':'function','function':{'name':'write' if write else 'read','arguments':args[:8]}}]})
                    emit({'tool_calls':[{'index':0,'function':{'arguments':args[8:]}}]},'tool_calls')
                else:
                    emit({'content':'mock '});emit({'content':'complete'},'stop')
                self.wfile.write(b'data: {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4}}\n\ndata: [DONE]\n\n')
                self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError): pass
    service=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    try: yield f'http://127.0.0.1:{service.server_port}/v1/chat/completions'
    finally: service.shutdown();service.server_close();thread.join()
