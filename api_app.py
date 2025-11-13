from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import math, os

from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, Field

# Import Integration Points From Modules
try:
    from models_repo import repo, User, Donation, Recipient, Pickup, Transaction, AuditLogEntry, Perishability, DonationState
except Exception:
    from pydantic import BaseModel
    import uuid
    def gen_id(): return str(uuid.uuid4())

    class Perishability(str):
        FRESH = "fresh"; REFRIGERATED = "refrigerated"; STABLE = "stable"

    class DonationState(str):
        POSTED="posted"; MATCHED="matched"; PICKUP_SCHEDULED="pickup_scheduled"
        IN_TRANSIT="in_transit"; DELIVERED="delivered"; CANCELLED="cancelled"; EXPIRED="expired"

    class User(BaseModel):
        id: str = Field(default_factory=gen_id); name: str = ""; role: str = ""; phone: Optional[str] = None; location: Optional[Dict[str,Any]] = None
    class Donation(BaseModel):
        id: str = Field(default_factory=gen_id); donor_id: str = ""; items: List[Dict]=[]; total_weight_kg: float = 0.0
        posted_at: datetime = Field(default_factory=datetime.utcnow); pickup_by: Optional[datetime]=None; location: Optional[Dict]=None
        state: str = DonationState.POSTED; matched_recipient_id: Optional[str]=None; pickup_id: Optional[str]=None
    class Recipient(BaseModel):
        id: str = Field(default_factory=gen_id); name: str = ""; capacity_kg: float = 0.0; location: Optional[Dict]=None; contact: Optional[str]=None
    class Pickup(BaseModel):
        id: str = Field(default_factory=gen_id); volunteer_id: Optional[str]=None; donation_ids: List[str] = []; route_order: List[Tuple[float,float]] = []
        scheduled_for: Optional[datetime]=None; status: str="scheduled"; metadata: Dict=str
    class Transaction(BaseModel):
        id: str = Field(default_factory=gen_id); donation_id: str = ""; recipient_id: str = ""; picked_up_at: Optional[datetime]=None; delivered_at: Optional[datetime]=None
    class AuditLogEntry(BaseModel):
        id: str = Field(default_factory=gen_id); donation_id: str = ""; actor_user_id: Optional[str]=None; actor_role: Optional[str]=None
        old_state: Optional[str]=None; new_state: str = ""; timestamp: datetime = Field(default_factory=datetime.utcnow); notes: Optional[str]=None

    # Local Repo
    class InMemoryRepo:
        def __init__(self):
            self.users = {}; self.donations = {}; self.recipients = {}; self.pickups = {}; self.transactions = {}; self.audit_logs = {}
        def add_user(self,u): self.users[u.id]=u; return u
        def get_user(self,uid): return self.users.get(uid)
        def add_donation(self,d): self.donations[d.id]=d; return d
        def get_donation(self,did): return self.donations.get(did)
        def update_donation(self,d): self.donations[d.id]=d; return d
        def list_open_donations(self): return [d for d in self.donations.values() if d.state==DonationState.POSTED]
        def add_recipient(self,r): self.recipients[r.id]=r; return r
        def list_recipients(self): return list(self.recipients.values())
        def update_recipient(self,r): self.recipients[r.id]=r; return r
        def add_pickup(self,p): self.pickups[p.id]=p; return p
        def update_pickup(self,p): self.pickups[p.id]=p; return p
        def add_transaction(self,t): self.transactions[t.id]=t; return t
        def add_audit_log(self,log): self.audit_logs.setdefault(log.donation_id,[]).append(log); return log
        def get_audit_logs_for_donation(self,did): return self.audit_logs.get(did,[])
    repo = InMemoryRepo()

try:
    from state_machine import transition_state
except Exception:
    ALLOWED = {
        "posted": {"matched","cancelled","expired"},
        "matched": {"pickup_scheduled","cancelled","expired"},
        "pickup_scheduled": {"in_transit","cancelled"},
        "in_transit": {"delivered","cancelled"},
        "delivered": set(), "cancelled": set(), "expired": set()
    }
    def transition_state(donation_id, new_state, actor_user_id=None, notes=None):
        d = repo.get_donation(donation_id)
        if not d: raise ValueError("donation not found")
        old = d.state
        if new_state == old:
            # idempotent: log and return
            log = AuditLogEntry(donation_id=d.id, actor_user_id=actor_user_id, actor_role=(repo.get_user(actor_user_id).role if actor_user_id and repo.get_user(actor_user_id) else None), old_state=old, new_state=new_state, notes=notes)
            repo.add_audit_log(log)
            return d
        if new_state not in ALLOWED.get(old, set()):
            raise ValueError(f"Invalid transition {old} -> {new_state}")
        d.state = new_state
        repo.update_donation(d)
        log = AuditLogEntry(donation_id=d.id, actor_user_id=actor_user_id, actor_role=(repo.get_user(actor_user_id).role if actor_user_id and repo.get_user(actor_user_id) else None), old_state=old, new_state=new_state, notes=notes)
        repo.add_audit_log(log)
        return d

