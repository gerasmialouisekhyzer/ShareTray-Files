from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, Dict, Any, List
import os
from bson import ObjectId

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "sharetray")

client: Optional[AsyncIOMotorClient] = None
database = None
users_collection = None
donations_collection = None
audit_logs_collection = None

async def init_db():
    global client, database, users_collection, donations_collection, audit_logs_collection
    client = AsyncIOMotorClient(MONGO_URI)
    database = client[MONGO_DB]
    users_collection = database["users"]
    donations_collection = database["donations"]
    audit_logs_collection = database["audit_logs"]
    # Basic Indexes
    await donations_collection.create_index([("status", 1)])
    await donations_collection.create_index([("created_at", -1)])
    await users_collection.create_index([("username", 1)], unique=True)

async def close_db():
    global client
    if client:
        client.close()
        client = None

# CRUD Helpers

async def insert_donation(doc: Dict[str, Any]) -> Dict[str, Any]:
    res = await donations_collection.insert_one(doc)
    doc = await donations_collection.find_one({"_id": res.inserted_id})
    doc["id"] = str(doc["_id"])
    return doc

async def find_all_donations(skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
    cursor = donations_collection.find().skip(skip).limit(limit)
    docs = []
    async for d in cursor:
        d["id"] = str(d["_id"])
        docs.append(d)
    return docs

async def find_donation_by_id(did: str) -> Optional[Dict[str, Any]]:
    try:
        o = ObjectId(did)
    except Exception:
        return None
    d = await donations_collection.find_one({"_id": o})
    if not d:
        return None
    d["id"] = str(d["_id"])
    return d

async def update_donation_by_id(did: str, update_fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        o = ObjectId(did)
    except Exception:
        return None
    update_fields = {"$set": update_fields}
    res = await donations_collection.find_one_and_update({"_id": o}, update_fields, return_document=True)
    if not res:
        return None
    res["id"] = str(res["_id"])
    return res

async def delete_donation_by_id(did: str) -> bool:
    try:
        o = ObjectId(did)
    except Exception:
        return False
    res = await donations_collection.delete_one({"_id": o})
    return res.deleted_count == 1

# Small Helper
async def insert_audit_entry(entry: Dict[str, Any]):
    await audit_logs_collection.insert_one(entry)
    return True
