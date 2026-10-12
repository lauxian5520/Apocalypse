"""Recompute historical comparisons and package existing adapters; never train."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from rl.eval.run_policy import summarize, file_hash
from rl.rollout.trajectory import Trajectory


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'artifacts/rl-delivery')
    parser.add_argument('--reports',type=Path,default=ROOT/'docs/experiments')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True);args.reports.mkdir(parents=True,exist_ok=True)
    data=ROOT/'rl/data'
    dev=data/'eval/hotpotqa-qwen3-8b-grpo-12steps'
    sft=data/'eval/hotpotqa-qwen3-8b-sft'
    test=data/'eval/hotpotqa-qwen3-8b-grpo-12steps-test'
    for split,baseline,candidates in [('dev',sft/'sft.jsonl',[('GRPO-75',dev/'grpo75.jsonl'),('GRPO-500',dev/'grpo500.jsonl')]),('test',test/'sft.jsonl',[('GRPO-500',test/'grpo500.jsonl')])]:
        cmd=[sys.executable,'-m','rl.eval.compare_grpo','--tasks',str(data/f'splits/{split}.jsonl'),'--split-name',split,'--baseline',str(baseline),'--out',str(args.reports/f'{split}-comparison.md')]
        for label,path in candidates:cmd+=['--candidate',f'{label}={path}']
        subprocess.run(cmd,cwd=ROOT,check=True)
    files=[]
    for directory in (data/'adapters/sft-qwen3',data/'adapters/grpo-qwen3-12steps/policy-step-000500'):
        if not (directory/'adapter_model.safetensors').is_file():raise SystemExit('missing adapter: '+str(directory))
        files.extend(p for p in directory.rglob('*') if p.is_file())
    for directory in (sft,dev,test):
        files.extend(p for p in directory.iterdir() if p.suffix=='.jsonl')
    files.extend(data/'metrics'/name for name in ['grpo-qwen3-12steps.jsonl','grpo-qwen3-12steps-status.json','grpo-qwen3-12steps.log'])
    files.extend(data/'sft'/name for name in ['qwen3-8b-sft-status.json','qwen3-8b-sft-train-active.log'])
    files.extend(data/'splits'/name for name in ['splits.json','train.jsonl','dev.jsonl','test.jsonl'])
    files.append(data/'corpus/manifest.json')
    files.extend((ROOT/'constraints').glob('*'))
    summaries={}
    for split,directory,names in [('dev',sft,['base','sft']),('dev',dev,['grpo75','grpo500']),('test',test,['sft','grpo500'])]:
        expected=sum(1 for _ in (data/f'splits/{split}.jsonl').open())
        for name in names:
            source=directory/f'{name}.jsonl'
            rows=[Trajectory.from_dict(json.loads(line)) for line in source.read_text().splitlines() if line.strip()]
            summaries[f'{split}/{name}']=summarize(rows,expected)
    revision_result=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True)
    revision=revision_result.stdout.strip() if revision_result.returncode==0 else None
    status_result=subprocess.run(['git','status','--porcelain'],cwd=ROOT,text=True,capture_output=True)
    inventory={
        'historical_training_revision':None,
        'historical_revision_note':'历史运行未绑定可核验提交；当前打包提交不是训练提交。',
        'packaging_base_revision':revision,
        'packaging_worktree_dirty':bool(status_result.stdout.strip()) if status_result.returncode==0 else None,
        'historical_generation':{'temperature':0,'top_p':0.95,'max_tokens':1024,'max_steps':12,'context_limit':16384,'provenance':'historical comparison reports; no original run receipts'},
        'historical_training_configuration':{
            'base_model':'Qwen/Qwen3-8B','sft_steps':710,'grpo_steps':500,
            'sft_tokenized_examples':1419,'training_split_tasks':1908,
            'hardware_from_logs':'NVIDIA GeForce RTX 4090; torch 2.13.0+cu130',
            'adapter_configuration':'adapter_config.json included for each adapter',
            'full_optimizer_and_launch_configuration':None,
            'limitation':'Original launch receipts unavailable; current defaults must not be treated as historical hyperparameters.'},
        'environment_stamps':{},'summaries':summaries,
        'files':{p.relative_to(ROOT).as_posix():{'bytes':p.stat().st_size,'sha256':file_hash(p)} for p in sorted(set(files))},
    }
    for p in files:
        if p.suffix=='.jsonl' and '/eval/' in str(p):
            stamps={json.dumps(json.loads(line)['stamp'],sort_keys=True) for line in p.read_text().splitlines() if line.strip()}
            inventory['environment_stamps'][p.relative_to(ROOT).as_posix()]=[json.loads(s) for s in sorted(stamps)]
    (args.reports/'manifest.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
    archive=args.out/'apocalypse-rl-existing-results.tar.gz'
    with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
        for p in sorted(set(files)):tar.add(p,arcname=p.relative_to(ROOT).as_posix(),recursive=False)
        for p in sorted(args.reports.glob('*')):
            if p.is_file():tar.add(p,arcname='reports/'+p.name,recursive=False)
    checksum=file_hash(archive)
    archive.with_suffix(archive.suffix+'.sha256').write_text(checksum+'  '+archive.name+'\n')
    print(json.dumps({'archive':str(archive),'sha256':checksum,'files':len(files)},ensure_ascii=False))


if __name__=='__main__':main()
