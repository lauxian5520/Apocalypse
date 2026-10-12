"""Verify prepared corpus and tokenizer identities without any network access."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
from rl.model_storage import configure_hf_home
configure_hf_home()
from rl.env import template
from harness.corpus.store import CorpusStore


def fingerprint(model):
    template.configure(model)
    tokenizer = template.tokenizer()
    serialized = json.loads(tokenizer.backend_tokenizer.to_str())
    digest = hashlib.sha256(json.dumps(serialized, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'tokenizer_sha256': digest, 'template_sha256': template.template_sha256()}


def main():
    expected = json.loads((ROOT/'constraints/rl-inputs.json').read_text())
    CorpusStore.load(str(ROOT/'rl/data/corpus'), check_hash=True)
    for model, identity in expected.items():
        if fingerprint(model) != identity:
            raise SystemExit('tokenizer/template identity mismatch: '+model)
    print('PASS corpus checksum and both frozen tokenizer/template fingerprints (offline)')


if __name__ == '__main__':
    main()
