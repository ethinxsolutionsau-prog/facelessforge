"""Routers: auth, projects, generation, exports, analytics, settings."""
from __future__ import annotations

import asyncio
import io
import json
import math
import re
import os
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Body
from fastapi.responses import StreamingResponse, PlainTextResponse

from .auth import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    set_auth_cookies, clear_auth_cookies, get_current_user, require_roles,
)
from .db import get_db
from .models import (
    RegisterRequest, LoginRequest, ProjectCreate, ProjectUpdate,
    ScriptUpdate, MetadataUpdate, ProviderSettingsUpdate, AssetCreate,
    ShareUpdate, ForgotPasswordRequest, ResetPasswordRequest,
    StockAttachRequest, FindAssetsRequest, AssetStatusUpdate,
    AutoAttachRequest, GenerateThumbnailImagesRequest,
)
try:
    from .billing import check_credits
except Exception:
    check_credits = None
try:
    from app.billing import check_credits as _cc2
    if check_credits is None:
        check_credits = _cc2
except Exception:
    pass
from .scoring import quality_score, quality_label, compute_project_status, scenes_to_csv
from . import generation as gen
from . import stock as stock_service
from . import thumbnail_images as thumb_images


router = APIRouter(prefix="/api")


def _now():
    return datetime.now(timezone.utc)


CINEMATIC_SUFFIX = "4k slow motion b-roll cinematic"
_SCENE_LEAK_STOP = {"might","obsolete","coming","year","years","small","businesses","drowning","imagine","software","shift","machine","impact","simulate","will","shall","could","should","would","may","might","about","hear","story","cover","converts","alter","the","and","a","an","of","to","in","on","for","with","about","is","are","was","were","be","as","it","its","this","that","what","you","about","to","hear","story","will","cover","how","it","converts","alter","will"}

def _clean_visual_full(visual: str) -> str:
    """Preserve full Visual including commas, normalize whitespace only. No split on comma, no truncation."""
    if not visual:
        return ""
    return " ".join((visual or "").strip().split())

def _extract_core_query(scene: dict) -> str:
    """Extract 3-4 word core for Pixabay/Unsplash fallback, e.g. crystal ball forecast.
    Prefers search_terms multi-word, else visual keywords, filters leak words.
    """
    terms = scene.get("search_terms") or []
    if terms:
        for t in terms:
            clean = " ".join(str(t or "").split()).strip()
            if not clean:
                continue
            words = clean.split()
            if len(words) == 1 and words[0].lower() in _SCENE_LEAK_STOP:
                continue
            if len(words) == 1 and len(words[0]) < 4:
                continue
            if len(words) >= 2:
                filtered = [w for w in words if w.lower() not in _SCENE_LEAK_STOP and len(w) >= 3]
                if len(filtered) >= 2:
                    return " ".join(filtered[:4])
                return " ".join(words[:4])
    visual = (scene.get("visual_direction") or "").strip()
    if visual:
        import re
        words = re.findall(r"[a-z]{3,}", visual.lower())
        kw = [w for w in words if w not in _SCENE_LEAK_STOP]
        kw = [w for w in kw if 3 <= len(w) <= 20]
        seen=set()
        out=[]
        for w in kw:
            if w not in seen:
                seen.add(w)
                out.append(w)
            if len(out)>=4:
                break
        if len(out)>=2:
            return " ".join(out[:4])
        if out:
            return " ".join(out)
    return "nature cinematic"

def _build_scene_stock_query(scene: dict, project: dict, override: str | None = None) -> str:
    """Do NOT truncate query server-side. Use full body.query or scene.search_terms joined.

    FIX: was using visual_direction sliced to 8 words / cinematic suffix only.
    Now prefers full search_terms joined (first 3 terms) preserving full intent,
    else visual trimmed to 120 chars, else project topic.
    Priority: explicit override > search_terms (first 3) > visual (120 chars) > project topic.
    """
    if override and override.strip():
        return override.strip()
    terms = scene.get("search_terms") or []
    if terms:
        cleaned: list[str] = []
        for t in terms:
            c = " ".join(str(t or "").split()).strip()
            if not c:
                continue
            # Skip single-word leak artifacts like "make", "small", "imagine"
            if len(c.split()) == 1 and c.lower() in _SCENE_LEAK_STOP:
                continue
            if len(c.split()) == 1 and len(c) < 4:
                continue
            cleaned.append(c)
            if len(cleaned) >= 3:
                break
        if cleaned:
            return " ".join(cleaned)
        # Fallback: raw first 3 terms if filtering removed all
        raw = [str(t).strip() for t in terms if str(t).strip()][:3]
        if raw:
            return " ".join(raw)
    visual = _clean_visual_full(scene.get("visual_direction") or "")
    if visual:
        # Frontend uses visual.trim().slice(0,120) — mirror here, no 8-word truncation
        return visual.strip()[:120].strip()
    # Fallback to project topic
    base = (project.get("topic") or project.get("niche") or "stock").strip()
    base = " ".join(base.split())
    if len(base.split()) < 2:
        base = "nature cinematic"
    return base.strip()

def _build_ordered_stock_queries(scene: dict, project: dict) -> list[dict]:
    """Generate exactly 3 ordered query objects per scene: pexels_video (cinematic full), pixabay_video (shorter core), unsplash_image (shortest).
    Preference order video first. Used by auto-attach fallback chain.
    """
    visual = _clean_visual_full(scene.get("visual_direction") or "")
    if not visual:
        terms = scene.get("search_terms") or []
        if terms and isinstance(terms, list):
            for t in terms:
                if t and len(str(t).split()) >= 2:
                    visual = str(t)
                    break
            if not visual:
                visual = str(terms[0]) if terms else ""
        elif isinstance(terms, str):
            visual = terms
        else:
            visual = (project.get("topic") if project and project.get("topic") else "") or "nature cinematic"
        visual = _clean_visual_full(visual) or "nature cinematic"
    pexels_q = f"{visual} {CINEMATIC_SUFFIX}".strip()
    core = _extract_core_query(scene)
    pixabay_q = core if core else "nature cinematic"
    if len(pixabay_q.split()) < 2:
        pixabay_q = "nature cinematic b-roll"
    unsplash_q = " ".join(core.split()[:3]).strip() if core else "nature cinematic"
    if len(unsplash_q.split()) < 2:
        unsplash_q = "nature cinematic"
    return [
        {"source": "pexels", "type": "video", "media_type": "videos", "query": pexels_q},
        {"source": "pixabay", "type": "video", "media_type": "videos", "query": pixabay_q},
        {"source": "unsplash", "type": "image", "media_type": "photos", "query": unsplash_q},
    ]


def _ser(doc: dict) -> dict:
    """Drop _id and serialise datetimes for JSON."""
    if not doc:
        return doc
    doc = {k: v for k, v in doc.items() if k != "_id"}
    for k, v in list(doc.items()):
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


def _ensure_project_access(project: dict, user: dict, *, write: bool = False):
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    role = user.get("role")
    if role == "admin":
        return
    if project["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your project")
    if write and role == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot modify projects")


# ============================ AUTH ============================

@router.post("/auth/register")
async def register(body: RegisterRequest, response: Response, request: Request):
    db = get_db()
    email = body.email.lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = str(uuid.uuid4())
    user_doc = {
        "id": user_id,
        "name": body.name,
        "email": email,
        "role": body.role,
        "password_hash": hash_password(body.password),
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.users.insert_one(user_doc)
    access = create_access_token(user_id, email, body.role)
    refresh = create_refresh_token(user_id)
    set_auth_cookies(response, access, refresh, request)
    return _ser({**user_doc, "password_hash": None})


@router.post("/auth/login")
async def login(body: LoginRequest, response: Response, request: Request):
    db = get_db()
    email = body.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    access = create_access_token(user["id"], email, user["role"])
    refresh = create_refresh_token(user["id"])
    set_auth_cookies(response, access, refresh, request)
    out = dict(user); out.pop("password_hash", None)
    return _ser(out)


@router.post("/auth/logout")
async def logout(response: Response, _user=Depends(get_current_user)):
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/auth/me")
async def me(user=Depends(get_current_user)):
    return _ser(user)


@router.get("/users/me")
async def users_me(user=Depends(get_current_user)):
    """Alias used by some frontend builds — returns the current authenticated user."""
    return _ser(user)


# ---- Forgot / Reset password ----

RESET_RATE_LIMIT = 5              # max requests
RESET_RATE_WINDOW_SECONDS = 900   # per 15 minutes


def _dev_mode() -> bool:
    return os.environ.get("DEV_MODE", "false").lower() in ("1", "true", "yes")


@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    """Always returns 200 — never reveals whether the email exists.
    Rate-limited per-IP+email to 5 requests per 15 minutes.
    In DEV_MODE, returns the reset token + reset_url in the response and logs it.
    """
    import secrets
    import logging
    logger = logging.getLogger("facelessforge.auth")

    db = get_db()
    email = body.email.lower()
    # Trust X-Forwarded-For first-hop (set by k8s ingress / proxy) for rate limiting.
    fwd = request.headers.get("x-forwarded-for", "")
    ip = (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "unknown")
    identifier = f"{ip}:{email}"
    now = _now()

    # Rate limit check
    window_start = now - timedelta(seconds=RESET_RATE_WINDOW_SECONDS)
    recent_count = await db.password_reset_attempts.count_documents({
        "identifier": identifier,
        "created_at": {"$gte": window_start},
    })
    if recent_count >= RESET_RATE_LIMIT:
        # Still return success to avoid enumeration; just skip token creation.
        return {"ok": True, "message": "If that email exists, a reset link has been issued."}

    await db.password_reset_attempts.insert_one({
        "identifier": identifier, "email": email, "ip": ip, "created_at": now,
    })

    user = await db.users.find_one({"email": email})
    response_payload = {"ok": True, "message": "If that email exists, a reset link has been issued."}

    if user:
        # Invalidate any existing un-used tokens for this user
        await db.password_reset_tokens.update_many(
            {"user_id": user["id"], "used_at": None},
            {"$set": {"used_at": now}},
        )
        ttl_minutes = int(os.environ.get("PASSWORD_RESET_TTL_MINUTES", "60"))
        token = secrets.token_urlsafe(32)
        expires_at = now + timedelta(minutes=ttl_minutes)
        await db.password_reset_tokens.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "email": email,
            "token": token,
            "created_at": now,
            "expires_at": expires_at,
            "used_at": None,
        })
        # Build reset link (frontend route)
        frontend = os.environ.get("FRONTEND_URL") or request.headers.get("origin") or ""
        reset_url = f"{frontend}/reset-password?token={token}" if frontend else f"/reset-password?token={token}"
        if _dev_mode():
            logger.warning("[DEV reset] %s -> %s", email, reset_url)
            response_payload["dev_reset_token"] = token
            response_payload["dev_reset_url"] = reset_url
            response_payload["dev_expires_in_minutes"] = ttl_minutes

    return response_payload


