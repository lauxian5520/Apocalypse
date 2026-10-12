"""CPU checks for evaluation denominators and strict resume provenance."""
import json
import sys
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rl.eval.run_policy import summarize, check_resume


def main():
    rows=[SimpleNamespace(task_id='a',ok=True,reward={'correct':True,'format_valid':True}),
          SimpleNamespace(task_id='b',ok=False,reward={})]
    report=summarize(rows,3)
    assert report['pass@1']==.3333 and report['status']=='incomplete' and report['missing']==1
    assert report['valid_only']['pass@1']==1
    try: summarize(rows+[rows[0]],3)
    except ValueError: pass
    else: raise AssertionError('duplicate accepted')
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'eval.jsonl'
        receipt={'generation':{'temperature':0},'adapter':{'sha256':'original'}}
        check_resume(path,receipt,[])
        path.write_text('')
        check_resume(path,receipt,rows)
        for altered in ({**receipt,'generation':{'temperature':1}},{**receipt,'adapter':{'sha256':'new'}}):
            try: check_resume(path,altered,rows)
            except ValueError: pass
            else: raise AssertionError('changed run accepted')
        path.with_suffix('.run.json').unlink()
        try: check_resume(path,receipt,rows)
        except ValueError: pass
        else: raise AssertionError('legacy untracked run resumed')
        pid=Path(tmp)/'pid';pid.write_text('999999999')
        common=['--pid-file',str(pid),'--log',str(Path(tmp)/'missing.log'),'--status',str(Path(tmp)/'status.json'),'--total-steps','1']
        for module,extra in [
            ('rl.train.monitor_sft',['--adapter',str(Path(tmp)/'adapter'),'--checkpoints',str(Path(tmp)/'checkpoints')]),
            ('rl.train.monitor_grpo',['--metrics',str(Path(tmp)/'metrics.jsonl'),'--adapter-root',str(Path(tmp)/'adapter')]),
        ]:
            result=subprocess.run([sys.executable,'-m',module,*common,*extra],capture_output=True,text=True)
            assert result.returncode==1,(module,result.stderr)
            assert json.loads((Path(tmp)/'status.json').read_text())['state']=='failed'
        for module in ('rl.eval.run_policy','rl.eval.compare_grpo','rl.train.monitor_sft','rl.train.monitor_grpo'):
            assert subprocess.run([sys.executable,'-m',module,'--help'],capture_output=True).returncode==0
    print('PASS all-selected denominator, missing/error counting, duplicate and resume guards')


if __name__=='__main__': main()
