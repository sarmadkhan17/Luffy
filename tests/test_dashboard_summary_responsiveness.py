"""A slow legacy summary must not block the dashboard event loop."""
import asyncio
import threading

import httpx

from tests.test_attention_view import server


def test_navigation_responds_while_summary_waits(server, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked_snapshot():
        entered.set()
        assert release.wait(3), 'test did not release snapshot adapter'
        return {}

    monkeypatch.setattr(server, '_account_snapshot', blocked_snapshot)
    monkeypatch.setattr(server, '_position_marks', lambda _: {})
    monkeypatch.setattr(server, '_universe_prices', lambda: {})
    app = server.create_app({'attention': {'enabled': False}})

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://testserver',
            headers={'x-luffy-token': 'fixture-token'},
        ) as client:
            pending = asyncio.create_task(client.get('/api/summary'))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                response = await asyncio.wait_for(client.get('/'), timeout=.5)
                assert response.status_code == 200
                assert not pending.done()
            finally:
                release.set()
                result = await pending
            assert result.status_code == 200
            assert result.json()['open_positions'] == []

    asyncio.run(exercise())
