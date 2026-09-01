import os, redis, logging, json, uuid
from datetime import datetime, timezone
logger = logging.getLogger("promo.base")
r = redis.Redis(host=os.getenv("REDIS_HOST","localhost"), port=int(os.getenv("REDIS_PORT","6379")), db=0, decode_responses=True)
class PromoAgent:
    platform: str = "base"
    daily_cap: int = 5
    def __init__(self):
        self.platform = self.__class__.platform
    def check_cap(self) -> tuple[bool,int]:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"promo:{self.platform}:{day}"
        try:
            cnt = r.incr(key)
            if cnt==1: r.expire(key, 86400)
            if cnt > self.daily_cap:
                return False, r.ttl(key)
            return True, self.daily_cap - cnt
        except Exception as e:
            logger.warning(f"promo cap redis fail {e}")
            return True, self.daily_cap
    def log(self, project_id: str, result: dict):
        try:
            ts = datetime.now(timezone.utc).isoformat()
            r.lpush(f"promo:analytics:{self.platform}", json.dumps({"project": project_id, "result": result, "ts": ts}))
            r.ltrim(f"promo:analytics:{self.platform}", 0, 999)
            r.incr(f"promo:count:{self.platform}")
            # also global
            r.lpush(f"promo:analytics:all", json.dumps({"platform": self.platform, "project": project_id, "ts": ts}))
        except Exception:
            pass
    async def promote(self, project: dict, render_job: dict) -> dict:
        raise NotImplementedError
