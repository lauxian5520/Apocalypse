"""Run isolated delivery gates and preserve machine-readable results. No live API calls."""
import argparse
import ast
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def syntax():
    count=0
    for root in ('backend','rl','tools'):
        for path in (ROOT/root).rglob('*.py'):
            ast.parse(path.read_text(encoding='utf-8-sig'),filename=str(path));count+=1
    scripts=list((ROOT/'frontend').rglob('*.js'))
    for path in scripts:subprocess.run(['node','--check',str(path)],check=True)
    print(f'PASS syntax: {count} Python, {len(scripts)} JavaScript')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group',choices=('all','web','rl'),default='all')
    parser.add_argument('--out',type=Path,default=ROOT/'artifacts/checks')
    parser.add_argument('--syntax-only',action='store_true')
    args=parser.parse_args()
    if args.syntax_only:syntax();return
    args.out.mkdir(parents=True,exist_ok=True)
    commands={}
    if args.group in ('all','web'):
        commands.update({
            'syntax':[sys.executable,__file__,'--syntax-only'],
            'config':[sys.executable,'tools/config_check.py'],
            'feeds':[sys.executable,'tools/check_feeds.py','--offline'],
            'web':[sys.executable,'tools/web_check.py'],
            'memo-interactions':[sys.executable,'tools/memo_interactions_check.py'],
            'agent':[sys.executable,'tools/agent_http_check.py'],
            'backup':[sys.executable,'tools/backup_check.py'],
            'harness':[sys.executable,'tools/harness_check.py','--offline'],
            'browser':[sys.executable,'tools/browser_check.py'],
        })
    if args.group in ('all','rl'):
        commands.update({'rl-inputs':[sys.executable,'tools/rl_inputs_check.py'],
                         'rl-delivery':[sys.executable,'tools/rl_delivery_check.py'],
                         'rl':[sys.executable,'-m','rl.checks.rl_check','--offline']})
    results=[]
    env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONUNBUFFERED='1')
    for name,command in commands.items():
        start=time.monotonic()
        with (args.out/f'{name}.log').open('w') as log:
            try:
                completed=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
                passed=completed.returncode==0
                detail=f'exit {completed.returncode}'
            except subprocess.TimeoutExpired:
                passed=False;detail='timeout'
        if name=='rl' and passed:
            # Real provider rollout is the only permitted skip in the CPU gate.
            passed=bool(re.search(r'跳过\s+1(?:\s|$)', (args.out/f'{name}.log').read_text()))
            if not passed:detail='unexpected skipped RL stage'
        result={'stage':name,'status':'passed' if passed else 'failed','seconds':round(time.monotonic()-start,2),'detail':detail}
        results.append(result)
        print(json.dumps(result),flush=True)
        (args.out/'results.json').write_text(json.dumps({'group':args.group,'results':results},indent=2)+'\n')
    if any(row['status']!='passed' for row in results):raise SystemExit(1)


if __name__=='__main__':main()
