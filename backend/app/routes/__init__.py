from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from bson import ObjectId
from datetime import datetime, timezone

from ..auth import get_current_user
from ..db import get_db

fix_router = APIRouter()

class CompanyProfile(BaseModel):
    name: str = "Ethinx Solutions"
    location: str = "Adelaide, South Australia"
    city: str = "Adelaide"
    tagline: str = ""

@fix_router.put("/users/me/profile")
async def update_my_profile(body: dict, user=Depends(get_current_user)):
    db = get_db()
    update = {}
    if "company_profile" in body:
        cp = body["company_profile"]
        if isinstance(cp, dict):
            loc = cp.get("location", "")
            if "sydney" in loc.lower():
                cp["location"] = "Adelaide, South Australia"
                cp["city"] = "Adelaide"
            update["company_profile"] = cp
    if "company_name" in body:
        update["company_name"] = body["company_name"]
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields")
    update["updated_at"] = datetime.now(timezone.utc)
    await db.users.update_one({"email": user["email"]}, {"$set": update})
    return await db.users.find_one({"email": user["email"]}, {"password_hash": 0})

@fix_router.put("/projects/{project_id}")
async def update_project(project_id: str, body: dict, user=Depends(get_current_user)):
    db = get_db()
    allowed = {"hook", "script", "scene_plan", "metadata", "title", "description", "thumbnail_concepts"}
    def fix_sydney(obj):
        if isinstance(obj, str):
            return obj.replace("Sydney", "Adelaide").replace("sydney", "Adelaide")
        if isinstance(obj, dict):
            return {k: fix_sydney(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [fix_sydney(x) for x in obj]
        return obj
    update = {}
    for k, v in body.items():
        if k in allowed:
            update[k] = fix_sydney(v)
    if not update:
        raise HTTPException(status_code=400, detail="No editable fields")
    update["updated_at"] = datetime.now(timezone.utc)
    try:
        oid = ObjectId(project_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid project id")
    result = await db.projects.update_one({"_id": oid, "owner_email": user["email"]}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Project not found or not yours")
    return await db.projects.find_one({"_id": oid})

@fix_router.get("/stats/me")
async def stats_me(user=Depends(get_current_user)):
    db = get_db()
    pipeline = [{"$match": {"owner_email": user["email"]}}, {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}, "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    data = await db.projects.aggregate(pipeline).to_list(length=100)
    return {"email": user["email"], "projects_over_time": data, "note": "Filtered to your account only"}

@fix_router.get("/stats/global")
async def stats_global(user=Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    db = get_db()
    pipeline = [{"$match": {"owner_email": {"$not": {"$regex": "test_stock"}}}}, {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}, "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    data = await db.projects.aggregate(pipeline).to_list(length=100)
    return {"projects_over_time": data, "filtered": "test_stock excluded"}
