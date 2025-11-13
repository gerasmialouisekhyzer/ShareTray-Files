import logging
from datetime import datetime
from typing import List
from fastapi import FastAPI, HTTPException, Depends, status, Request, BackgroundTasks
from pydantic import BaseModel, Field
import nest_asyncio
from pyngrok import ngrok
import database
from role import router as role_router, require_role, get_current_user
from audit import record_audit_event
import uvicorn
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ShareTray",
    description="API for matching donations and recipients for food waste reduction and community support.",
    version="0.1.0"
)

app.include_router(role_router, prefix="", tags=["users"])

class DonationCreate(BaseModel):
    donor_id: str
    item: str
    quantity: int = Field(gt=0)
    pickup_address: str

class Donation(BaseModel):
    id: str
    donor_id: str
    item: str
    quantity: int
    pickup_address: str
    status: str
    created_at: datetime
    updated_at: datetime

@app.on_event("startup")
async def on_startup():
    await database.init_db()

@app.on_event("shutdown")
async def on_shutdown():
    await database.close_db()

@app.post(
    "/donations",
    response_model=Donation,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(["donor", "admin"]))]
)
async def create_donation(
    d: DonationCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    request: Request = None
):
    now = datetime.utcnow()
    doc = {
        "donor_id": d.donor_id,
        "item": d.item,
        "quantity": d.quantity,
        "pickup_address": d.pickup_address,
        "status": "pending",
        "created_at": now,
        "updated_at": now
    }
    new_doc = await database.insert_donation(doc)

    background_tasks.add_task(
        record_audit_event,
        actor_id = current_user["id"],
        actor_role = current_user["role"],
        action = "create_donation",
        resource = f"donation:{new_doc['id']}",
        details = {"item": d.item, "quantity": d.quantity, "pickup_address": d.pickup_address},
        request = request
    )

    return Donation(**new_doc)

@app.get(
    "/donations",
    response_model=List[Donation],
    dependencies=[Depends(require_role(["admin", "volunteer", "donor"]))]
)
async def list_donations(
    skip: int = 0,
    limit: int = 100,
    background_tasks: BackgroundTasks = None,
    current_user: dict = Depends(get_current_user),
    request: Request = None
):
    docs = await database.find_all_donations(skip=skip, limit=limit)

    background_tasks.add_task(
        record_audit_event,
        actor_id = current_user["id"],
        actor_role = current_user["role"],
        action = "list_donations",
        resource = "donations",
        details = {"skip": skip, "limit": limit},
        request = request
    )

    return [Donation(**d) for d in docs]

@app.get(
    "/donations/{donation_id}",
    response_model=Donation,
    dependencies=[Depends(require_role(["admin", "volunteer", "donor"]))]
)
async def get_donation(
    donation_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    request: Request = None
):
    doc = await database.find_donation_by_id(donation_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Donation not found")

    background_tasks.add_task(
        record_audit_event,
        actor_id = current_user["id"],
        actor_role = current_user["role"],
        action = "get_donation",
        resource = f"donation:{donation_id}",
        details = {},
        request = request
    )

    return Donation(**doc)

@app.put(
    "/donations/{donation_id}",
    response_model=Donation,
    dependencies=[Depends(require_role(["admin", "volunteer"]))]
)
async def update_donation(
    donation_id: str,
    d: DonationCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    request: Request = None
):
    now = datetime.utcnow()
    update_data = {
        "item": d.item,
        "quantity": d.quantity,
        "pickup_address": d.pickup_address,
        "updated_at": now
    }
    updated = await database.update_donation_by_id(donation_id, update_data)
    if not updated:
        raise HTTPException(status_code=404, detail="Donation not found or not updated")

    background_tasks.add_task(
        record_audit_event,
        actor_id = current_user["id"],
        actor_role = current_user["role"],
        action = "update_donation",
        resource = f"donation:{donation_id}",
        details = {"item": d.item, "quantity": d.quantity, "pickup_address": d.pickup_address},
        request = request
    )

    return Donation(**updated)

@app.delete(
    "/donations/{donation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(["admin"]))]
)
async def delete_donation(
    donation_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    request: Request = None
):
    ok = await database.delete_donation_by_id(donation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Donation not found or not deleted")

    background_tasks.add_task(
        record_audit_event,
        actor_id = current_user["id"],
        actor_role = current_user["role"],
        action = "delete_donation",
        resource = f"donation:{donation_id}",
        details = {},
        request = request
    )

    return None

@app.get("/", dependencies=[Depends(require_role(["admin", "volunteer", "donor"]))])
async def root(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
    request: Request = None
):
    background_tasks.add_task(
        record_audit_event,
        actor_id = current_user["id"],
        actor_role = current_user["role"],
        action = "access_root",
        resource = "root",
        details = {},
        request = request
    )
    return {"message": "ShareTray API is up and running"}

YOUR_TOKEN = "34eacO0is2mmQBXrTPox6kaoLy7_5tW2kzkU4ifeiSuGmYv95"
nest_asyncio.apply()
ngrok.set_auth_token(YOUR_TOKEN)

port = 8000
public_url = ngrok.connect(addr=port, pooling_enabled=True).public_url
logger.info(f"🔗 Public URL: {public_url} -> http://127.0.0.1:{port}")
print(f"🔗 Public URL: {public_url}")

async def main():
    config = uvicorn.Config(app=app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
