from .base import PromoAgent
import uuid, logging
logger = logging.getLogger("promo.tiktok")
class TiktokAgent(PromoAgent):
    platform = "tiktok"
    daily_cap = 8
    async def promote(self, project, render_job):
        allowed, remaining = self.check_cap()
        if not allowed:
            return {"platform": "tiktok", "status": "cap_reached", "retry_after": remaining}
        # Mock promotion - in prod would call platform API to post teaser/clip
        vid = f"tiktok_promo_{uuid.uuid4().hex[:6]}"
        res = {"platform": "tiktok", "status": "promoted", "promo_id": vid, "project": project.get("id"), "url": f"https://tiktok.com/promo/{vid}", "remaining": remaining}
        self.log(project.get("id"), res)
        logger.info(f"promo tiktok project={project.get('id')} {vid}")
        return res
