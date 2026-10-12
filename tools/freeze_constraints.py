"""Capture installed dependency closure; validate installation separately."""
import argparse
import importlib.metadata as metadata
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('requirements',nargs='+',type=Path);p.add_argument('--out',required=True,type=Path);args=p.parse_args()
    queue=[]
    for path in args.requirements:
        for line in path.read_text().splitlines():
            line=line.split('#',1)[0].strip()
            if line:queue.append(Requirement(line))
    seen=set();versions={}
    while queue:
        req=queue.pop();key=(canonicalize_name(req.name),tuple(sorted(req.extras)))
        if key in seen:continue
        seen.add(key)
        dist=metadata.distribution(req.name)
        versions[key[0]]=dist.version
        for raw in dist.requires or []:
            dep=Requirement(raw)
            if dep.marker is None or any(dep.marker.evaluate({'extra':extra}) for extra in {'',*req.extras}):
                queue.append(dep)
    args.out.write_text('# Installed dependency closure, Python 3.12 Linux x86_64.\n'+'\n'.join(f'{name}=={version}' for name,version in sorted(versions.items()))+'\n')
    print(args.out,len(versions))


if __name__=='__main__':main()
