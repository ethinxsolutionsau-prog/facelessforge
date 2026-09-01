"""X (Twitter) adapter - vault X_BEARER_TOKEN, 140s max"""
from .base import PlatformPoster, compliance_check, rate_limit_check, log_analytics
import uuid, logging
logger = logging.getLogger("autopost.x")
class XPoster(PlatformPoster):
    platform="x"
    def is_configured(self):
        return bool(self.vault_tokens.get("x_bearer"))
    async def post(self, project, video_url, metadata, tenant_id):
        title=(metadata.get("selected_title") or project.get("name") or "")[:250]
        duration= metadata.get("duration") or project.get("target_duration") or 60
        ok, reason = compliance_check("x", title, metadata.get("description",""), duration)
        if not ok:
            return {"platform":"x","status":"blocked","reason":reason}
        allowed, remaining = rate_limit_check("x", tenant_id)
        if not allowed:
            return {"platform":"x","status":"rate_limited","retry_after": remaining}
        if not self.is_configured():
            vid=f"x_mock_{uuid.uuid4().hex[:8]}"
            url=f"https://x.com/facelessforge/status/{vid}"
            res={"platform":"x","status":"mock_uploaded","video_id":vid,"url":url}
            log_analytics("x", tenant_id, project.get("id"), res)
            logger.info(f"X mock tenant={tenant_id} vid={vid}")
            return res
        try:
            raise NotImplementedError("X real upload via v2 media not yet connected")
        except Exception as e:
            vid=f"x_mock_{uuid.uuid4().hex[:8]}"
            url=f"https://x.com/facelessforge/status/{vid}"
            res={"platform":"x","status":"mock_uploaded","video_id":vid,"url":url,"warning":str(e)[:200]}
            log_analytics("x", tenant_id, project.get("id"), res)
            return res
