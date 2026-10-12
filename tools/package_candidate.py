"""Export the reviewed Git index as a candidate archive without creating a commit."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'artifacts/candidate')
    args = parser.parse_args()
    if subprocess.run(['git', 'diff', '--quiet'], cwd=ROOT).returncode:
        parser.error('Stage reviewed source changes before packaging; unstaged edits exist.')
    if git('ls-files', '--others', '--exclude-standard').strip():
        parser.error('Untracked source exists; review and stage or ignore it before packaging.')
    files = git('ls-files', '-z').decode().split('\0')
    for name in filter(None, files):
        path = Path(name)
        if path.name == '.env' or path.suffix in ('.pem', '.key'):
            parser.error('A configuration secret or certificate is tracked; refusing archive.')
    tree = git('write-tree').decode().strip()
    args.out.mkdir(parents=True, exist_ok=True)
    archive = (args.out/f'apocalypse-candidate-{tree[:12]}.tar.gz').resolve()
    git('archive', '--format=tar.gz', '-o', str(archive), tree)
    with archive.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    archive.with_name(archive.name+'.sha256').write_text(digest+'  '+archive.name+'\n')
    manifest = {'base_commit':git('rev-parse', 'HEAD').decode().strip(),
                'candidate_tree':tree, 'new_commit_created':False,
                'archive':archive.name, 'sha256':digest}
    (args.out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
