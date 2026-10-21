import asyncio
import json
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest
from gateway.motion_director import claude_host as host


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'VERIFICATION_STATE', tmp_path / 'verification.json')
    monkeypatch.setattr(host, 'status_cache', None)
    monkeypatch.setattr(host, 'status_task', None)
    monkeypatch.setattr(host, 'rate_failures', 0)
    monkeypatch.setattr(host, 'status_lock', asyncio.Lock())


def test_polling_never_verifies_and_cancelled_caller_does_not_cancel_probe(monkeypatch):
    calls=[]
    async def scenario():
        entered, finish=asyncio.Event(), asyncio.Event()
        async def verify():
            calls.append(1);entered.set();await finish.wait()
            return {'available':True, 'reason':''}
        monkeypatch.setattr(host, '_subscription_status', verify)
        for _ in range(30):
            assert not (await host.subscription_status(check=False))['available']
        first=asyncio.create_task(host.subscription_status())
        await entered.wait()
        others=[asyncio.create_task(host.subscription_status()) for _ in range(30)]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        finish.set()
        assert all(r['available'] for r in await asyncio.gather(*others))
        assert (await host.subscription_status())['available']
    asyncio.run(scenario())
    assert len(calls)==1


def test_429_backoff_is_persistent_and_honors_retry_after(monkeypatch):
    calls=[]
    async def limited():
        calls.append(1)
        return {'available':False, 'state':'verification_rate_limited', 'reason':'HTTP 429; model usage unknown', '_retry_after':1200}
    monkeypatch.setattr(host, '_subscription_status', limited)
    async def scenario():
        result=await host.subscription_status()
        assert result['next_check_epoch']-host.time.time()>1198
        assert not result['available']
        host.status_cache=None  # host restart cannot evade cooldown
        for _ in range(20):assert (await host.subscription_status())['state']=='verification_rate_limited'
    asyncio.run(scenario())
    assert len(calls)==1


def test_retry_after_numeric_date_invalid_and_large():
    assert host.retry_delay('900', 1000)==900
    assert host.retry_delay(format_datetime(datetime.fromtimestamp(2200, timezone.utc)),1000)==1200
    assert host.retry_delay('garbage') is None
    assert host.retry_delay('inf') is None
    assert host.retry_delay('-4')==0


def test_expired_success_is_unavailable_to_passive_poll(monkeypatch):
    monkeypatch.setattr(host, 'status_cache', (0, {'available':True, 'next_check_epoch':0, 'reason':''}))
    result=asyncio.run(host.subscription_status(check=False))
    assert not result['available'] and result['state']=='verification_due'