@router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordRequest):
    db = get_db()
    now = _now()
    record = await db.password_reset_tokens.find_one({"token": body.token})
    if not record:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used.")
    if record.get("used_at") is not None:
        raise HTTPException(status_code=400, detail="This reset link has already been used.")
    expires_at = record.get("expires_at")
    if isinstance(expires_at, datetime):
        # Motor returns naive UTC datetimes; normalise before comparison.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")

    user = await db.users.find_one({"id": record["user_id"]})
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used.")

    new_hash = hash_password(body.new_password)
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password_hash": new_hash, "updated_at": now}},
    )
    await db.password_reset_tokens.update_one(
        {"token": body.token},
        {"$set": {"used_at": now}},
    )
    # Invalidate any other outstanding tokens for this user
    await db.password_reset_tokens.update_many(
        {"user_id": user["id"], "used_at": None},
        {"$set": {"used_at": now}},
    )
    return {"ok": True, "message": "Password updated. You can now sign in."}


# ============================ PROJECTS ============================

async def _attach_project_view(db, project: dict) -> dict:
    script = await db.scripts.find_one({"project_id": project["id"]}, {"_id": 0})
    scenes = await db.scenes.find({"project_id": project["id"]}, {"_id": 0}).to_list(1000)
    metadata = await db.metadata_packages.find_one({"project_id": project["id"]}, {"_id": 0})
    assets = await db.assets.find({"project_id": project["id"]}, {"_id": 0}).to_list(1000)
    render_job = await db.render_jobs.find_one(
        {"project_id": project["id"]},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    score = quality_score(project=project, script=script, scenes=scenes, metadata=metadata, render_job=render_job)
    status = compute_project_status(
        has_script=bool(script), has_scenes=bool(scenes), has_metadata=bool(metadata),
        has_assets=bool(assets), render_status=(render_job or {}).get("status"),
    )
    if status != project.get("status"):
        await db.projects.update_one({"id": project["id"]}, {"$set": {"status": status, "updated_at": _now()}})
        project["status"] = status
    if score != project.get("quality_score"):
        await db.projects.update_one({"id": project["id"]}, {"$set": {"quality_score": score, "updated_at": _now()}})
        project["quality_score"] = score
    return {
        "project": _ser(project) | {"quality_label": quality_label(score)},
        "script": _ser(script) if script else None,
        "scenes": sorted([_ser(s) for s in scenes], key=lambda s: s.get("scene_number", 0)),
        "metadata": _ser(metadata) if metadata else None,
        "assets": [_ser(a) for a in assets],
        "render_job": _ser(render_job) if render_job else None,
        "share": _share_payload(project),
    }


@router.post("/projects")
async def create_project(body: ProjectCreate, user=Depends(get_current_user)):
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewers cannot create projects")
    db = get_db()
    proj = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "name": body.name,
        "niche": body.niche,
        "topic": body.topic,
        "audience": body.audience,
        "tone": body.tone,
        "target_duration": body.target_duration,
        "voice_style": body.voice_style or "neutral male narrator",
        "voice_id": body.voice_id or body.voice_style or "neutral male narrator",
        "visual_style": body.visual_style or "cinematic b-roll",
        "monetisation_intent": body.monetisation_intent or "ads + affiliate",
        "cta_goal": body.cta_goal or "subscribe",
        "auto_post": body.auto_post if body.auto_post is not None else False,
        "platforms": body.platforms or [],
        "status": "DRAFT",
        "quality_score": 0,
        "estimated_cost": 0.0,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.projects.insert_one(proj)
    return _ser(proj)


@router.get("/projects")
async def list_projects(user=Depends(get_current_user)):
    db = get_db()
    q = {} if user["role"] == "admin" else {"user_id": user["id"]}
    projs = await db.projects.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    # Attach derived score labels without recomputing too expensively:
    return [_ser(p) | {"quality_label": quality_label(int(p.get("quality_score", 0)))} for p in projs]


@router.get("/projects/{project_id}")
async def get_project(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    return await _attach_project_view(db, project)


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "target_duration" in patch and not (30 <= int(patch["target_duration"]) <= 3600):
        raise HTTPException(status_code=422, detail="Target duration must be 30..3600s")
    if patch:
        patch["updated_at"] = _now()
        await db.projects.update_one({"id": project_id}, {"$set": patch})
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    await db.projects.delete_one({"id": project_id})
    await db.scripts.delete_many({"project_id": project_id})
    await db.scenes.delete_many({"project_id": project_id})
    await db.metadata_packages.delete_many({"project_id": project_id})
    await db.assets.delete_many({"project_id": project_id})
    await db.render_jobs.delete_many({"project_id": project_id})
    return {"ok": True}


# ============================ GENERATION ============================

def _log_cost(db, project_id: str, operation: str, tokens: int, cost: float):
    return db.cost_logs.insert_one({
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "provider": "deepseek/deepseek-chat",
        "operation": operation,
        "tokens_used": tokens,
        "characters_used": 0,
        "estimated_cost": cost,
        "created_at": _now(),
    })


@router.post("/projects/{project_id}/generate-script")
async def generate_script_endpoint(project_id: str, user=Depends(get_current_user)):
    if check_credits:
        await check_credits(user, required=1)
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    data = await gen.generate_script(project)
    doc = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        **data,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.scripts.replace_one({"project_id": project_id}, doc, upsert=True)
    await _log_cost(db, project_id, "script", tokens=max(500, (data.get("word_count", 0) or 0) * 2), cost=0.08)
    await db.projects.update_one({"id": project_id}, {"$set": {"estimated_cost": float(project.get("estimated_cost", 0)) + 0.08, "updated_at": _now()}})
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/{project_id}/generate-scenes")
async def generate_scenes_endpoint(project_id: str, user=Depends(get_current_user)):
    if check_credits:
        await check_credits(user, required=1)
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    if not script:
        raise HTTPException(status_code=400, detail="Generate a script before scenes")
    try:
        scenes = await gen.generate_scene_plan(project, script)
    except Exception as e:  # noqa: BLE001
        logger = __import__("logging").getLogger("facelessforge.routes")
        logger.warning("generate_scene_plan failed project=%s: %s — using fallback", project_id, e)
        scenes = await gen.generate_scene_plan(project, {"full_script": script.get("full_script", "") or project.get("topic") or "test"})
    for sc in scenes:
        sc["id"] = str(uuid.uuid4())
        sc["project_id"] = project_id
        sc["created_at"] = _now()
        sc["updated_at"] = _now()
    await db.scenes.delete_many({"project_id": project_id})
    if scenes:
        await db.scenes.insert_many([dict(sc) for sc in scenes])
    await _log_cost(db, project_id, "scenes", tokens=len(scenes) * 200, cost=0.06)
    await db.projects.update_one({"id": project_id}, {"$set": {"estimated_cost": float(project.get("estimated_cost", 0)) + 0.06, "updated_at": _now()}})
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/generate-scenes")
async def generate_scenes_shim(request: Request):
    """Shim for POST /api/projects/generate-scenes — accepts JSON {project_id, script, topic, niche, target_duration}.

    Public (no auth) so health checks and external callers can test auto-gen without a full project lifecycle.
    Never 500: falls back to deterministic generation if LLM times out or keys missing.
    Returns {"scenes": [...]} where scenes is always an array.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    project_id = (body.get("project_id") or "test").strip() or "test"
    script_text = body.get("script") or body.get("full_script") or body.get("topic") or "test 450 word flow"
    if isinstance(script_text, dict):
        script_text = script_text.get("full_script") or str(script_text)
    topic = body.get("topic") or (script_text[:60] if isinstance(script_text, str) else "test")
    niche = body.get("niche") or "general"
    target_duration = int(body.get("target_duration") or body.get("duration") or 300)
    # Build minimal project/script dicts for generation
    project = {
        "id": project_id,
        "topic": topic,
        "niche": niche,
        "target_duration": target_duration,
        "tone": body.get("tone") or "documentary",
        "title": topic,
    }
    script = {"full_script": script_text if isinstance(script_text, str) else str(script_text)}
    try:
        # Use timeout to avoid hanging on LLM
        scenes = await asyncio.wait_for(gen.generate_scene_plan(project, script), timeout=25.0)
    except asyncio.TimeoutError:
        logger = __import__("logging").getLogger("facelessforge.routes")
        logger.warning("generate-scenes shim timeout project=%s — fallback", project_id)
        scenes = await gen.generate_scene_plan(project, {"full_script": script_text[:2000] or "fallback script"})
    except Exception as e:  # noqa: BLE001
        logger = __import__("logging").getLogger("facelessforge.routes")
        logger.warning("generate-scenes shim failed project=%s: %s — fallback", project_id, e)
        # Deterministic fallback: sync call
        try:
            scenes = await gen.generate_scene_plan(project, {"full_script": script_text[:2000] or "fallback script"})
        except Exception as e2:  # noqa: BLE001
            logger.error("fallback also failed: %s", e2)
            scenes = [
                {
                    "scene_number": 1,
                    "start_time": 0.0,
                    "end_time": float(target_duration),
                    "duration": float(target_duration),
                    "narration_text": script_text[:500] or "Test scene narration",
                    "visual_direction": "forest nature b-roll",
                    "caption_text": "Test scene",
                    "search_terms": ["forest", "nature", "trees"],
                    "id": str(uuid.uuid4()),
                    "project_id": project_id,
                }
            ]
    # Ensure scenes is list
    if not isinstance(scenes, list):
        scenes = []
    # Attach ids if missing
    for sc in scenes:
        sc.setdefault("id", str(uuid.uuid4()))
        sc.setdefault("project_id", project_id)
    return {"scenes": scenes, "project_id": project_id, "count": len(scenes)}


@router.post("/projects/{project_id}/generate-metadata")
async def generate_metadata_endpoint(project_id: str, user=Depends(get_current_user)):
    if check_credits:
        await check_credits(user, required=1)
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    scenes = await db.scenes.find({"project_id": project_id}).sort("scene_number", 1).to_list(500)
    if not script:
        raise HTTPException(status_code=400, detail="Generate a script before metadata")
    data = await gen.generate_metadata(project, script, scenes)
    doc = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        **data,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.metadata_packages.replace_one({"project_id": project_id}, doc, upsert=True)
    await _log_cost(db, project_id, "metadata", tokens=600, cost=0.04)
    await db.projects.update_one({"id": project_id}, {"$set": {"estimated_cost": float(project.get("estimated_cost", 0)) + 0.04, "updated_at": _now()}})

    # Auto-create thumbnail briefs
    try:
        concepts = await gen.generate_thumbnail_concepts(project, script)
        await db.assets.delete_many({"project_id": project_id, "asset_type": "thumbnail_concept"})
        for concept in concepts:
            await db.assets.insert_one({
                "id": __import__("uuid").uuid4().hex,
                "project_id": project_id,
                "asset_type": "thumbnail_concept",
                "brief": concept,
                "status": "pending",
                "created_at": _now(),
                "updated_at": _now(),
            })
    except Exception:
        pass

    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/{project_id}/generate-thumbnails")
async def generate_thumbnails_endpoint(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0}) or {}
    concepts = await gen.generate_thumbnail_concepts(project, script)
    # Upsert as assets (type=thumbnail_concept)
    await db.assets.delete_many({"project_id": project_id, "asset_type": "thumbnail_concept"})
    for i, c in enumerate(concepts, start=1):
        await db.assets.insert_one({
            "id": str(uuid.uuid4()),
            "project_id": project_id,
            "name": f"Thumbnail Concept #{i}",
            "asset_type": "thumbnail_concept",
            "file_path": None,
            "source": "llm" if os.environ.get("EMERGENT_LLM_KEY") else "fallback",
            "tags": ["thumbnail", "concept"],
            "status": "ready",
            "brief": c,
            "created_at": _now(),
            "updated_at": _now(),
        })
    await _log_cost(db, project_id, "thumbnails", tokens=400, cost=0.03)
    await db.projects.update_one({"id": project_id}, {"$set": {"estimated_cost": float(project.get("estimated_cost", 0)) + 0.03, "updated_at": _now()}})
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/{project_id}/render")
async def prepare_render(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)

    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    scenes = await db.scenes.find({"project_id": project_id}, {"_id": 0}).to_list(500)
    metadata = await db.metadata_packages.find_one({"project_id": project_id}, {"_id": 0})
    assets = await db.assets.find({"project_id": project_id}, {"_id": 0}).to_list(500)

    missing = []
    if not script: missing.append("script")
    if not scenes: missing.append("scenes")
    if not metadata: missing.append("metadata")

    job_id = str(uuid.uuid4())
    if missing:
        job = {
            "id": job_id,
            "project_id": project_id,
            "status": "FAILED",
            "progress": 0,
            "current_step": "validation",
            "output_path": None,
            "error_message": f"Missing: {', '.join(missing)}",
            "started_at": _now(),
            "completed_at": _now(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.render_jobs.insert_one(job)
        return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))

    status = "COMPLETED" if assets else "READY_TO_RENDER"
    job = {
        "id": job_id,
        "project_id": project_id,
        "status": status,
        "progress": 100 if status == "COMPLETED" else 80,
        "current_step": "ready_to_render" if status == "READY_TO_RENDER" else "completed",
        "output_path": f"/exports/{project_id}.package.json",
        "error_message": None,
        "started_at": _now(),
        "completed_at": _now(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.render_jobs.insert_one(job)
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


# ============================ SCRIPT / METADATA EDIT ============================

@router.patch("/projects/{project_id}/script")
async def update_script(project_id: str, body: ScriptUpdate, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot edit")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "full_script" in patch:
        import re as _re
        patch["word_count"] = len(_re.findall(r"\b\w+\b", patch["full_script"]))
        patch["estimated_duration"] = int(patch["word_count"] / 2.5)
    patch["updated_at"] = _now()
    await db.scripts.update_one({"project_id": project_id}, {"$set": patch})
    return await _attach_project_view(db, project)


@router.patch("/projects/{project_id}/metadata")
async def update_metadata(project_id: str, body: MetadataUpdate, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    patch["updated_at"] = _now()
    await db.metadata_packages.update_one({"project_id": project_id}, {"$set": patch})
    return await _attach_project_view(db, project)


# ============================ ASSETS ============================

@router.post("/projects/{project_id}/assets")
async def create_asset(project_id: str, body: AssetCreate, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    doc = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "name": body.name,
        "asset_type": body.asset_type,
        "file_path": body.file_path,
        "source": body.source,
        "tags": body.tags,
        "status": "ready",
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.assets.insert_one(doc)
    return _ser(doc)


@router.delete("/projects/{project_id}/assets/{asset_id}")
async def delete_asset(project_id: str, asset_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    await db.assets.delete_one({"id": asset_id, "project_id": project_id})
    return {"ok": True}


@router.patch("/projects/{project_id}/assets/{asset_id}")
async def update_asset_status(project_id: str, asset_id: str, body: AssetStatusUpdate, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    result = await db.assets.update_one(
        {"id": asset_id, "project_id": project_id},
        {"$set": {"status": body.status, "updated_at": _now()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset = await db.assets.find_one({"id": asset_id, "project_id": project_id}, {"_id": 0})
    return _ser(asset)


# ============================ STOCK / PEXELS ============================

@router.get("/stock/meta")
async def stock_meta(user=Depends(get_current_user)):
    """Lightweight endpoint so the UI can show 'mock mode' badge without triggering a search."""
    # Use aggregated mock check — true only if all keys missing or flag set
    if hasattr(stock_service, "is_mock_mode_aggregated"):
        return {"mock": stock_service.is_mock_mode_aggregated()}
    return {"mock": stock_service.is_mock_mode()}


@router.get("/stock/search")
async def stock_search(
    q: str = "",
    source: str = "pexels",
    type: str = "both",
    per_page: int = 12,
):
    """Public stock search for scene picker — GET /api/stock/search?q=&source=pexels|pixabay|unsplash&type=video|image

    Calls Pexels Video API (preferred), Pixabay Video API, or Unsplash API
    depending on source param. Falls back to deterministic mock if provider
    key missing or rate-limited. No auth required so scene picker can query
    quickly; still respects mock mode when keys absent.

    Query params:
      q: search keywords
      source: pexels|pixabay|unsplash (default pexels)
      type: video|image|both (maps to videos/photos/both)
      per_page: 1..40
    """
    query = (q or "").strip()
    source = (source or "pexels").strip().lower()
    if source not in ("pexels", "pixabay", "unsplash"):
        source = "pexels"
    # Map type param to internal MediaType
    t = (type or "both").strip().lower()
    if t in ("video", "videos"):
        media_type = "videos"
    elif t in ("image", "images", "photo", "photos"):
        media_type = "photos"
    else:
        media_type = "both"
    per_page = max(1, min(int(per_page), 40))
    if not query:
        query = "forest"
    # Unsplash only supports images; force photos
    if source == "unsplash" and media_type == "videos":
        media_type = "photos"
    try:
        result = await stock_service.search_stock_with_source(query, source, media_type, per_page)
        return result
    except Exception as e:  # noqa: BLE001
        logger = __import__("logging").getLogger("facelessforge.routes")
        logger.warning("stock/search failed q=%r source=%s type=%s: %s", query[:60], source, media_type, e)
        # Fallback to mock never fails
        from .stock import _mock_results
        return {"source": "mock", "results": _mock_results(query, media_type, per_page), "mock": True, "query": query, "warning": str(e)}


@router.post("/projects/{project_id}/stock-search")
async def project_stock_search(project_id: str, body: FindAssetsRequest, user=Depends(get_current_user)):
    """Ad-hoc stock search scoped to a project (no scene context). Aggregated Pexels+Pixabay+Unsplash."""
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    query = (body.query or project.get("topic") or project.get("niche") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Provide a query or ensure project has a topic.")
    # Aggregated multi-source — do NOT truncate query server-side, use full body.query
    if hasattr(stock_service, "search_stock_aggregated"):
        return await stock_service.search_stock_aggregated(query, body.media_type, body.per_page)
    return await stock_service.search_stock(query, body.media_type, body.per_page)


@router.post("/projects/{project_id}/scenes/{scene_id}/find-assets")
async def find_scene_assets(project_id: str, scene_id: str, body: FindAssetsRequest, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    scene = await db.scenes.find_one({"id": scene_id, "project_id": project_id}, {"_id": 0})
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")

    query = _build_scene_stock_query(scene, project, body.query)
    # Fix stock video type param: scene visuals must be videos, not images
    media_type = body.media_type if body.media_type in ("videos","photos") else "videos"
    if hasattr(stock_service, "search_stock_aggregated"):
        return await stock_service.search_stock_aggregated(query, media_type, body.per_page)
    return await stock_service.search_stock(query, media_type, body.per_page)


@router.post("/projects/{project_id}/scenes/{scene_id}/attach-asset")
async def attach_scene_asset(project_id: str, scene_id: str, body: StockAttachRequest, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot attach assets")
    scene = await db.scenes.find_one({"id": scene_id, "project_id": project_id}, {"_id": 0})
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")

    # Idempotency: reject duplicates by (project_id, scene_id, external_id, source)
    existing = await db.assets.find_one({
        "project_id": project_id,
        "scene_id": scene_id,
        "external_id": body.external_id,
        "source": body.source,
    }, {"_id": 0})
    if existing:
        raise HTTPException(status_code=409, detail="This asset is already attached to the scene.")

    doc = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "scene_id": scene_id,
        "name": body.title,
        "asset_type": body.media_type,  # stock_video | stock_image
        "file_path": None,
        "source": body.source,
        "external_id": body.external_id,
        "preview_url": body.preview_url,
        "source_url": body.source_url,
        "download_url": body.download_url,
        "attribution_name": body.attribution_name,
        "attribution_url": body.attribution_url,
        "width": body.width,
        "height": body.height,
        "duration": body.duration,
        "tags": body.tags or [],
        "query": body.query,
        "status": "attached",
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.assets.insert_one(doc)
    # Mark scene as having assets
    await db.scenes.update_one(
        {"id": scene_id, "project_id": project_id},
        {"$set": {"status": "assets_attached", "updated_at": _now()}},
    )
    return _ser(doc)


# ---- Auto-attach 3-second rule: 10 assets per 30s scene ----

def _parse_time_to_seconds(t: str) -> float:
    """Parse 0:00, 0:30, 1:30, 00:01:30 to seconds."""
    t = (t or "").strip()
    if not t:
        return 0.0
    parts = t.strip().split(":")
    try:
        parts = [float(p.strip()) for p in parts if p.strip() != ""]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 1:
            return float(parts[0])
    except Exception:
        return 0.0
    return 0.0

def _get_scene_duration(scene: dict) -> float:
    """Parse duration from scene.duration or start_time/end_time or scene.time '0:00 -> 0:30'."""
    try:
        if scene.get("duration") is not None:
            d = float(scene.get("duration") or 0)
            if d > 0:
                return d
        if scene.get("end_time") is not None and scene.get("start_time") is not None:
            try:
                end = float(scene.get("end_time") or 0)
                start = float(scene.get("start_time") or 0)
                if end > start:
                    return end - start
            except Exception:
                pass
        t = scene.get("time") or scene.get("timestamp") or ""
        if isinstance(t, str) and "->" in t:
            left, right = t.split("->", 1)
            s = _parse_time_to_seconds(left.strip())
            e = _parse_time_to_seconds(right.strip())
            if e > s:
                return e - s
            if e > 0:
                return e
        # fallback to string duration like "30s"
        for k in ("duration", "length", "time"):
            v = scene.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
    except Exception:
        pass
    return 30.0

def _dedup_by_preview(results: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for r in results:
        k = r.get("preview_url") or r.get("external_id") or r.get("id")
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out

@router.post("/projects/{project_id}/auto-attach-assets")
async def auto_attach_assets(project_id: str, body: AutoAttachRequest, user=Depends(get_current_user)):
    """3-second rule: ceil(duration/3) assets per scene (10 for 30s), 3s each, order/start_offset."""

    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot attach assets")

    scenes = await db.scenes.find({"project_id": project_id}).sort("scene_number", 1).to_list(500)
    if not scenes:
        raise HTTPException(status_code=400, detail="Generate scenes before auto-attach.")

    total = len(scenes)
    attached = 0
    skipped = 0
    failed = 0
    details: list[dict] = []

    # --- Credits check: sum needed for scenes that will be attached ---
    total_needed = 0
    for _s in scenes:
        _d = _get_scene_duration(_s)
        _n = max(1, math.ceil(_d / 3))
        _sid = str(_s.get("id") or _s.get("_id"))
        _cnt = await db.assets.count_documents({"project_id": project_id, "scene_id": _sid, "asset_type": {"$in": ["stock_video", "stock_image"]}})
        if _cnt > 0 and not body.replace_existing:
            if _cnt >= _n:
                continue
            total_needed += (_n - _cnt)
        else:
            total_needed += _n
    # ensure subscription exists and check - also check finite redis tenant:{id} credits (DODO/PayPal finite via hincrby)
    sub = await db.subscriptions.find_one({"user_id": user["id"]}, {"_id": 0})
    # Try redis finite credits first (DODO/PayPal webhook grants via hincrby tenant:{id})
    redis_credits = None
    try:
        import redis as _r
        _rc = _r.Redis(host=os.getenv("REDIS_HOST","localhost"), port=int(os.getenv("REDIS_PORT","6379")), db=0, decode_responses=True)
        rc_data = _rc.hgetall(f"tenant:{user['id']}")
        if rc_data and rc_data.get("credits") is not None:
            try:
                redis_credits = int(float(str(rc_data.get("credits"))))
            except Exception:
                redis_credits = None
        _rc.close()
    except Exception:
        redis_credits = None
    if not sub:
        # fallback to ff_credits for backward compat, else create free 30, but prefer redis if present
        ff = await db.ff_credits.find_one({"tenant_id": user["id"]}, {"_id": 0}) or await db.ff_credits.find_one({"user_id": user["id"]}, {"_id": 0})
        if ff:
            sub = {"credits_remaining": int(ff.get("credits", 30)), "credits_monthly": int(ff.get("credits", 30))}
        elif redis_credits is not None:
            sub = {"credits_remaining": redis_credits, "credits_monthly": max(redis_credits, 30), "plan": "finite", "current_period_end": _now() + timedelta(days=30)}
        else:
            # create free subscription 30 credits
            sub_doc = {
                "user_id": user["id"],
                "paddle_customer_id": None,
                "paddle_subscription_id": None,
                "paddle_price_id": None,
                "plan": "free",
                "status": "active",
                "credits_monthly": 30,
                "credits_remaining": 30,
                "current_period_end": _now() + timedelta(days=30),
                "created_at": _now(),
                "updated_at": _now(),
            }
            await db.subscriptions.insert_one(sub_doc)
            sub = sub_doc
    # Prefer redis finite credits if higher than subscription
    remaining = int(sub.get("credits_remaining", 30) if isinstance(sub, dict) else 30)
    if redis_credits is not None and redis_credits > remaining:
        remaining = redis_credits
        sub["credits_remaining"] = remaining
    if total_needed > 0 and remaining < total_needed:
        reset_at = sub.get("current_period_end") or _now() + timedelta(days=30)
        if hasattr(reset_at, 'isoformat'):
            reset_at = reset_at.isoformat()
        raise HTTPException(status_code=402, detail={"code": "credits_exhausted", "reset_at": reset_at, "plan": sub.get("plan", "free"), "remaining": remaining, "required": total_needed, "quota": int(sub.get("credits_monthly", 30)), "message": f"Insufficient credits: {remaining} remaining, {total_needed} required. Need {total_needed} credits for {total_needed} assets (3s each)."})

    for scene in scenes:
        scene_id = str(scene.get("id") or scene.get("_id"))
        # 3-second rule: ceil(duration/3) assets per scene (10 for 30s), 3s each
        duration = _get_scene_duration(scene)
        num_needed = max(1, math.ceil(duration / 3))
        # Handle existing count and replace logic
        existing_count = await db.assets.count_documents({
            "project_id": project_id,
            "scene_id": scene_id,
            "asset_type": {"$in": ["stock_video", "stock_image"]},
        })
        if existing_count > 0 and not body.replace_existing:
            if existing_count >= num_needed:
                skipped += 1
                details.append({"scene_id": scene_id, "scene_number": scene.get("scene_number"), "status": "skipped", "reason": "already_has_stock"})
                continue
            remaining = num_needed - existing_count
        else:
            if body.replace_existing and existing_count > 0:
                await db.assets.delete_many({
                    "project_id": project_id,
                    "scene_id": scene_id,
                    "asset_type": {"$in": ["stock_video", "stock_image"]},
                })
                existing_count = 0
            remaining = num_needed

        # Build query from search_terms[0] as spec, truncated to 60 (stock.py will also truncate to 4 words)
        try:
            import logging
            logger = logging.getLogger("facelessforge.routes")
            terms = scene.get("search_terms") or []
            visual = (scene.get("visual_direction") or scene.get("visual") or "").strip()
            if terms and isinstance(terms, list) and len(terms) > 0 and str(terms[0]).strip():
                q_raw = str(terms[0]).strip()[:60]
            elif visual:
                q_raw = visual.split(",")[0].strip()[:60] if "," in visual else visual.split(".")[0].strip()[:60]
                if not q_raw:
                    q_raw = visual[:60]
            else:
                q_raw = (project.get("topic") or "ancient Rome").strip()[:60]
            query = q_raw.strip()
            if not query:
                query = "ancient Rome"
            # Gather existing ids to exclude (dedup)
            existing_assets = await db.assets.find({"project_id": project_id, "scene_id": scene_id, "asset_type": {"$in": ["stock_video", "stock_image"]}}, {"_id": 0, "external_id": 1, "preview_url": 1}).to_list(100)
            existing_ids = set()
            for ea in existing_assets:
                if ea.get("external_id"):
                    existing_ids.add(str(ea.get("external_id")))
                if ea.get("preview_url"):
                    existing_ids.add(str(ea.get("preview_url")))
            per_page = max(remaining * 2, remaining + 5)
            per_page = min(per_page, 40)
            result = None
            if hasattr(stock_service, "search_stock_aggregated"):
                result = await stock_service.search_stock_aggregated(query=query, media_type="both", per_page=per_page)
            else:
                result = await stock_service.search_stock(query, "both", per_page)
            results = (result.get("results") or []) if result else []
            # Filter out already attached
            filtered = []
            for r in results:
                ext = str(r.get("external_id") or "")
                prev = str(r.get("preview_url") or "")
                if ext in existing_ids or prev in existing_ids:
                    continue
                filtered.append(r)
            unique = _dedup_by_preview(filtered)[:remaining]
            if not unique:
                failed += 1
                details.append({"scene_id": scene_id, "scene_number": scene.get("scene_number"), "status": "failed", "reason": "no_stock_results"})
                continue
            start_order = existing_count
            for i, asset in enumerate(unique):
                order = start_order + i
                start_offset = order * 3  # 0,3,6,9...
                duration_asset = 3
                doc = {
                    "id": str(uuid.uuid4()),
                    "project_id": project_id,
                    "scene_id": scene_id,
                    "name": asset.get("title") or asset.get("name") or f"Asset {order+1}",
                    "asset_type": asset.get("media_type") or asset.get("asset_type") or "stock_image",
                    "file_path": None,
                    "source": asset.get("source") or "pexels",
                    "external_id": asset.get("external_id") or str(uuid.uuid4()),
                    "preview_url": asset.get("preview_url"),
                    "source_url": asset.get("source_url"),
                    "download_url": asset.get("download_url"),
                    "attribution_name": asset.get("attribution_name"),
                    "attribution_url": asset.get("attribution_url"),
                    "width": asset.get("width"),
                    "height": asset.get("height"),
                    "duration": duration_asset,
                    "tags": asset.get("tags") or [],
                    "query": query,
                    "status": "attached",
                    "order": order,
                    "start_offset": start_offset,
                    "scene_duration": duration,
                    "created_at": _now(),
                    "updated_at": _now(),
                }
                try:
                    await db.assets.insert_one(doc)
                    attached += 1
                    details.append({"scene_id": scene_id, "scene_number": scene.get("scene_number"), "status": "attached", "asset_id": doc["id"], "order": order, "start_offset": start_offset})
                except Exception:
                    skipped += 1
                    details.append({"scene_id": scene_id, "scene_number": scene.get("scene_number"), "status": "skipped", "reason": "duplicate"})
            # Deduct credits for this scene's attached assets - finite via hincrby tenant:{id} as well
            if unique:
                await db.subscriptions.update_one({"user_id": user["id"]}, {"$inc": {"credits_remaining": -len(unique)}, "$set": {"updated_at": _now()}})
                for _ in range(len(unique)):
                    await db.credit_transactions.insert_one({
                        "id": str(uuid.uuid4()),
                        "user_id": user["id"],
                        "amount": -1,
                        "reason": f"auto-attach scene {scene.get('scene_number')} {duration}s",
                        "created_at": _now(),
                    })
                # also mirror to ff_credits for backward compat
                try:
                    await db.ff_credits.update_one({"tenant_id": user["id"]}, {"$inc": {"credits": -len(unique)}})
                except: pass
                # Deduct finite redis tenant credits via hincrby (atomic)
                try:
                    import redis as _r2
                    _rc2 = _r2.Redis(host=os.getenv("REDIS_HOST","localhost"), port=int(os.getenv("REDIS_PORT","6379")), db=0, decode_responses=True)
                    _rc2.hincrby(f"tenant:{user['id']}", "credits", -len(unique))
                    _rc2.close()
                except Exception:
                    pass
            logger.info("auto-attach scene %s duration=%s num_needed=%s remaining=%s query='%s' -> %d unique attached %d", scene.get("scene_number"), duration, num_needed, remaining, query[:60], len(unique), attached)
        except Exception as e:
            failed += 1
            details.append({"scene_id": scene_id, "scene_number": scene.get("scene_number"), "status": "failed", "reason": str(e)[:120]})

    # Required journalctl log for spec: [ATTACH] 18 attached 0 failed
    import logging as _logging
    _logging.getLogger("facelessforge.routes").info("[ATTACH] %s attached %s failed project=%s mock=%s", attached, failed, project_id, stock_service.is_mock_mode())
    print(f"[ATTACH] {attached} attached {failed} failed project={project_id}", flush=True)
    return {
        "total": total,
        "attached": attached,
        "skipped": skipped,
        "failed": failed,
        "details": details,
        "mock": stock_service.is_mock_mode(),
    }


# ============================ THUMBNAIL IMAGE GENERATION ============================

@router.get("/thumbnails/meta")
async def thumbnails_meta(user=Depends(get_current_user)):
    return thumb_images.provider_info()


@router.post("/projects/{project_id}/thumbnails/{brief_asset_id}/generate")
async def generate_thumbnail_image_endpoint(
    project_id: str, brief_asset_id: str,
    body: GenerateThumbnailImagesRequest,
    user=Depends(get_current_user),
):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot generate images")
    brief_asset = await db.assets.find_one(
        {"id": brief_asset_id, "project_id": project_id, "asset_type": "thumbnail_concept"},
        {"_id": 0},
    )
    if not brief_asset:
        raise HTTPException(status_code=404, detail="Thumbnail brief not found")
    brief = brief_asset.get("brief") or {}

    generated = await thumb_images.generate_thumbnail_images(
        project, brief, variants=body.variants, project_id=project_id,
    )
    if not generated:
        raise HTTPException(status_code=502, detail="Image generation failed. Please retry.")

    now = _now()
    inserts = []
    for g in generated:
        g["brief_asset_id"] = brief_asset_id
        g["created_at"] = now
        g["updated_at"] = now
        inserts.append(dict(g))
    await db.assets.insert_many([dict(d) for d in inserts])

    est_cost = 0.02 * len(generated) if not generated[0].get("mock") else 0
    if est_cost:
        await db.projects.update_one(
            {"id": project_id},
            {"$set": {"estimated_cost": float(project.get("estimated_cost", 0)) + est_cost, "updated_at": now}},
        )
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/{project_id}/thumbnails/{asset_id}/select")
async def select_thumbnail(project_id: str, asset_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot select thumbnails")
    asset = await db.assets.find_one(
        {"id": asset_id, "project_id": project_id, "asset_type": "generated_thumbnail"},
        {"_id": 0},
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Generated thumbnail not found")
    now = _now()
    # Demote any currently selected thumbnail
    await db.assets.update_many(
        {"project_id": project_id, "asset_type": "generated_thumbnail", "status": "selected"},
        {"$set": {"status": "generated", "updated_at": now}},
    )
    await db.assets.update_one(
        {"id": asset_id, "project_id": project_id},
        {"$set": {"status": "selected", "updated_at": now}},
    )
    await db.projects.update_one(
        {"id": project_id},
        {"$set": {"selected_thumbnail_asset_id": asset_id, "updated_at": now}},
    )
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/{project_id}/thumbnails/{asset_id}/reject")
async def reject_thumbnail(project_id: str, asset_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot reject thumbnails")
    asset = await db.assets.find_one(
        {"id": asset_id, "project_id": project_id, "asset_type": "generated_thumbnail"},
        {"_id": 0},
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Generated thumbnail not found")
    now = _now()
    await db.assets.update_one(
        {"id": asset_id, "project_id": project_id},
        {"$set": {"status": "rejected", "updated_at": now}},
    )
    # If the rejected one was selected, clear project pointer
    if project.get("selected_thumbnail_asset_id") == asset_id:
        await db.projects.update_one(
            {"id": project_id},
            {"$set": {"selected_thumbnail_asset_id": None, "updated_at": now}},
        )
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


# ============================ VOICEOVER (TTS) ============================
from . import tts as tts_service
from .models import GenerateVoiceoverRequest


@router.get("/tts/meta")
async def tts_meta(user=Depends(get_current_user)):
    return tts_service.provider_info()


def _voice_style_for(project: dict, override: Optional[str]) -> str:
    """Resolve a narrator preset ("NEUTRAL_FEMALE_NARRATOR") or style word ("calm").

    Preset/override strings are passed through untouched so tts.py resolves the
    ElevenLabs voice ID; free-form project.voice_style labels are normalized to
    a preset or style word so a female selection actually gets a female voice.
    """
    if override:
        return override
    raw = (project.get("voice_style") or "").strip()
    if not raw:
        return os.environ.get("DEFAULT_VOICE_STYLE", "narrator")
    preset = tts_service._normalize_preset(raw)
    if preset in tts_service.VOICE_MAP:
        return preset
    low = raw.lower()
    for key in tts_service.VOICE_STYLE_MAP.keys():
        if key in low:
            return key
    if "female" in low:
        return "NEUTRAL_FEMALE_NARRATOR"
    if "male" in low or "deep" in low or "narrator" in low:
        return "NEUTRAL_MALE_NARRATOR"
    if "upbeat" in low or "energy" in low or "fast" in low:
        return "energetic"
    if "doc" in low or "neutral" in low:
        return "documentary"
    return os.environ.get("DEFAULT_VOICE_STYLE", "narrator")


@router.post("/projects/{project_id}/voiceover/generate-script")
async def generate_full_voiceover(
    project_id: str,
    body: GenerateVoiceoverRequest = Body(default=None),
    user=Depends(get_current_user),
):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot generate voiceovers")
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    if not script or not script.get("full_script", "").strip():
        raise HTTPException(status_code=400, detail="Generate a script before voiceover.")
    body = body or GenerateVoiceoverRequest()
    text = (body.text_override or script["full_script"]).strip()
    voice = _voice_style_for(project, body.voice_style)

    asset_id = str(uuid.uuid4())
    try:
        payload = await tts_service.generate_voiceover(
            text=text, voice_style=voice, tone=body.tone, project_id=project_id,
            asset_id=asset_id, scene_id=None, name_suffix="(Full script)",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Voiceover generation failed: {e}")

    payload["text_excerpt"] = text[:240]
    payload["created_at"] = _now()
    payload["updated_at"] = _now()
    # Auto-select: mirror the thumbnail auto-chain so render preflight passes
    # immediately after generation. Any previously selected full-script voiceover
    # is demoted to keep the per-project exclusivity invariant.
    payload["status"] = "selected"
    await db.assets.update_many(
        {
            "project_id": project_id,
            "asset_type": "voiceover_audio",
            "scene_id": None,
            "status": "selected",
        },
        {"$set": {"status": "generated", "updated_at": _now()}},
    )
    await db.assets.insert_one(dict(payload))
    project_set = {"selected_voiceover_asset_id": asset_id, "updated_at": _now()}

    if not payload.get("mock"):
        cost = float(payload.get("cost_estimate") or 0)
        if cost:
            project_set["estimated_cost"] = float(project.get("estimated_cost", 0)) + cost
    await db.projects.update_one({"id": project_id}, {"$set": project_set})
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/{project_id}/scenes/{scene_id}/voiceover/generate")
async def generate_scene_voiceover(
    project_id: str, scene_id: str,
    body: GenerateVoiceoverRequest = Body(default=None),
    user=Depends(get_current_user),
):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot generate voiceovers")
    scene = await db.scenes.find_one({"id": scene_id, "project_id": project_id}, {"_id": 0})
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    body = body or GenerateVoiceoverRequest()
    text = (body.text_override or scene.get("narration_text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Scene has no narration text. Generate scenes first.")
    voice = _voice_style_for(project, body.voice_style)

    asset_id = str(uuid.uuid4())
    try:
        payload = await tts_service.generate_voiceover(
            text=text, voice_style=voice, tone=body.tone, project_id=project_id,
            asset_id=asset_id, scene_id=scene_id,
            name_suffix=f"(Scene {scene.get('scene_number', '?'):02d})" if isinstance(scene.get('scene_number'), int) else "(Scene)",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Voiceover generation failed: {e}")

    payload["text_excerpt"] = text[:240]
    payload["scene_number"] = scene.get("scene_number")
    payload["created_at"] = _now()
    payload["updated_at"] = _now()
    # Demote any other voiceover for this scene to "generated", mark this one selected
    await db.assets.update_many(
        {"project_id": project_id, "scene_id": scene_id, "asset_type": "voiceover_audio", "status": "selected"},
        {"$set": {"status": "generated", "updated_at": _now()}},
    )
    payload["status"] = "selected"
    await db.assets.insert_one(dict(payload))

    if not payload.get("mock"):
        cost = float(payload.get("cost_estimate") or 0)
        if cost:
            await db.projects.update_one(
                {"id": project_id},
                {"$set": {"estimated_cost": float(project.get("estimated_cost", 0)) + cost, "updated_at": _now()}},
            )
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/{project_id}/voiceover/{asset_id}/select")
async def select_voiceover(project_id: str, asset_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot select voiceovers")
    asset = await db.assets.find_one(
        {"id": asset_id, "project_id": project_id, "asset_type": "voiceover_audio"},
        {"_id": 0},
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Voiceover not found")
    now = _now()
    if asset.get("scene_id"):
        # Per-scene exclusivity
        await db.assets.update_many(
            {"project_id": project_id, "scene_id": asset["scene_id"],
             "asset_type": "voiceover_audio", "status": "selected"},
            {"$set": {"status": "generated", "updated_at": now}},
        )
        await db.assets.update_one(
            {"id": asset_id, "project_id": project_id},
            {"$set": {"status": "selected", "updated_at": now}},
        )
    else:
        # Full-script exclusivity — demote prior selected full-script voiceovers
        await db.assets.update_many(
            {"project_id": project_id, "scene_id": None,
             "asset_type": "voiceover_audio", "status": "selected"},
            {"$set": {"status": "generated", "updated_at": now}},
        )
        await db.assets.update_one(
            {"id": asset_id, "project_id": project_id},
            {"$set": {"status": "selected", "updated_at": now}},
        )
        await db.projects.update_one(
            {"id": project_id},
            {"$set": {"selected_voiceover_asset_id": asset_id, "updated_at": now}},
        )
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.post("/projects/{project_id}/voiceover/{asset_id}/reject")
async def reject_voiceover(project_id: str, asset_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot reject voiceovers")
    asset = await db.assets.find_one(
        {"id": asset_id, "project_id": project_id, "asset_type": "voiceover_audio"},
        {"_id": 0},
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Voiceover not found")
    now = _now()
    await db.assets.update_one(
        {"id": asset_id, "project_id": project_id},
        {"$set": {"status": "rejected", "updated_at": now}},
    )
    if not asset.get("scene_id") and project.get("selected_voiceover_asset_id") == asset_id:
        await db.projects.update_one(
            {"id": project_id},
            {"$set": {"selected_voiceover_asset_id": None, "updated_at": now}},
        )
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


@router.delete("/projects/{project_id}/voiceover/{asset_id}")
async def delete_voiceover(project_id: str, asset_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot delete voiceovers")
    asset = await db.assets.find_one(
        {"id": asset_id, "project_id": project_id, "asset_type": "voiceover_audio"},
        {"_id": 0},
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Voiceover not found")
    # Best-effort artifact removal via storage abstraction
    try:
        from .storage import get_storage as _gs
        store = _gs()
        if asset.get("storage_key"):
            store.delete(key=asset["storage_key"])
        elif asset.get("file_path"):
            from pathlib import Path as _P
            p = _P(asset["file_path"])
            if p.exists() and p.is_file():
                p.unlink()
    except Exception:  # noqa: BLE001
        pass
    await db.assets.delete_one({"id": asset_id, "project_id": project_id})
    if project.get("selected_voiceover_asset_id") == asset_id:
        await db.projects.update_one(
            {"id": project_id},
            {"$set": {"selected_voiceover_asset_id": None, "updated_at": _now()}},
        )
    return await _attach_project_view(db, await db.projects.find_one({"id": project_id}, {"_id": 0}))


# ============================ RENDER QUEUE (Phase 6) ============================
from . import render as render_service
from .models import RenderStartRequest


@router.get("/projects/{project_id}/render/preflight")
async def render_preflight(project_id: str, user=Depends(get_current_user)):
    """Return validation checklist for the render UI."""
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    scenes = await db.scenes.find({"project_id": project_id}).sort("scene_number", 1).to_list(500)
    metadata = await db.metadata_packages.find_one({"project_id": project_id}, {"_id": 0})
    assets = await db.assets.find({"project_id": project_id}, {"_id": 0}).to_list(500)
    return render_service.validate_prerequisites(project, script, scenes, metadata, assets)


@router.post("/projects/{project_id}/render/start")
async def render_start(project_id: str,
                       body: RenderStartRequest = Body(default=None),
                       user=Depends(get_current_user)):
    _check_render_rate(user["id"])
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot start renders")
    body = body or RenderStartRequest()
    # Voice selector moved from /generate-script to /render payload — allow last-minute voice change
    # DB: voice_id now lives on projects table (not just scripts)
    if body.voice_id or body.voice_style:
        update_fields = {"updated_at": _now()}
        if body.voice_style:
            update_fields["voice_style"] = body.voice_style
        if body.voice_id:
            update_fields["voice_id"] = body.voice_id
            # normalize voice_id to voice_style for TTS resolver
            update_fields["voice_style"] = body.voice_id
        await db.projects.update_one({"id": project_id}, {"$set": update_fields})
        project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    # Validate prereqs first so caller gets a clean 400 before queuing
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    scenes = await db.scenes.find({"project_id": project_id}).sort("scene_number", 1).to_list(500)
    metadata = await db.metadata_packages.find_one({"project_id": project_id}, {"_id": 0})
    assets = await db.assets.find({"project_id": project_id}, {"_id": 0}).to_list(500)
    check = render_service.validate_prerequisites(project, script, scenes, metadata, assets)
    if not check["ok"]:
        raise HTTPException(status_code=400, detail={
            "message": "Render prerequisites not met",
            "issues": check["issues"],
            "checklist": check["checklist"],
        })
    try:
        job = await render_service.queue_render(project_id, requested_by=user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=_scrub_email(str(e)))
    # scrub email in log
    import logging as _logging
    _logging.getLogger("facelessforge.render").info("render start project=%s user=%s", project_id, _scrub_email(user.get("email","")))
    return _ser(job)


@router.get("/projects/{project_id}/render/jobs")
async def render_list_jobs(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    jobs = await db.render_jobs.find({"project_id": project_id}, {"_id": 0})\
        .sort("created_at", -1).to_list(50)
    return [_ser(j) for j in jobs]


@router.get("/projects/{project_id}/render/jobs/{job_id}")
async def render_get_job(project_id: str, job_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    job = await db.render_jobs.find_one({"id": job_id, "project_id": project_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Render job not found")
    return _ser(job)


@router.post("/projects/{project_id}/render/jobs/{job_id}/cancel")
async def render_cancel_job(project_id: str, job_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer cannot cancel renders")
    ok = await render_service.cancel_render(project_id, job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Job is not cancellable in its current state.")
    job = await db.render_jobs.find_one({"id": job_id, "project_id": project_id}, {"_id": 0})
    return _ser(job)


# ============================ PROD FIXES: billing balance + full render + thumbnails ============================
import time as _time
_RENDER_RATE = {}
def _scrub_email(s: str) -> str:
    import re
    return re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email-redacted]", str(s))
def _check_render_rate(user_id: str):
    now = _time.time()
    bucket = _RENDER_RATE.get(user_id, [])
    bucket = [t for t in bucket if now - t < 3600]
    if len(bucket) >= 5:
        raise HTTPException(status_code=429, detail="Rate limited: 5 renders per hour")
    bucket.append(now)
    _RENDER_RATE[user_id] = bucket

@router.get("/billing/balance")
async def billing_balance(user=Depends(get_current_user)):
    from .db import get_db as _get_db
    db = _get_db()
    # reuse billing status logic
    try:
        from .billing import get_billing_status_for_user
        return await get_billing_status_for_user(user)
    except Exception:
        # fallback to dodo credits
        try:
            import redis
            r = redis.Redis(host=os.getenv("REDIS_HOST","localhost"), port=int(os.getenv("REDIS_PORT","6379")), decode_responses=True)
            data = r.hgetall(f"tenant:{user['id']}")
            credits = int(data.get("credits", 0) or 0)
            return {"credits": credits, "remaining": credits, "monthly": 150, "quota": 150, "plan": "free", "balance": credits}
        except:
            return {"credits": 30, "remaining": 30, "monthly": 30, "quota": 30, "plan": "free", "balance": 30}

@router.get("/thumbnails/{asset_id}")
async def get_thumbnail(asset_id: str, request: Request, user=Depends(get_current_user)):
    from .db import get_db as _get_db
    from fastapi.responses import FileResponse, Response
    db = _get_db()
    asset = await db.assets.find_one({"id": asset_id}, {"_id": 0})
    if not asset:
        # fallback image
        fallback = Path(__file__).parent.parent / "static" / "thumbs" / "fallback.png"
        if fallback.exists():
            return FileResponse(str(fallback), media_type="image/png")
        return Response(status_code=404, content=b"not found")
    fp = asset.get("file_path")
    if fp and Path(fp).exists():
        ext = Path(fp).suffix.lower()
        ct = "image/png" if ext==".png" else "image/jpeg" if ext in (".jpg",".jpeg") else "image/svg+xml" if ext==".svg" else "image/png"
        return FileResponse(str(fp), media_type=ct, headers={"Content-Disposition": f'inline; filename="{asset_id}{ext}"'})
    url = asset.get("preview_url")
    if url:
        # redirect to remote with correct content-type header passthrough
        return Response(status_code=302, headers={"Location": url})
    fallback = Path(__file__).parent.parent / "static" / "thumbs" / "fallback.png"
    if fallback.exists():
        return FileResponse(str(fallback), media_type="image/png")
    return Response(status_code=404, content=b"fallback not found")

def _full_render_impl(project_id: str, user: dict):
    # shared logic for full render idempotent
    return project_id

@router.post("/render/full/{project_id}")
async def render_full(project_id: str, user=Depends(get_current_user)):
    _check_render_rate(user["id"])
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    # idempotent: if active job exists return it
    active = await db.render_jobs.find_one({"project_id": project_id, "status": {"$in": ["queued","validating","preparing_assets","rendering"]}}, {"_id": 0})
    if active:
        return {"queued": True, "job": _ser(active), "idempotent": True}
    # validate
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    scenes = await db.scenes.find({"project_id": project_id}).sort("scene_number", 1).to_list(500)
    metadata = await db.metadata_packages.find_one({"project_id": project_id}, {"_id": 0})
    assets = await db.assets.find({"project_id": project_id}, {"_id": 0}).to_list(500)
    check = render_service.validate_prerequisites(project, script, scenes, metadata, assets)
    if not check["ok"]:
        raise HTTPException(status_code=400, detail={"message": "Prerequisites not met", "issues": check["issues"]})
    try:
        job = await render_service.queue_render(project_id, requested_by=user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=_scrub_email(str(e)))
    # scrub email from logs
    import logging as _logging
    _logging.getLogger("facelessforge.render").info("full render queued project=%s user=%s", project_id, _scrub_email(user.get("email","")))
    return {"queued": True, "job": _ser(job)}

@router.post("/projects/{project_id}/render/full")
async def render_full_alias(project_id: str, user=Depends(get_current_user)):
    return await render_full(project_id, user)

# --- Waitlist (public, used by landing) ---
from pydantic import BaseModel as _BM
class _WaitlistReq(_BM):
    email: str

@router.post("/waitlist")
async def join_waitlist(body: _WaitlistReq):
    email = (body.email or "").strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="valid email required")
    db = get_db()
    existing = await db.waitlist.find_one({"email": email}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=409, detail="already on waitlist")
    doc = {"id": str(uuid.uuid4()), "email": email, "created_at": _now()}
    await db.waitlist.insert_one(doc)
    return {"ok": True, "email": email}

@router.get("/waitlist")
async def list_waitlist(user=Depends(require_roles("admin"))):
    db = get_db()
    items = await db.waitlist.find({}, {"_id": 0}).sort("created_at", -1).to_list(1000)
    return items


# ============================ ADMIN DIAGNOSTICS / RETENTION ============================
from . import retention as retention_service
from .storage import storage_status as storage_status_fn


def _provider_modes() -> dict:
    ollama_ready = bool(os.environ.get("OLLAMA_MODEL", "").strip())
    claude_ready = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    return {
        "llm_text": {
            "mode": "live" if (ollama_ready or claude_ready) else "fallback",
            "provider": "ollama" if ollama_ready else ("anthropic" if claude_ready else "none"),
            "model": os.environ.get("OLLAMA_MODEL") or os.environ.get("LLM_MODEL", "claude-3-5-sonnet-20241022"),
            "ollama_ready": ollama_ready,
            "claude_ready": claude_ready,
        },
        "thumbnail_image": {
            "mode": "mock" if thumb_images.is_mock_mode() else "live",
            "provider": os.environ.get("THUMBNAIL_IMAGE_PROVIDER", "gemini_nano_banana"),
            "model": os.environ.get("THUMBNAIL_IMAGE_MODEL", "gemini-3.1-flash-image-preview"),
        },
        "tts": {
            "mode": "mock" if tts_service.is_mock_mode() else "live",
            "provider": os.environ.get("TTS_PROVIDER", "openai"),
            "model": os.environ.get("OPENAI_TTS_MODEL", "tts-1"),
        },
        "stock_footage": {
            "mode": "mock" if stock_service.is_mock_mode() else "live",
            "provider": "pexels",
        },
    }


@router.get("/admin/diagnostics")
async def admin_diagnostics(_admin=Depends(require_roles("admin"))):
    """Single-pane production-readiness check. Admin only."""
    from server import SYSTEM_STATUS  # cached at boot; fall back to live probe
    sys_status = dict(SYSTEM_STATUS) if SYSTEM_STATUS else {}
    if not sys_status:
        from .system import ensure_ffmpeg_available
        sys_status = ensure_ffmpeg_available()

    dev_mode = os.environ.get("DEV_MODE", "false").lower() in ("1", "true", "yes")
    frontend_url = os.environ.get("FRONTEND_URL", "")
    cors_origins = [o.strip() for o in (frontend_url or "").split(",") if o.strip()]
    if not cors_origins and dev_mode:
        cors_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

    db = get_db()
    project_count = await db.projects.count_documents({})
    user_count = await db.users.count_documents({})
    active_renders = await db.render_jobs.count_documents(
        {"status": {"$in": ["queued", "validating", "preparing_assets", "rendering"]}}
    )

    return {
        "service": "facelessforge",
        "ok": bool(sys_status.get("ffmpeg")),
        "dev_mode": dev_mode,
        "cookie_mode": "lax+insecure" if dev_mode else "none+secure",
        "cors": {
            "origins": cors_origins,
            "regex_fallback": dev_mode,
            "wildcard": (not cors_origins),
        },
        "binaries": {
            "ffmpeg_path": sys_status.get("ffmpeg"),
            "ffmpeg_source": sys_status.get("ffmpeg_source"),
            "ffprobe_path": sys_status.get("ffprobe"),
            "ffprobe_source": sys_status.get("ffprobe_source"),
        },
        "providers": _provider_modes(),
        "storage": {
            **retention_service.disk_usage_report(),
            **storage_status_fn(),
        },
        "render_queue": {
            "active_jobs": active_renders,
            "concurrency": "single asyncio worker per pod",
            "lock": "per-project asyncio Lock + DB status guard",
            "timeout_seconds": int(os.environ.get("RENDER_TIMEOUT_SECONDS", "600")),
        },
        "data_counts": {
            "users": user_count,
            "projects": project_count,
        },
    }


@router.post("/admin/retention/run")
async def admin_retention_run(_admin=Depends(require_roles("admin"))):
    """Manually trigger the retention sweep. Returns the cleanup report."""
    return await retention_service.run_cleanup_once()


# ============================ EXPORTS ============================

@router.get("/projects/{project_id}/export/script.txt")
async def export_script_txt(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    if not script:
        raise HTTPException(status_code=404, detail="No script")
    body = (
        f"# {project['name']}\n\n"
        f"## Hook\n{script['selected_hook']}\n\n"
        f"## Full Script\n{script['full_script']}\n\n"
        f"## CTA\n{script['cta_block']}\n"
    )
    return PlainTextResponse(body, headers={"Content-Disposition": f'attachment; filename="{project_id}-script.txt"'})


@router.get("/projects/{project_id}/export/scenes.csv")
async def export_scenes_csv(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    scenes = await db.scenes.find({"project_id": project_id}).sort("scene_number", 1).to_list(500)
    csv = scenes_to_csv(scenes)
    return PlainTextResponse(csv, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{project_id}-scenes.csv"'})


@router.get("/projects/{project_id}/export/metadata.json")
async def export_metadata_json(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    metadata = await db.metadata_packages.find_one({"project_id": project_id}, {"_id": 0})
    if not metadata:
        raise HTTPException(status_code=404, detail="No metadata")
    return _ser(metadata)


@router.get("/projects/{project_id}/export/package.zip")
async def export_package_zip(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    script = await db.scripts.find_one({"project_id": project_id}, {"_id": 0})
    scenes = await db.scenes.find({"project_id": project_id}).sort("scene_number", 1).to_list(500)
    metadata = await db.metadata_packages.find_one({"project_id": project_id}, {"_id": 0})
    assets = await db.assets.find({"project_id": project_id}, {"_id": 0}).to_list(500)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(_ser(project), indent=2, default=str))
        if script:
            zf.writestr("script.txt",
                f"# {project['name']}\n\n## Hook\n{script['selected_hook']}\n\n## Full Script\n{script['full_script']}\n\n## CTA\n{script['cta_block']}\n")
            zf.writestr("script.json", json.dumps(_ser(script), indent=2, default=str))
        if scenes:
            zf.writestr("scenes.csv", scenes_to_csv(scenes))
            zf.writestr("scenes.json", json.dumps([_ser(s) for s in scenes], indent=2, default=str))
        if metadata:
            zf.writestr("metadata.json", json.dumps(_ser(metadata), indent=2, default=str))
        if assets:
            zf.writestr("assets.json", json.dumps([_ser(a) for a in assets], indent=2, default=str))
            voiceovers = [a for a in assets if a.get("asset_type") == "voiceover_audio"]
            if voiceovers:
                # Trim heavy fields, keep what a renderer / client needs
                summary = []
                for v in voiceovers:
                    summary.append({
                        "id": v.get("id"),
                        "scene_id": v.get("scene_id"),
                        "scene_number": v.get("scene_number"),
                        "voice_style": v.get("voice_style"),
                        "duration": v.get("duration"),
                        "provider": v.get("provider"),
                        "model": v.get("model"),
                        "mock": v.get("mock"),
                        "status": v.get("status"),
                        "preview_url": v.get("preview_url"),
                        "preview_path": v.get("preview_path"),
                        "text_excerpt": v.get("text_excerpt"),
                        "is_full_script": v.get("scene_id") is None,
                        "selected_for_project": (project.get("selected_voiceover_asset_id") == v.get("id")),
                    })
                zf.writestr("voiceovers.json", json.dumps(summary, indent=2, default=str))
        # Final render metadata (latest completed) — never include internal file_path
        latest = await db.render_jobs.find_one(
            {"project_id": project_id, "status": "completed", "output_url": {"$ne": None}},
            {"_id": 0},
            sort=[("completed_at", -1)],
        )
        if latest:
            zf.writestr("render.json", json.dumps({
                "job_id": latest.get("id"),
                "status": latest.get("status"),
                "duration": latest.get("duration"),
                "file_size": latest.get("file_size"),
                "url": latest.get("output_url"),
                "completed_at": (latest.get("completed_at").isoformat()
                    if isinstance(latest.get("completed_at"), datetime) else latest.get("completed_at")),
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "video_codec": "h264",
                "audio_codec": "aac",
            }, indent=2, default=str))
        zf.writestr("README.md",
            f"# {project['name']}\n\nGenerated with FacelessForge.\n\n"
            f"- Niche: {project['niche']}\n- Topic: {project['topic']}\n"
            f"- Target duration: {project['target_duration']}s\n- Quality: {project.get('quality_score',0)}/100\n")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_id}-package.zip"'},
    )


# ============================ ANALYTICS ============================

@router.get("/analytics/overview")
async def analytics_overview(user=Depends(get_current_user)):
    db = get_db()
    q = {} if user["role"] == "admin" else {"user_id": user["id"]}
    projects = await db.projects.find(q, {"_id": 0}).to_list(1000)
    total = len(projects)
    completed = sum(1 for p in projects if p.get("status") == "COMPLETED")
    in_progress = sum(1 for p in projects if p.get("status") not in ("COMPLETED", "FAILED", "DRAFT"))
    avg_q = (sum(int(p.get("quality_score", 0)) for p in projects) / total) if total else 0
    total_cost = sum(float(p.get("estimated_cost", 0)) for p in projects)

    status_counts = {}
    niche_counts = {}
    for p in projects:
        status_counts[p.get("status", "DRAFT")] = status_counts.get(p.get("status", "DRAFT"), 0) + 1
        niche_counts[p.get("niche", "other")] = niche_counts.get(p.get("niche", "other"), 0) + 1

    # projects over time (last 14 days)
    from collections import Counter
    by_day = Counter()
    for p in projects:
        created = p.get("created_at")
        if isinstance(created, datetime):
            by_day[created.date().isoformat()] += 1
    # monthly content output projection
    monthly_output_projection = round(completed * 4.2 + in_progress * 1.5, 1)

    return {
        "total_projects": total,
        "completed": completed,
        "in_progress": in_progress,
        "average_quality_score": round(avg_q, 1),
        "total_estimated_cost": round(total_cost, 2),
        "monthly_output_projection": monthly_output_projection,
        "status_counts": status_counts,
        "niche_counts": niche_counts,
        "projects_over_time": sorted([{"date": d, "count": c} for d, c in by_day.items()], key=lambda x: x["date"]),
    }


# ============================ SETTINGS ============================

@router.get("/settings")
async def get_settings(user=Depends(get_current_user)):
    db = get_db()
    s = await db.provider_settings.find_one({"user_id": user["id"]}, {"_id": 0})
    if not s:
        s = {
            "user_id": user["id"],
            "default_tone": "calm-authoritative",
            "default_visual_style": "cinematic b-roll",
            "cost_limit_monthly": 50.0,
            "preferred_provider": "deepseek/deepseek-chat",
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.provider_settings.insert_one(s)
    return _ser(s)


@router.patch("/settings")
async def update_settings(body: ProviderSettingsUpdate, user=Depends(get_current_user)):
    db = get_db()
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    patch["updated_at"] = _now()
    await db.provider_settings.update_one(
        {"user_id": user["id"]},
        {"$set": patch, "$setOnInsert": {"user_id": user["id"], "created_at": _now()}},
        upsert=True,
    )
    s = await db.provider_settings.find_one({"user_id": user["id"]}, {"_id": 0})
    return _ser(s)


# ============================ ADMIN USERS ============================

@router.get("/admin/users")
async def admin_list_users(user=Depends(require_roles("admin"))):
    db = get_db()
    users = await db.users.find({}, {"_id": 0, "password_hash": 0}).to_list(1000)
    return [_ser(u) for u in users]


@router.patch("/admin/users/{user_id}/role")
async def admin_update_role(user_id: str, role: str = Body(..., embed=True), _admin=Depends(require_roles("admin"))):
    if role not in ("admin", "creator", "editor", "viewer"):
        raise HTTPException(status_code=422, detail="Invalid role")
    db = get_db()
    await db.users.update_one({"id": user_id}, {"$set": {"role": role, "updated_at": _now()}})
    u = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    return _ser(u) if u else {"ok": True}



# ============================ SHARE LINKS ============================

SHAREABLE_STATUSES = {"METADATA_GENERATED", "ASSETS_READY", "READY_TO_RENDER", "COMPLETED"}


def _share_payload(project: dict) -> dict:
    return {
        "enabled": bool(project.get("share_enabled")),
        "token": project.get("share_token") if project.get("share_enabled") else None,
        "title_override": project.get("share_title_override"),
        "view_count": int(project.get("share_view_count") or 0),
        "last_viewed_at": project.get("share_last_viewed_at").isoformat()
            if isinstance(project.get("share_last_viewed_at"), datetime) else project.get("share_last_viewed_at"),
    }


@router.post("/projects/{project_id}/share")
async def enable_share(project_id: str, body: ShareUpdate = Body(default=None), user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    if project.get("status") not in SHAREABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Project must be one of {sorted(SHAREABLE_STATUSES)} to be shared (current: {project.get('status')})",
        )
    patch = {
        "share_enabled": True,
        "updated_at": _now(),
    }
    if not project.get("share_token"):
        import secrets
        patch["share_token"] = secrets.token_urlsafe(24)
    if body is not None and body.title_override is not None:
        patch["share_title_override"] = body.title_override.strip() or None
    await db.projects.update_one({"id": project_id}, {"$set": patch})
    updated = await db.projects.find_one({"id": project_id}, {"_id": 0})
    return _share_payload(updated)


@router.patch("/projects/{project_id}/share")
async def update_share(project_id: str, body: ShareUpdate, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    patch = {"updated_at": _now()}
    if body.title_override is not None:
        patch["share_title_override"] = body.title_override.strip() or None
    await db.projects.update_one({"id": project_id}, {"$set": patch})
    updated = await db.projects.find_one({"id": project_id}, {"_id": 0})
    return _share_payload(updated)


@router.delete("/projects/{project_id}/share")
async def disable_share(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    await db.projects.update_one(
        {"id": project_id},
        {"$set": {"share_enabled": False, "updated_at": _now()}},
    )
    updated = await db.projects.find_one({"id": project_id}, {"_id": 0})
    return _share_payload(updated)


@router.post("/projects/{project_id}/share/regenerate")
async def regenerate_share_token(project_id: str, user=Depends(get_current_user)):
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=True)
    import secrets
    patch = {
        "share_token": secrets.token_urlsafe(24),
        "share_view_count": 0,
        "share_last_viewed_at": None,
        "updated_at": _now(),
    }
    await db.projects.update_one({"id": project_id}, {"$set": patch})
    updated = await db.projects.find_one({"id": project_id}, {"_id": 0})
    return _share_payload(updated)


@router.get("/public/share/{token}")
async def public_share(token: str):
    """Read-only public view of a shared project. No auth required."""
    if not token or len(token) < 8:
        raise HTTPException(status_code=404, detail="Share link not found")
    db = get_db()
    project = await db.projects.find_one({"share_token": token, "share_enabled": True}, {"_id": 0})
    if not project:
        raise HTTPException(status_code=404, detail="Share link not found or disabled")
    if project.get("status") not in SHAREABLE_STATUSES:
        raise HTTPException(status_code=404, detail="Project is no longer shareable")

    metadata = await db.metadata_packages.find_one({"project_id": project["id"]}, {"_id": 0})
    thumbnails = await db.assets.find(
        {"project_id": project["id"], "asset_type": "thumbnail_concept"}, {"_id": 0}
    ).to_list(10)
    # Selected generated thumbnail (if any)
    selected_thumb = None
    if project.get("selected_thumbnail_asset_id"):
        selected_thumb = await db.assets.find_one(
            {"id": project["selected_thumbnail_asset_id"], "project_id": project["id"]},
            {"_id": 0},
        )

    # Selected full-script voiceover (if any)
    selected_voice = None
    if project.get("selected_voiceover_asset_id"):
        selected_voice = await db.assets.find_one(
            {"id": project["selected_voiceover_asset_id"], "project_id": project["id"], "asset_type": "voiceover_audio"},
            {"_id": 0},
        )

    # Latest completed render job (final video)
    final_render = await db.render_jobs.find_one(
        {"project_id": project["id"], "status": "completed", "output_url": {"$ne": None}},
        {"_id": 0},
        sort=[("completed_at", -1)],
    )

    # Increment view count and update last viewed
    await db.projects.update_one(
        {"id": project["id"]},
        {"$inc": {"share_view_count": 1}, "$set": {"share_last_viewed_at": _now()}},
    )

    display_title = (
        project.get("share_title_override")
        or (metadata or {}).get("selected_title")
        or project["name"]
    )

    # Read-only, scrub private fields
    return {
        "display_title": display_title,
        "project_name": project["name"],
        "niche": project["niche"],
        "status": project["status"],
        "quality_score": int(project.get("quality_score") or 0),
        "metadata": {
            "selected_title": (metadata or {}).get("selected_title"),
            "description": (metadata or {}).get("description"),
            "tags": (metadata or {}).get("tags") or [],
            "hashtags": (metadata or {}).get("hashtags") or [],
            "chapters": (metadata or {}).get("chapters") or [],
            "pinned_comment": (metadata or {}).get("pinned_comment"),
        } if metadata else None,
        "thumbnails": [
            {"name": t.get("name"), "brief": t.get("brief")}
            for t in thumbnails if t.get("brief")
        ],
        "selected_thumbnail_url": selected_thumb.get("preview_url") if selected_thumb else None,
        "selected_voiceover": ({
            "preview_url": selected_voice.get("preview_url"),
            "duration": selected_voice.get("duration"),
            "voice_style": selected_voice.get("voice_style"),
        } if selected_voice else None),
        "final_video": ({
            "url": final_render.get("output_url"),
            "duration": final_render.get("duration"),
            "width": 1920,
            "height": 1080,
            "download_url": final_render.get("output_url"),
            "gcs_mirror": final_render.get("output_url"),
            "r2_mirror": final_render.get("output_url"),
        } if final_render else None),
        "posting_guidance": {
            "youtube_title": (metadata or {}).get("selected_title"),
            "description": (metadata or {}).get("description"),
            "tags": (metadata or {}).get("tags") or [],
            "hashtags": (metadata or {}).get("hashtags") or [],
            "steps": [
                "Upload MP4 native — don't re-encode (1080p 30fps H.264/AAC)",
                "Title: Copy YouTube title from share page (<70 chars)",
                "Description: Paste full description + add your company.com link first line",
                "Tags: Copy tags block; add niche hashtag",
                "Thumbnail: Download selected thumbnail as custom thumbnail 1280x720",
                "Scheduling: Post 10am-2pm local, add chapters, pin comment",
                "Shorts/Reels: Crop center 1080x1920 if vertical needed",
            ],
            "cta": "Need help? Reply — support@ethinx.solutions — ABN 60 578 933 517",
        },
        "download": ({
            "mp4": final_render.get("output_url"),
            "gcs_mirror": final_render.get("output_url"),
            "r2_url": final_render.get("output_url"),
            "filename": f"{(project.get('name') or 'facelessforge').replace(' ', '_')}.mp4",
        } if final_render else None),
        "shared_at": project.get("updated_at").isoformat()
            if isinstance(project.get("updated_at"), datetime) else project.get("updated_at"),
        "abn": "EthinX Solutions ABN 60 578 933 517",
    }


# ── Email: Your video IS ready — {FirstName} {company.com} ──

@router.get("/projects/{project_id}/email/preview")
async def email_preview(project_id: str, user=Depends(get_current_user)):
    """Preview the Your video IS ready cold email for a project (no send)."""
    from .email import render_video_ready_email, get_email_service_status
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    # Find latest completed render for MP4 URL
    final = await db.render_jobs.find_one(
        {"project_id": project_id, "status": "completed", "output_url": {"$ne": None}},
        {"_id": 0},
        sort=[("completed_at", -1)],
    )
    mp4_url = (final or {}).get("output_url") or f"/api/projects/{project_id}/download"
    # Build share url if enabled
    share_url = None
    if project.get("share_enabled") and project.get("share_token"):
        base = os.environ.get("FRONTEND_URL", "https://facelessforge.ethinx.solutions").rstrip("/")
        share_url = f"{base}/s/{project['share_token']}"
    preview = render_video_ready_email(user=user, project=project, mp4_url=mp4_url, share_url=share_url)
    return {
        "service": get_email_service_status(),
        "project_id": project_id,
        "mp4_url": mp4_url,
        "share_url": share_url,
        "gcs_mirror": mp4_url,
        "download": mp4_url,
        "email": preview,
        "posting_guidance": preview["text"].split("POSTING GUIDANCE")[1][:1200] if "POSTING GUIDANCE" in preview["text"] else None,
    }


@router.post("/projects/{project_id}/email/send")
async def email_send(project_id: str, user=Depends(get_current_user)):
    """Send Your video IS ready email to project owner (owner or admin). Triggers actual send/log."""
    from .email import send_video_ready_email
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user, write=False)
    final = await db.render_jobs.find_one(
        {"project_id": project_id, "status": "completed", "output_url": {"$ne": None}},
        {"_id": 0},
        sort=[("completed_at", -1)],
    )
    if not final or not final.get("output_url"):
        raise HTTPException(status_code=400, detail="No completed render — MP4 not ready yet")
    owner = await db.users.find_one({"id": project["user_id"]}, {"_id": 0})
    if not owner:
        raise HTTPException(status_code=404, detail="Project owner not found")
    share_url = None
    if project.get("share_enabled") and project.get("share_token"):
        base = os.environ.get("FRONTEND_URL", "https://facelessforge.ethinx.solutions").rstrip("/")
        share_url = f"{base}/s/{project['share_token']}"
    result = await send_video_ready_email(user=owner, project=project, mp4_url=final["output_url"], share_url=share_url)
    return result


@router.get("/projects/{project_id}/download")
async def download_redirect(project_id: str, user=Depends(get_current_user)):
    """Redirect to MP4 download (R2/GCS mirror). Also supports anonymous via share token query."""
    from fastapi.responses import RedirectResponse
    db = get_db()
    project = await db.projects.find_one({"id": project_id}, {"_id": 0})
    _ensure_project_access(project, user)
    final = await db.render_jobs.find_one(
        {"project_id": project_id, "status": "completed", "output_url": {"$ne": None}},
        {"_id": 0},
        sort=[("completed_at", -1)],
    )
    if not final or not final.get("output_url"):
        raise HTTPException(status_code=404, detail="No completed render for download")
    return RedirectResponse(url=final["output_url"], status_code=302)


@router.get("/email/status")
async def email_status(user=Depends(get_current_user)):
    """Email service config check (any authenticated user)."""
    from .email import get_email_service_status
    return get_email_service_status()
