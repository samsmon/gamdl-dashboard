import asyncio

from app.bus import EventBus


async def test_publish_reaches_all_and_drops_when_full():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish({"type": "state"})
    assert await a.get() == {"type": "state"} and await b.get() == {"type": "state"}
    for _ in range(500):
        bus.publish({"type": "log"})  # must never raise or block
    assert b.qsize() == 100  # bounded: extra events are dropped
    bus.unsubscribe(a)
    before = a.qsize()
    bus.publish({"type": "state"})
    assert a.qsize() == before  # unsubscribed queues receive nothing
