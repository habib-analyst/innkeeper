"""WhatsApp Cloud API transport (production adapter).

Uses the Meta-hosted WhatsApp Cloud API directly ($0 platform; ~1,000 free
service messages per number per month — a guest house lives inside this).

Setup (one-time, Habib's task — NOT automated here):
  1. Meta developer app + WhatsApp product, phone number attached.
  2. Business verification for production messaging limits.
  3. Permanent access token with whatsapp_business_messaging scope.
  4. Webhook pointed at your server for inbound messages.

Env: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID.

Until those exist, use MockTransport — the agent core is identical.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Dict, List

from innkeeper.transport.base import InboundMessage, OutboundMessage, Transport

GRAPH = "https://graph.facebook.com/v21.0"


class WhatsAppCloudTransport(Transport):
    name = "whatsapp_cloud"

    def __init__(self, token: str | None = None, phone_number_id: str | None = None):
        self.token = token or os.environ.get("WHATSAPP_TOKEN", "")
        self.phone_number_id = phone_number_id or os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        if not self.token or not self.phone_number_id:
            raise RuntimeError(
                "WhatsApp Cloud API credentials missing: set WHATSAPP_TOKEN and "
                "WHATSAPP_PHONE_NUMBER_ID (see module docstring for setup)"
            )

    def _post(self, payload: Dict) -> Dict:
        req = urllib.request.Request(
            f"{GRAPH}/{self.phone_number_id}/messages",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def send(self, msg: OutboundMessage) -> str:
        if msg.kind == "template" and msg.template:
            payload = {
                "messaging_product": "whatsapp",
                "to": msg.to,
                "type": "template",
                "template": {"name": msg.template, "language": {"code": "en"}},
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "to": msg.to,
                "type": "text",
                "text": {"body": msg.text, "preview_url": False},
            }
        resp = self._post(payload)
        return resp["messages"][0]["id"]

    def poll(self) -> List[InboundMessage]:
        # Production inbound arrives via webhook -> your server enqueues it.
        # This adapter exposes `ingest_webhook(payload)` for that path instead.
        return []

    def ingest_webhook(self, payload: Dict) -> List[InboundMessage]:
        """Convert a WhatsApp webhook payload into InboundMessages."""
        out: List[InboundMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for m in change.get("value", {}).get("messages", []):
                    if m.get("type") == "text":
                        out.append(
                            InboundMessage(
                                sender=m["from"],
                                text=m["text"]["body"],
                                ts=m.get("timestamp", ""),
                                meta={"wa_id": m["id"]},
                            )
                        )
        return out
