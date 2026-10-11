"""innkeeper — the AI innkeeper: the agent that becomes your business system.

One agent core + vertical skill packs. WhatsApp-first. Every action is
journaled as a reversible transaction; nothing the agent does can't be undone.
"""
from innkeeper.core.backend import generate_backend, BusinessBackend
from innkeeper.core.ledger import TransactionLedger, EscalationTier
from innkeeper.core.escalation import EscalationPolicy

__version__ = "0.1.0"
__all__ = [
    "generate_backend",
    "BusinessBackend",
    "TransactionLedger",
    "EscalationTier",
    "EscalationPolicy",
]
