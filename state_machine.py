
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

from fastapi import FastAPI, HTTPException

# Models
def gen_id():
    return str(uuid.uuid4())

class Role(str, Enum):
    DONOR = "donor"
    RECIPIENT = "recipient"
    VOLUNTEER = "volunteer"
    ADMIN = "admin"

class DonationState(str, Enum):
    POSTED = "posted"
    MATCHED = "matched"
    PICKUP_SCHEDULED = "pickup_scheduled"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

class Donation(BaseModel):
    id: str = Field(default_factory=gen_id)
    donor_id: Optional[str] = None
    state: DonationState = DonationState.POSTED
    matched_recipient_id: Optional[str] = None
    pickup_id: Optional[str] = None

class AuditLogEntry(BaseModel):
    id: str = Field(default_factory=gen_id)
    donation_id: str
    actor_user_id: Optional[str] = None
    actor_role: Optional[Role] = None
    old_state: Optional[DonationState] = None
    new_state: DonationState
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    notes: Optional[str] = None

class InMemoryRepo:
    def __init__(self):
        self.donations: Dict[str, Donation] = {}
        self.users: Dict[str, Dict[str, Any]] = {}  # minimal user store for actor roles
        self.audit_logs: Dict[str, List[AuditLogEntry]] = {}

    # Donation Ops 
    def add_donation(self, d: Donation):
        self.donations[d.id] = d
        return d
    def get_donation(self, donation_id: str) -> Optional[Donation]:
        return self.donations.get(donation_id)
    def update_donation(self, d: Donation):
        if d.id not in self.donations:
            raise KeyError("Donation not found")
        self.donations[d.id] = d
        return d

    # User Ops
    def add_user(self, user_id: str, role: str):
        self.users[user_id] = {"id": user_id, "role": role}
    def get_user(self, user_id: str):
        return self.users.get(user_id)

    # Audit Ops
    def add_audit_log(self, entry: AuditLogEntry):
        self.audit_logs.setdefault(entry.donation_id, []).append(entry)
        return entry
    def get_audit_logs_for_donation(self, donation_id: str) -> List[AuditLogEntry]:
        return self.audit_logs.get(donation_id, [])

repo = InMemoryRepo()

# Machine Rules
ALLOWED_TRANSITIONS = {
    DonationState.POSTED: {DonationState.MATCHED, DonationState.CANCELLED, DonationState.EXPIRED},
    DonationState.MATCHED: {DonationState.PICKUP_SCHEDULED, DonationState.CANCELLED, DonationState.EXPIRED},
    DonationState.PICKUP_SCHEDULED: {DonationState.IN_TRANSIT, DonationState.CANCELLED},
    DonationState.IN_TRANSIT: {DonationState.DELIVERED, DonationState.CANCELLED},
    DonationState.DELIVERED: set(),
    DonationState.CANCELLED: set(),
    DonationState.EXPIRED: set(),
}

def transition_state(donation_id: str, new_state: DonationState, actor_user_id: Optional[str] = None, notes: Optional[str] = None) -> Donation:
    d = repo.get_donation(donation_id)
    if not d:
        raise ValueError("Donation not found")
    old_state = d.state
    if new_state == old_state:
        entry = AuditLogEntry(
            donation_id=d.id,
            actor_user_id=actor_user_id,
            actor_role=(repo.get_user(actor_user_id)["role"] if actor_user_id and repo.get_user(actor_user_id) else None),
            old_state=old_state,
            new_state=new_state,
            notes=(notes or "idempotent transition")
        )
        repo.add_audit_log(entry)
        return d
    allowed = ALLOWED_TRANSITIONS.get(old_state, set())
    if new_state not in allowed:
        raise ValueError(f"Invalid transition from {old_state} to {new_state}")
    d.state = new_state
    repo.update_donation(d)
    entry = AuditLogEntry(
        donation_id=d.id,
        actor_user_id=actor_user_id,
        actor_role=(repo.get_user(actor_user_id)["role"] if actor_user_id and repo.get_user(actor_user_id) else None),
        old_state=old_state,
        new_state=new_state,
        notes=notes
    )
    repo.add_audit_log(entry)
    return d


app = FastAPI(title="ShareTray: State Machine (compact)")

class TransitionRequest(BaseModel):
    new_state: DonationState
    actor_user_id: Optional[str] = None
    notes: Optional[str] = None

@app.post("/donations/{donation_id}/transition", response_model=Donation)
def api_transition(donation_id: str, req: TransitionRequest):
    try:
        d = transition_state(donation_id, req.new_state, actor_user_id=req.actor_user_id, notes=req.notes)
        return d
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/donations/{donation_id}/audit_logs", response_model=List[AuditLogEntry])
def api_audit_logs(donation_id: str):
    return repo.get_audit_logs_for_donation(donation_id)