# RolesManager
try:
    from user_roles_criteria import RolesManager
    roles_mgr = RolesManager()
except Exception:
    class RolesManager:
        def __init__(self): self._data = {"donor":[{"id":"r1","text":"Post donation with weight & location","mandatory":True}], "recipient":[{"id":"r2","text":"Accept/reject matches","mandatory":True}], "volunteer":[{"id":"r3","text":"Receive route & mark pickup","mandatory":True}], "admin":[{"id":"r4","text":"View totals & export CSV","mandatory":True}]}
        def list_criteria(self, role): return self._data.get(role,[])
    roles_mgr = RolesManager()

# Haversine
def haversine_distance(lat1, lon1, lat2, lon2):
    R=6371.0
    import math
    phi1=math.radians(lat1); phi2=math.radians(lat2)
    dphi=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return R*2*math.atan2(math.sqrt(a), math.sqrt(1-a))

try:
    from matching import greedy_match as ext_greedy_match
except Exception:
    ext_greedy_match = None

try:
    from routing import plan_route_nearest_neighbor as ext_plan_route
except Exception:
    ext_plan_route = None

def greedy_match_local(max_search_km: float = 10.0):
    """Simple greedy: nearest recipient with capacity >= donation weight; perishable priority not implemented here (keeps simple)."""
    assigned = []
    donations = repo.list_open_donations()
    for d in donations:
        if not getattr(d, "location", None): continue
        d_lat = d.location["coordinates"][1]; d_lon = d.location["coordinates"][0]
        need = getattr(d, "total_weight_kg", 0.0)
        candidates = []
        for r in repo.list_recipients():
            if getattr(r, "capacity_kg", 0) >= need and getattr(r, "location", None):
                r_lat = r.location["coordinates"][1]; r_lon = r.location["coordinates"][0]
                dist = haversine_distance(d_lat, d_lon, r_lat, r_lon)
                if dist <= max_search_km:
                    candidates.append((dist, r))
        if not candidates: continue
        candidates.sort(key=lambda x: x[0])
        chosen = candidates[0][1]
        d.matched_recipient_id = chosen.id
        transition_state(d.id, "matched", actor_user_id=None, notes="auto-match")
        repo.update_donation(d)
        chosen.capacity_kg -= need
        repo.update_recipient(chosen)
        assigned.append((d.id, chosen.id))
    return assigned

def plan_route_local(volunteer_id: str, donation_ids: List[str]) -> List[Tuple[float,float]]:
    """Nearest-neighbor ordering returning list of (lat, lon)."""
    v = repo.get_user(volunteer_id)
    if not v or not getattr(v, "location", None):
        raise ValueError("Volunteer missing or has no location")
    start_lat = v.location["coordinates"][1]; start_lon = v.location["coordinates"][0]
    points = []
    for did in donation_ids:
        d = repo.get_donation(did)
        if not d or not getattr(d, "location", None):
            raise ValueError(f"Donation {did} missing or has no location")
        points.append((d.location["coordinates"][1], d.location["coordinates"][0]))  # lat, lon
    order = []
    current = (start_lat, start_lon)
    remaining = points.copy()
    import math
    while remaining:
        distances = [(haversine_distance(current[0], current[1], p[0], p[1]), p) for p in remaining]
        distances.sort(key=lambda x: x[0])
        nearest = distances[0][1]
        order.append(nearest)
        remaining.remove(nearest)
        current = nearest
    return order

# FastAPI app
app = FastAPI(title="ShareTray - Minimal API")

class MatchRequest(BaseModel):
    max_search_km: float = 5.0

class PlanPickupRequest(BaseModel):
    volunteer_id: str
    donation_ids: List[str]

class TransitionRequest(BaseModel):
    new_state: str
    actor_user_id: Optional[str] = None
    notes: Optional[str] = None

