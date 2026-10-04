"""Offline provider-boundary probes; no API credential or paid call is used."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from trader.brain.llm import BrainLLM


def brain(tmp_path,monkeypatch,budget=600,client=None):
    monkeypatch.setattr('trader.brain.llm.Env.deepseek_key',lambda:'offline')
    b=BrainLLM({'brain':{'model_fast':'f','model_deep':'d','max_tokens_per_call':4000,
        'daily_token_budget':1000,'purpose_budgets':{'research':budget}}})
    b._usage_path=tmp_path/'usage.json'
    b._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=client)))
    return b


def response(used):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='ok',tool_calls=[]),
        finish_reason='stop')],usage=SimpleNamespace(total_tokens=used))


def invoke(b,tools=False):
    return (b.chat_tools([{'role':'user','content':'p'}],[],purpose='research') if tools
            else b.chat('p',purpose='research'))


@pytest.mark.parametrize('tools',[False,True])
def test_one_remaining_token_never_sends_4000(tmp_path,monkeypatch,tools):
    sent=[]
    b=brain(tmp_path,monkeypatch,client=lambda **kw:sent.append(kw) or response(20))
    b._spend(599,'research')
    invoke(b,tools)
    assert not sent or sent[0]['max_tokens']<=1
    assert not sent # The known input itself cannot fit in the remaining token.
    assert b._tokens_today()==599


@pytest.mark.parametrize('tools',[False,True])
def test_limited_admission_and_truthful_under_or_over_usage(tmp_path,monkeypatch,tools):
    sent=[];actual=[20,700]
    b=brain(tmp_path,monkeypatch,client=lambda **kw:sent.append(kw) or response(actual.pop(0)))
    assert invoke(b,tools) is not None
    assert 0<sent[0]['max_tokens']<600
    assert b._tokens_today()==20 and b.budget_left('research')==580
    assert invoke(b,tools) is not None
    assert sent[1]['max_tokens']<580
    assert b._tokens_today()==720 and b.budget_left('research')==0
    assert invoke(b,tools) is None and len(sent)==2


def test_concurrent_instances_reserve_before_send(tmp_path,monkeypatch):
    entered,release=Event(),Event();sent=[]
    def provider(**kw):
        sent.append(kw);entered.set();assert release.wait(5);return response(20)
    first=brain(tmp_path,monkeypatch,client=provider)
    second=brain(tmp_path,monkeypatch,client=provider)
    with ThreadPoolExecutor(2) as pool:
        future=pool.submit(invoke,first)
        assert entered.wait(5)
        try:
            assert invoke(second,True) is None
            assert len(sent)==1
        finally:release.set()
        assert future.result()=='ok'
    assert second._tokens_today()==20 and second.budget_left('research')==580


def test_unknown_provider_outcome_remains_reserved_on_reopen(tmp_path,monkeypatch):
    sent=[]
    def provider(**kw):
        sent.append(kw);raise TimeoutError('provider may have billed')
    b=brain(tmp_path,monkeypatch,client=provider)
    assert invoke(b) is None
    reopened=brain(tmp_path,monkeypatch,client=provider)
    assert invoke(reopened,True) is None
    assert len(sent)==1 and reopened.budget_left('research')==0
    assert reopened._tokens_today()==0 # Unknown usage is reserved, never invented as actual spend.


def test_invalid_ledger_cannot_reset_admission(tmp_path,monkeypatch):
    sent=[]
    b=brain(tmp_path,monkeypatch,client=lambda **kw:sent.append(kw) or response(20))
    b._usage_path.write_text('{broken')
    assert invoke(b) is None and not sent
    assert b._usage_path.read_text()=='{broken'


def test_missing_usage_is_not_settled_as_free_and_input_exhaustion_is_local(tmp_path,monkeypatch):
    sent=[]
    def provider(**kw):
        sent.append(kw)
        result=response(0);result.usage=None
        return result
    b=brain(tmp_path,monkeypatch,client=provider)
    assert b.chat('x'*1000,purpose='research') is None and not sent
    assert invoke(b)=='ok' and len(sent)==1
    assert b._tokens_today()==0 and b.budget_left('research')==0
    assert invoke(b,True) is None and len(sent)==1


def test_sdk_hidden_retries_are_disabled_before_provider_send(tmp_path,monkeypatch):
    import openai
    constructors=[];sent=[]
    def sdk(**kw):
        constructors.append(kw)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **request:sent.append(request) or response(20))))
    monkeypatch.setattr(openai,'OpenAI',sdk)
    b=brain(tmp_path,monkeypatch);b._client=None
    assert invoke(b)=='ok'
    assert constructors[0]['max_retries']==0 and len(sent)==1
