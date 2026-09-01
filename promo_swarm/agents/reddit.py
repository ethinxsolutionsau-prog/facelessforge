from .base import PromoAgent
import uuid, logging
logger = logging.getLogger("promo.reddit")
class RedditAgent(PromoAgent):
    platform = "reddit"
    daily_cap = 5
    async def promote(self, project, render_job):
        allowed, remaining = self.check_cap()
        if not allowed:
            return {"platform": "reddit", "status": "cap_reached", "retry_after": remaining}
        # Mock promotion - in prod would call platform API to post teaser/clip
        vid = f"reddit_promo_{uuid.uuid4().hex[:6]}"
        res = {"platform": "reddit", "status": "promoted", "promo_id": vid, "project": project.get("id"), "url": f"https://reddit.com/promo/{vid}", "remaining": remaining}
        self.log(project.get("id"), res)
        logger.info(f"promo reddit project={project.get('id')} {vid}")
        return res
