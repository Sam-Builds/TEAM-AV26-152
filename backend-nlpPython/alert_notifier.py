"""
Alert Notifier
--------------
Dispatches alerts to connected consumers via:
  1. SSE (Server-Sent Events) — real-time push to browser/dashboard clients
  2. Webhooks               — HTTP POST to registered external URLs

SSEManager  : manages per-subscriber asyncio queues
AlertNotifier: combines SSE + webhook delivery
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE manager
# ---------------------------------------------------------------------------

class SSEManager:
    """
    One asyncio.Queue per connected SSE subscriber.
    broadcast() pushes an alert dict to all queues.
    Slow / dead subscribers are evicted automatically.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._queues.append(q)
        logger.info("SSE subscriber connected. Total: %d", len(self._queues))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._queues.remove(q)
        except ValueError:
            pass
        logger.info("SSE subscriber disconnected. Total: %d", len(self._queues))

    async def broadcast(self, payload: dict) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@dataclass
class WebhookConfig:
    url: str
    secret: Optional[str] = None
    enabled: bool = True
    id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex[:8])


# ---------------------------------------------------------------------------
# Alert notifier
# ---------------------------------------------------------------------------

class AlertNotifier:
    """
    Fire-and-forget notification engine.

    Usage
    -----
    notifier = AlertNotifier()
    await notifier.start()
    await notifier.notify(alert_dict)   # call after every new alert
    await notifier.stop()
    """

    def __init__(self) -> None:
        self.sse = SSEManager()
        self._webhooks: list[WebhookConfig] = []
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)
        logger.info("AlertNotifier started.")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()

    # -- Webhook management --------------------------------------------------

    def add_webhook(self, url: str, secret: Optional[str] = None) -> WebhookConfig:
        for wh in self._webhooks:
            if wh.url == url:
                return wh          # already registered
        wh = WebhookConfig(url=url, secret=secret)
        self._webhooks.append(wh)
        logger.info("Webhook registered: %s (id=%s)", url, wh.id)
        return wh

    def remove_webhook(self, webhook_id: str) -> bool:
        before = len(self._webhooks)
        self._webhooks = [w for w in self._webhooks if w.id != webhook_id]
        removed = len(self._webhooks) < before
        if removed:
            logger.info("Webhook removed: %s", webhook_id)
        return removed

    def list_webhooks(self) -> list[WebhookConfig]:
        return list(self._webhooks)

    # -- Notification --------------------------------------------------------

    async def notify(self, alert_dict: dict) -> None:
        """Broadcast to SSE subscribers and fire webhook tasks."""
        await self.sse.broadcast(alert_dict)
        for wh in self._webhooks:
            if wh.enabled:
                asyncio.create_task(
                    self._deliver_webhook(wh, alert_dict),
                    name=f"webhook-{wh.id}",
                )

    async def _deliver_webhook(self, wh: WebhookConfig, payload: dict) -> None:
        headers = {"Content-Type": "application/json"}
        if wh.secret:
            headers["X-Webhook-Secret"] = wh.secret
        try:
            resp = await self._client.post(
                wh.url,
                content=json.dumps(payload, default=str),
                headers=headers,
            )
            resp.raise_for_status()
            logger.info("Webhook delivered → %s (%d)", wh.url, resp.status_code)
        except Exception as exc:
            logger.warning("Webhook failed → %s : %s", wh.url, exc)
