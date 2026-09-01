"""Promo Swarm - separate agents for X/IG/TikTok/YT/Reddit/Product Hunt/LinkedIn
Each promotes Forge renders with analytics, daily cap.
Vault only, health endpoint, OTEL logs.
"""
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os, asyncio, logging, redis, json, uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("promo.swarm")

r = redis.Redis(host=os.getenv("REDIS_HOST","localhost"), port=int(os.getenv("REDIS_PORT","6379")), db=0, decode_responses=True)

# Daily caps per spec
DAILY_CAPS = {"x":10, "instagram":6, "tiktok":8, "youtube":6, "reddit":5, "product_hunt":1, "linkedin":5}

# Lazy import agents
def get_agent(platform: str):
    try:
        mod = __import__(f"agents.{platform}", fromlist=["*"])
        # Find class ending with Agent
        for name in dir(mod):
            obj = getattr(mod, name)
            if isinstance(obj, type) and name.lower().startswith(platform.split("_")[0]):
                return obj()
        # fallback generic
        from agents.base import PromoAgent
        a = PromoAgent()
        a.platform = platform
        a.daily_cap = DAILY_CAPS.get(platform,5)
        return a
    except Exception as e:
        logger.warning(f"agent {platform} load failed {e}")
        from agents.base import PromoAgent
        a = PromoAgent()
        a.platform = platform
        a.daily_cap = DAILY_CAPS.get(platform,5)
        return a

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("promo swarm starting")
    # ensure redis reachable
    try:
        r.ping()
    except Exception as e:
        logger.warning(f"redis ping failed {e}")
    yield
    logger.info("promo swarm shutting down")

app = FastAPI(title="Promo Swarm", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    try:
        r.ping()
        redis_ok = True
    except:
        redis_ok = False
    return {"status":"ok","swarm":"active","redis": redis_ok, "agents": list(DAILY_CAPS.keys())}

@app.get("/healthz")
def healthz():
    return {"status":"ok"}

@app.get("/analytics")
def analytics():
    out = {}
    for plat in DAILY_CAPS:
        try:
            cnt = r.get(f"promo:count:{plat}") or "0"
            recent = r.lrange(f"promo:analytics:{plat}", 0, 4)
            out[plat] = {"count": int(cnt), "daily_cap": DAILY_CAPS[plat], "recent": [json.loads(x) for x in recent]}
        except Exception:
            out[plat] = {"count":0, "daily_cap": DAILY_CAPS[plat], "recent":[]}
    return out

@app.get("/analytics/summary")
def analytics_summary():
    total = 0
    for plat in DAILY_CAPS:
        try:
            total += int(r.get(f"promo:count:{plat}") or 0)
        except:
            pass
    return {"total_promos": total, "caps": DAILY_CAPS}

@app.post("/promote/{platform}")
async def promote_one(platform: str, body: dict):
    platform = platform.lower()
    if platform not in DAILY_CAPS:
        raise HTTPException(404, f"unknown platform {platform}")
    project_id = body.get("project_id") or body.get("id") or str(uuid.uuid4())
    # Cap check inside agent
    agent = get_agent(platform)
    # Build mock project/render for promo
    project = {"id": project_id, "name": body.get("title") or "Forge Render", "topic": body.get("topic") or "AI side hustles"}
    render_job = {"output_url": body.get("video_url") or body.get("url") or "https://videos.ethinx.solutions/mock.mp4"}
    res = await agent.promote(project, render_job)
    return res

@app.post("/promote/all")
async def promote_all(body: dict):
    project_id = body.get("project_id") or str(uuid.uuid4())
    project = {"id": project_id, "name": body.get("title") or "Forge Render", "topic": body.get("topic") or "AI side hustles"}
    render_job = {"output_url": body.get("video_url") or "https://videos.ethinx.solutions/mock.mp4"}
    results = {}
    for plat in DAILY_CAPS:
        agent = get_agent(plat)
        try:
            res = await agent.promote(project, render_job)
            results[plat] = res
        except Exception as e:
            results[plat] = {"platform": plat, "status":"error","error":str(e)[:200]}
        await asyncio.sleep(0.2)
    return {"project_id": project_id, "results": results}

@app.get("/")
def root():
    return {"message":"Promo Swarm API - use /health /promote/{platform} /analytics"}
