"""Core account routes, explicitly mounted independently of the legacy router.

These handlers preserve the account/JWT contract in app/routes.py. Keeping this
router narrow avoids enabling its unrelated generation, publishing and admin API.
"""
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import (
    clear_auth_cookies, create_access_token, create_refresh_token,
    get_current_user, hash_password, set_auth_cookies, verify_password,
)
from ..db import get_db
from ..models import (
    ForgotPasswordRequest, LoginRequest, RegisterRequest, ResetPasswordRequest,
)

router = APIRouter(prefix="/api", tags=["auth"])


def _public_user(user: dict) -> dict:
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in user.items()
        if key not in {"_id", "password_hash"}
    }


@router.post("/auth/register")
async def register(body: RegisterRequest, response: Response, request: Request):
    db = get_db()
    email = body.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    now = datetime.now(timezone.utc)
    user = {
        "id": str(uuid.uuid4()), "name": body.name, "email": email,
        "role": body.role, "password_hash": hash_password(body.password),
        "created_at": now, "updated_at": now,
    }
    await db.users.insert_one(user)
    set_auth_cookies(
        response, create_access_token(user["id"], email, body.role),
        create_refresh_token(user["id"]), request,
    )
    return _public_user(user)


@router.post("/auth/login")
async def login(body: LoginRequest, response: Response, request: Request):
    user = await get_db().users.find_one({"email": body.email.lower()})
    if not user or not verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    set_auth_cookies(
        response, create_access_token(user["id"], user["email"], user["role"]),
        create_refresh_token(user["id"]), request,
    )
    return _public_user(user)


@router.post("/auth/logout")
async def logout(response: Response, user=Depends(get_current_user)):
    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/auth/me")
@router.get("/users/me")
async def me(user=Depends(get_current_user)):
    return _public_user(user)


RESET_RATE_LIMIT = 5
RESET_RATE_WINDOW_SECONDS = 900


@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request):
    """Issue a single-use reset token without revealing account existence."""
    db = get_db()
    email = body.email.lower()
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = (forwarded.split(",", 1)[0].strip() or client_host) if forwarded else client_host
    now = datetime.now(timezone.utc)
    identifier = f"{ip}:{email}"
    attempts = await db.password_reset_attempts.count_documents({
        "identifier": identifier,
        "created_at": {"$gte": now - timedelta(seconds=RESET_RATE_WINDOW_SECONDS)},
    })
    generic = {"ok": True, "message": "If that email exists, a reset link has been issued."}
    if attempts >= RESET_RATE_LIMIT:
        return generic
    await db.password_reset_attempts.insert_one({
        "identifier": identifier, "email": email, "ip": ip, "created_at": now,
    })
    user = await db.users.find_one({"email": email})
    if not user:
        return generic
    token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=int(os.environ.get("PASSWORD_RESET_TTL_MINUTES", "60")))
    await db.password_reset_tokens.update_many(
        {"user_id": user["id"], "used_at": None}, {"$set": {"used_at": now}}
    )
    await db.password_reset_tokens.insert_one({
        "id": str(uuid.uuid4()), "user_id": user["id"], "email": email,
        "token": token, "created_at": now, "expires_at": expires_at, "used_at": None,
    })
    # Delivery is intentionally delegated to the configured mail integration.
    # Never return a token outside explicit local development mode.
    if os.environ.get("DEV_MODE", "false").lower() in {"1", "true", "yes"}:
        generic.update({"dev_reset_token": token, "dev_reset_url": f"/reset-password?token={token}"})
    return generic


@router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordRequest):
    db = get_db()
    now = datetime.now(timezone.utc)
    record = await db.password_reset_tokens.find_one({"token": body.token})
    if not record or record.get("used_at") is not None:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used.")
    expires_at = record.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")
    user = await db.users.find_one({"id": record["user_id"]})
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used.")
    await db.users.update_one({"id": user["id"]}, {"$set": {
        "password_hash": hash_password(body.new_password), "updated_at": now,
    }})
    await db.password_reset_tokens.update_many(
        {"user_id": user["id"], "used_at": None}, {"$set": {"used_at": now}}
    )
    return {"ok": True, "message": "Password updated. You can now sign in."}
