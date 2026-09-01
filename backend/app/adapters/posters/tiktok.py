"""TikTok adapter - vault TIKTOK_ACCESS_TOKEN, rate limit 10/day, 600s max"""
from .base import PlatformPoster, compliance_check, rate_limit_check, log_analytics
import uuid, logging, os
logger = logging.getLogger("autopost.tiktok")
class TiktokPoster(PlatformPoster):
    platform="tiktok"
    def is_configured(self):
        return bool(self.vault_tokens.get("tiktok_access"))
    async def post(self, project, video_url, metadata, tenant_id):
        title= (metadata.get("selected_title") or project.get("name") or "")[:90]
        duration= metadata.get("duration") or project.get("target_duration") or 60
        ok, reason = compliance_check("tiktok", title, metadata.get("description",""), duration)
        if not ok:
            return {"platform":"tiktok","status":"blocked","reason":reason}
        allowed, remaining = rate_limit_check("tiktok", tenant_id)
        if not allowed:
            return {"platform":"tiktok","status":"rate_limited","retry_after": remaining}
        if not self.is_configured():
            vid=f"tk_mock_{uuid.uuid4().hex[:8]}"
            url=f"https://www.tiktok.com/@facelessforge/video/{vid}"
            res={"platform":"tiktok","status":"mock_uploaded","video_id":vid,"url":url}
            log_analytics("tiktok", tenant_id, project.get("id"), res)
            logger.info(f"TikTok mock tenant={tenant_id} vid={vid}")
            return res
        # Real TikTok Upload API would use POST https://open.tiktokapis.com/v2/post/publish/video/init etc.
        # Placeholder for now - log and mock
        try:
            # TODO: implement real TikTok API with chunked upload when token provided
            raise NotImplementedError("TikTok real upload not yet connected - using mock until OAuth verified")
        except Exception as e:
            vid=f"tk_mock_{uuid.uuid4().hex[:8]}"
            url=f"https://www.tiktok.com/@facelessforge/video/{vid}"
            res={"platform":"tiktok","status":"mock_uploaded","video_id":vid,"url":url,"warning":str(e)[:200]}
            log_analytics("tiktok", tenant_id, project.get("id"), res)
            return res
