"""Live like counts: Redis pub/sub fanned out to Server-Sent Events.

Each API instance runs one Redis subscriber on the `post-events` channel and forwards
messages to the browsers connected to *it*. Publishing through Redis is what lets a
like handled by instance 1 reach a viewer connected to instance 2. With no Redis
configured (local dev, one instance) events are delivered in-process instead.
"""

import asyncio
import contextlib
import json
import logging

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

CHANNEL = "post-events"
QUEUE_SIZE = 100


class Subscription:
    def __init__(self, post_ids: set[int]):
        self.post_ids = post_ids
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_SIZE)


class Broker:
    def __init__(self) -> None:
        self.redis: aioredis.Redis | None = None
        self._subs: set[Subscription] = set()
        self._task: asyncio.Task | None = None

    async def start(self, redis: aioredis.Redis | None) -> None:
        self.redis = redis
        if redis is not None:
            self._task = asyncio.create_task(self._listen(), name="post-events-listener")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def subscribe(self, post_ids: set[int]) -> Subscription:
        sub = Subscription(post_ids)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    @property
    def connections(self) -> int:
        return len(self._subs)

    async def publish(self, event: dict) -> None:
        """Best effort: the database already has the change; viewers can catch up later."""
        if self.redis is None:
            self._deliver(event)
            return
        try:
            await self.redis.publish(CHANNEL, json.dumps(event))
        except Exception:
            log.warning("publish to %s failed", CHANNEL, exc_info=True)

    def _deliver(self, event: dict) -> None:
        for sub in list(self._subs):
            if event.get("post_id") in sub.post_ids:
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    # A stalled viewer: drop the update rather than block everyone. Counts
                    # are versioned, so the next update for that post corrects it.
                    pass

    async def _listen(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with self.redis.pubsub() as pubsub:
                    await pubsub.subscribe(CHANNEL)
                    backoff = 1.0
                    async for message in pubsub.listen():
                        if message.get("type") == "message":
                            self._deliver(json.loads(message["data"]))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("post-events subscriber dropped; retrying in %.0fs", backoff, exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)


broker = Broker()