@app.post("/users", response_model=Dict[str,Any])
def create_user(u: Dict[str,Any]):
    user = User(**u) if isinstance(u, dict) else User(**u.dict())
    repo.add_user(user)
    return {"id": user.id, "created_at": datetime.utcnow().isoformat()}

@app.post("/donations", response_model=Dict[str,Any])
def create_donation(d: Dict[str,Any]):
    donation = Donation(**d) if isinstance(d, dict) else Donation(**d.dict())
    if not repo.get_user(donation.donor_id):
        raise HTTPException(status_code=404, detail="donor not found")
    repo.add_donation(donation)
    return {"id": donation.id, "posted_at": donation.posted_at.isoformat()}

@app.get("/donations/open", response_model=List[Dict[str,Any]])
def list_open_donations():
    return [d.dict() for d in repo.list_open_donations()]

@app.post("/match/run")
def run_matching(req: MatchRequest = Body(...)):
    if ext_greedy_match:
        assigned = ext_greedy_match(repo, req.max_search_km)
    else:
        assigned = greedy_match_local(req.max_search_km)
    return {"assigned": assigned, "count": len(assigned)}

@app.post("/pickups/plan", response_model=Dict[str,Any])
def plan_pickup(req: PlanPickupRequest):
    # Choose Planner
    if ext_plan_route:
        route = ext_plan_route(repo.get_user(req.volunteer_id).location["coordinates"][0], repo.get_user(req.volunteer_id).location["coordinates"][1], [(repo.get_donation(d).location["coordinates"][0], repo.get_donation(d).location["coordinates"][1]) for d in req.donation_ids])
        ordered = [(p[1], p[0]) for p in route]
    else:
        ordered = plan_route_local(req.volunteer_id, req.donation_ids)
    p = Pickup(volunteer_id=req.volunteer_id, donation_ids=req.donation_ids, route_order=ordered, scheduled_for=datetime.utcnow() + timedelta(minutes=30))
    repo.add_pickup(p)
    for did in req.donation_ids:
        d = repo.get_donation(did)
        transition_state(d.id, "pickup_scheduled", actor_user_id=req.volunteer_id, notes=f"pickup {p.id} planned")
        d.pickup_id = p.id
        repo.update_donation(d)
    return {"pickup_id": p.id, "route": ordered, "scheduled_for": p.scheduled_for.isoformat()}

@app.post("/donations/{donation_id}/transition", response_model=Dict[str,Any])
def api_transition(donation_id: str, req: TransitionRequest):
    try:
        d = transition_state(donation_id, req.new_state, actor_user_id=req.actor_user_id, notes=req.notes)
        return {"id": d.id, "state": d.state}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/donations/{donation_id}/audit_logs")
def api_audit_logs(donation_id: str):
    logs = repo.get_audit_logs_for_donation(donation_id)
    return [l.dict() for l in logs]

@app.get("/roles/criteria")
def get_roles_criteria(role: Optional[str] = None):
    if role:
        return roles_mgr.list_criteria(role)
    out = {}
    for r in ["donor","recipient","volunteer","admin"]:
        out[r] = roles_mgr.list_criteria(r)
    return out

@app.get("/reports/summary")
def reports_summary():
    # Count Delivered, Total Weight Delivered
    total = 0.0; cnt = 0
    for d in getattr(repo, "donations", {}).values():
        if getattr(d, "state", None) == getattr(DonationState, "DELIVERED", "delivered"):
            total += getattr(d, "total_weight_kg", 0.0)
            cnt += 1
    return {"delivered_count": cnt, "total_weight_kg": total}

# Simple Seed for Testing
@app.post("/seed/demo")
def seed_demo():
    donor = User(name="Demo Cafe", role="donor", location={"type":"Point","coordinates":[121.001,14.601]})
    repo.add_user(donor)
    recipient = Recipient(name="Demo Pantry", capacity_kg=100.0, location={"type":"Point","coordinates":[121.005,14.605]}, contact="09170000000")
    repo.add_recipient(recipient)
    volunteer = User(name="Demo Vol", role="volunteer", location={"type":"Point","coordinates":[121.002,14.603]})
    repo.add_user(volunteer)
    donation = Donation(donor_id=donor.id, items=[{"name":"Cooked Rice","quantity":10}], total_weight_kg=5.0, location={"type":"Point","coordinates":[121.0015,14.602]})
    repo.add_donation(donation)
    return {"donor_id": donor.id, "recipient_id": recipient.id, "volunteer_id": volunteer.id, "donation_id": donation.id}

