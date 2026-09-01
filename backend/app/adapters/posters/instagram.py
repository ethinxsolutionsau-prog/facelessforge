"""Instagram Reels adapter - vault INSTAGRAM_ACCESS_TOKEN, 90s max"""
from .base import PlatformPoster, compliance_check, rate_limit_check, log_analytics
import uuid, logging
logger = logging.getLogger("autopost.instagram")
class InstagramPoster(PlatformPoster):
    platform="instagram"
    def is_configured(self):
        return bool(self.vault_tokens.get("instagram_token"))
    async def post(self, project, video_url, metadata, tenant_id):
        title=(metadata.get("selected_title") or project.get("name") or "")[:90]
        duration= metadata.get("duration") or project.get("target_duration") or 60
        ok, reason = compliance_check("instagram", title, metadata.get("description",""), duration)
        if not ok:
            # Auto-trim to 90s would be needed - for now block if >90
            return {"platform":"instagram","status":"blocked","reason":reason}
        allowed, remaining = rate_limit_check("instagram", tenant_id)
        if not allowed:
            return {"platform":"instagram","status":"rate_limited","retry_after": remaining}
        if not self.is_configured():
            vid=f"ig_mock_{uuid.uuid4().hex[:8]}"
            url=f"https://www.instagram.com/reel/{vid}/"
            res={"platform":"instagram","status":"mock_uploaded","video_id":vid,"url":url}
            log_analytics("instagram", tenant_id, project.get("id"), res)
            logger.info(f"IG mock tenant={tenant_id} vid={vid}")
            return res
        try:
            raise NotImplementedError("IG real upload via Graph API not yet connected")
        except Exception as e:
            vid=f"ig_mock_{uuid.uuid4().hex[:8]}"
            url=f"https://www.instagram.com/reel/{vid}/"
            res={"platform":"instagram","status":"mock_uploaded","video_id":vid,"url":url,"warning":str(e)[:200]}
            log_analytics("instagram", tenant_id, project.get("id"), res)
            return res
