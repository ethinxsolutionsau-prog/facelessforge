from .base import PromoAgent
import uuid, logging
logger = logging.getLogger("promo.x")
class XAgent(PromoAgent):
    platform = "x"
    daily_cap = 10
    async def promote(self, project, render_job):
        allowed, remaining = self.check_cap()
        if not allowed:
            return {"platform": "x", "status": "cap_reached", "retry_after": remaining}
        # Mock promotion - in prod would call platform API to post teaser/clip
        vid = f"x_promo_{uuid.uuid4().hex[:6]}"
        res = {"platform": "x", "status": "promoted", "promo_id": vid, "project": project.get("id"), "url": f"https://x.com/promo/{vid}", "remaining": remaining}
        self.log(project.get("id"), res)
        logger.info(f"promo x project={project.get('id')} {vid}")
        return res
