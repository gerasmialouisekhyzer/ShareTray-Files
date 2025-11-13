import logging
from datetime import datetime
from typing import Optional, Dict, Any
import database
from fastapi import Request

logger = logging.getLogger("audit")

logging.basicConfig(level=logging.INFO)

async def record_audit_event(
    actor_id: str,
    actor_role: str,
    action: str,
    resource: str,
    details: Optional[Dict[str, Any]] = None,
    request: Optional[Request] = None
):

    entry = {
        "timestamp": datetime.utcnow(),
        "actor_id": actor_id,
        "actor_role": actor_role,
        "action": action,
        "resource": resource,
        "details": details or {},
    }
    if request:
        entry["ip_address"] = request.client.host if request.client else None
        entry["path"] = request.url.path

    await database.database["audit_logs"].insert_one(entry)

    logger.info(f"AUDIT: {actor_role}({actor_id}) performed {action} on {resource} – details={entry['details']}")

def build_report_summary():

    from bson import DESCENDING
    now = datetime.utcnow()
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cursor = database.database["audit_logs"].find({"timestamp": {"$gte": cutoff}})
    summary: Dict[str, int] = {}
    async def _inner():
        async for ev in cursor:
            act = ev.get("action", "unknown")
            summary[act] = summary.get(act, 0) + 1
        return summary
    return _inner()
