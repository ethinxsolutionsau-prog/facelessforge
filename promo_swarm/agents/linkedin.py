from .base import PromoAgent
import uuid, logging
logger = logging.getLogger("promo.linkedin")
class LinkedinAgent(PromoAgent):
    platform = "linkedin"
    daily_cap = 5
    async def promote(self, project, render_job):
        allowed, remaining = self.check_cap()
        if not allowed:
            return {"platform": "linkedin", "status": "cap_reached", "retry_after": remaining}
        # Mock promotion - in prod would call platform API to post teaser/clip
        vid = f"linkedin_promo_{uuid.uuid4().hex[:6]}"
        res = {"platform": "linkedin", "status": "promoted", "promo_id": vid, "project": project.get("id"), "url": f"https://linkedin.com/promo/{vid}", "remaining": remaining}
        self.log(project.get("id"), res)
        logger.info(f"promo linkedin project={project.get('id')} {vid}")
        return res
