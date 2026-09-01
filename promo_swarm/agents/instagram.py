from .base import PromoAgent
import uuid, logging
logger = logging.getLogger("promo.instagram")
class InstagramAgent(PromoAgent):
    platform = "instagram"
    daily_cap = 6
    async def promote(self, project, render_job):
        allowed, remaining = self.check_cap()
        if not allowed:
            return {"platform": "instagram", "status": "cap_reached", "retry_after": remaining}
        # Mock promotion - in prod would call platform API to post teaser/clip
        vid = f"instagram_promo_{uuid.uuid4().hex[:6]}"
        res = {"platform": "instagram", "status": "promoted", "promo_id": vid, "project": project.get("id"), "url": f"https://instagram.com/promo/{vid}", "remaining": remaining}
        self.log(project.get("id"), res)
        logger.info(f"promo instagram project={project.get('id')} {vid}")
        return res
