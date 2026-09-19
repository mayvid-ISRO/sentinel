"""
Multi-tier Approval Manager — Phase 3 RBAC
=============================================
Tracks which tasks are awaiting human approval and collects signatures
from multiple reviewers.

A destructive task (2 approvers) enters the queue with tier 1 pending.
Once tier 1 signs off, the task moves to tier 2. When tier 2 signs,
the task is authorised and can proceed.

This file is imported lazily by backend/main.py to avoid circular imports.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_APPROVALS_PATH = Path(".iris/approvals.json")


@dataclass
class ApprovalSig:
    reviewer: str       # username
    role: str
    tier: int           # 1-based, matches auth.policy.approvals_required
    signed_at: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovalSig":
        return cls(**d)


@dataclass
class ApprovalTicket:
    task_id: str
    tool_name: str
    risk_tier: str
    required_approvals: int
    status: str           # "pending" | "tier1_done" | "approved" | "rejected"
    created_by: str
    created_at: str
    signatures: List[ApprovalSig] = field(default_factory=list)
    rejection_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "risk_tier": self.risk_tier,
            "required_approvals": self.required_approvals,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "signatures": [s.to_dict() for s in self.signatures],
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovalTicket":
        sigs = [ApprovalSig.from_dict(s) for s in d.pop("signatures", [])]
        return cls(signatures=sigs, **d)


class ApprovalManager:
    """In-memory store backed by .iris/approvals.json on disk."""

    def __init__(self, path: Path = _APPROVALS_PATH) -> None:
        self._path = path
        self._tickets: Dict[str, ApprovalTicket] = self._load()

    def _load(self) -> Dict[str, ApprovalTicket]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {k: ApprovalTicket.from_dict(v) for k, v in data.items()}
        except Exception as exc:
            logger.warning("Could not load approvals (%s); starting fresh", exc)
            return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({k: v.to_dict() for k, v in self._tickets.items()}, indent=2),
            encoding="utf-8",
        )

    def create_ticket(self, task_id: str, tool_name: str, risk_tier: str,
                       required_approvals: int, created_by: str) -> ApprovalTicket:
        ticket = ApprovalTicket(
            task_id=task_id,
            tool_name=tool_name,
            risk_tier=risk_tier,
            required_approvals=required_approvals,
            status="pending",
            created_by=created_by,
            created_at=datetime.utcnow().isoformat(),
        )
        self._tickets[task_id] = ticket
        self._save()
        return ticket

    def sign_tier(self, task_id: str, reviewer: str, role: str,
                   tier: int, note: str = "") -> Optional[ApprovalTicket]:
        ticket = self._tickets.get(task_id)
        if ticket is None or ticket.status in ("approved", "rejected"):
            return None
        # Check already signed
        if any(s.reviewer == reviewer and s.tier == tier for s in ticket.signatures):
            return ticket
        sig = ApprovalSig(reviewer=reviewer, role=role, tier=tier,
                          signed_at=datetime.utcnow().isoformat(), note=note)
        ticket.signatures.append(sig)
        # Progress through tiers; if all required approvals collected, mark approved
        if len(ticket.signatures) >= ticket.required_approvals:
            ticket.status = "approved"
        elif tier == 1 and len(ticket.signatures) >= 1:
            ticket.status = "tier1_done"
        self._save()
        return ticket

    def reject(self, task_id: str, reviewer: str, role: str, reason: str = "") -> Optional[ApprovalTicket]:
        ticket = self._tickets.get(task_id)
        if ticket is None or ticket.status == "rejected":
            return None
        ticket.status = "rejected"
        ticket.rejection_reason = reason
        self._save()
        return ticket

    def get(self, task_id: str) -> Optional[ApprovalTicket]:
        return self._tickets.get(task_id)

    def all_pending(self) -> List[ApprovalTicket]:
        return [t for t in self._tickets.values() if t.status not in ("approved", "rejected")]

    def clear(self) -> None:
        self._tickets.clear()
        self._save()


# Module-level singleton — created lazily
_manager: Optional[ApprovalManager] = None


def get_approval_manager() -> ApprovalManager:
    global _manager
    if _manager is None:
        _manager = ApprovalManager()
    return _manager
