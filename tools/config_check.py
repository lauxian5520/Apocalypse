"""Production configuration and finite request-budget contract checks."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from core.config import Settings
from core.errors import ConfigurationError
from core.model_requests import reserve_request
from harness.llm import pricing
from harness.llm.base import LLMUsage


def main():
    base={'_env_file':None,'app_env':'production','jwt_secret':'a'*40,'cookie_secure':True}
    with patch.dict(os.environ,{},clear=True):
        settings=Settings(**base)
        assert settings.registration_enabled is False
        for override in ({'jwt_secret':'change-me'},{'cookie_secure':False},{'harness_shell_enabled':True},{'harness_require_admin':False},{'dev_reload':True}):
            try:Settings(**{**base,**override})
            except ValueError:pass
            else:raise AssertionError(override)
    with tempfile.TemporaryDirectory() as tmp:
        settings=Settings(_env_file=None,app_env='test',model_request_limit=2,model_output_limit=1024,model_request_ledger=str(Path(tmp)/'ledger.json'))
        with patch('core.model_requests.get_settings',return_value=settings):
            for _ in range(2):
                payload={'max_tokens':8192};reserve_request(payload);assert payload['max_tokens']==1024
            try:reserve_request({})
            except ConfigurationError:pass
            else:raise AssertionError('budget exceeded')
        assert json.loads(Path(settings.model_request_ledger).read_text())['requests']==2
        price_file=Path(tmp)/'pricing.json'
        settings.pricing_file=str(price_file)
        with patch('harness.llm.pricing.get_settings',return_value=settings):
            price_file.write_text(json.dumps({'models':{'known':{'cache_hit':0,'input':1,'output':2}}}))
            pricing._table.cache_clear()
            assert pricing.estimate_cost(LLMUsage(model='unknown')) is None
            for invalid in ('NaN','Infinity','-1','true'):
                price_file.write_text('{"models":{"bad":{"cache_hit":0,"input":'+invalid+',"output":2}}}')
                pricing._table.cache_clear()
                try:pricing._table()
                except ValueError:pass
                else:raise AssertionError('invalid price accepted: '+invalid)
            pricing._table.cache_clear()
    print('PASS production guards, closed registration, persistent request budget and output cap')


if __name__=='__main__':main()
