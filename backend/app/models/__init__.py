"""Pydantic models for request/response schemas."""
from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict
# ---------- Auth ----------
class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: str = Field(min_length=6, max_length=200)
    role: Literal["creator", "editor", "viewer"] = "creator"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    new_password: str = Field(min_length=6, max_length=200)


class UserPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    email: EmailStr
    role: str
    created_at: datetime


# ---------- Project ----------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    niche: str = Field(min_length=3, max_length=120)
    topic: str = Field(min_length=10, max_length=1000)
    audience: str = Field(min_length=1, max_length=200)
    tone: str = Field(min_length=1, max_length=80)
    target_duration: int = Field(ge=30, le=3600)
    voice_style: Optional[str] = "neutral male narrator"
    voice_id: Optional[str] = None
    visual_style: Optional[str] = "cinematic b-roll"
    monetisation_intent: Optional[str] = "ads + affiliate"
    cta_goal: Optional[str] = "subscribe"
    auto_post: Optional[bool] = False
    platforms: Optional[List[str]] = []


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    niche: Optional[str] = None
    topic: Optional[str] = None
    audience: Optional[str] = None
    tone: Optional[str] = None
    target_duration: Optional[int] = None
    voice_style: Optional[str] = None
    voice_id: Optional[str] = None
    visual_style: Optional[str] = None
    monetisation_intent: Optional[str] = None
    cta_goal: Optional[str] = None
    status: Optional[str] = None
    auto_post: Optional[bool] = None
    platforms: Optional[List[str]] = None


class Project(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str
    name: str
    niche: str
    topic: str
    audience: str
    tone: str
    target_duration: int
    voice_style: str
    visual_style: str
    monetisation_intent: str
    cta_goal: str
    status: str
    quality_score: int
    estimated_cost: float
    auto_post: bool = False
    platforms: List[str] = []
    created_at: datetime
    updated_at: datetime


# ---------- Script / Scene / Metadata (output payloads) ----------
class ScriptOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    project_id: str
    hook_option_one: str
    hook_option_two: str
    hook_option_three: str
    selected_hook: str
    full_script: str
    retention_beats: List[str]
    cta_block: str
    word_count: int
    estimated_duration: int
    created_at: datetime
    updated_at: datetime


class SceneOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    project_id: str
    scene_number: int
    start_time: int
    end_time: int
    narration_text: str
    visual_direction: str
    asset_type: str
    search_terms: List[str]
    image_prompt: str
    caption_text: str
    status: str


class MetadataOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    project_id: str
    title_options: List[str]
    selected_title: str
    description: str
    tags: List[str]
    hashtags: List[str]
    chapters: List[dict]
    pinned_comment: str


class ThumbnailConcept(BaseModel):
    thumbnail_title_text: str
    visual_composition: str
    emotion_angle: str
    background_idea: str
    subject_focal_point: str
    colour_direction: str
    click_trigger: str
    image_prompt: str


# ---------- Script editing ----------
class ScriptUpdate(BaseModel):
    selected_hook: Optional[str] = None
    full_script: Optional[str] = None
    cta_block: Optional[str] = None


class MetadataUpdate(BaseModel):
    selected_title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    pinned_comment: Optional[str] = None


# ---------- Provider settings ----------
class ProviderSettingsUpdate(BaseModel):
    default_tone: Optional[str] = None
    default_visual_style: Optional[str] = None
    cost_limit_monthly: Optional[float] = None
    preferred_provider: Optional[str] = None
    # White-label branding (Advanced plan)
    brand_name: Optional[str] = Field(default=None, max_length=120)
    brand_logo_url: Optional[str] = Field(default=None, max_length=500)
    brand_color: Optional[str] = Field(default=None, max_length=20)
    white_label_enabled: Optional[bool] = None
    custom_domain: Optional[str] = Field(default=None, max_length=200)


# ---------- Asset ----------
class AssetCreate(BaseModel):
    name: str
    asset_type: str
    source: str = "generated_brief"
    tags: List[str] = []
    file_path: Optional[str] = None


# ---------- Stock / Pexels ----------
class StockAttachRequest(BaseModel):
    """Caller sends one normalised stock result as returned by /find-assets."""
    source: str = "pexels"
    external_id: str
    media_type: Literal["stock_video", "stock_image"]
    title: str
    preview_url: Optional[str] = None
    source_url: Optional[str] = None
    download_url: Optional[str] = None
    attribution_name: Optional[str] = None
    attribution_url: Optional[str] = None
    width: Optional[int] = 0
    height: Optional[int] = 0
    duration: Optional[int] = None
    tags: List[str] = []
    query: Optional[str] = None


class FindAssetsRequest(BaseModel):
    query: Optional[str] = None
    media_type: Literal["both", "videos", "photos"] = "both"
    per_page: int = Field(default=12, ge=1, le=40)


class AssetStatusUpdate(BaseModel):
    status: Literal["suggested", "attached", "rejected", "selected", "ready"]


# ---------- Auto-attach / Thumbnail images ----------
class AutoAttachRequest(BaseModel):
    replace_existing: bool = False
    media_type: Literal["both", "videos", "photos"] = "both"


class GenerateThumbnailImagesRequest(BaseModel):
    variants: int = Field(default=1, ge=1, le=3)


# ---------- Voiceover (TTS) ----------
class GenerateVoiceoverRequest(BaseModel):
    voice_style: Optional[str] = None  # narrator preset ("NEUTRAL_MALE_NARRATOR") or style word (calm, energetic, ...)
    tone: Optional[str] = None  # style/tone word tuning ElevenLabs settings: calm, energetic, dramatic, documentary, corporate, mysterious, narrator
    text_override: Optional[str] = Field(default=None, max_length=5000)


# ---------- Render ----------
class RenderStartRequest(BaseModel):
    force: Optional[bool] = False  # allow concurrent renders if explicitly forced (admin/owner)
    voice_style: Optional[str] = None  # narrator preset or style (moved from generate-script to render payload)
    voice_id: Optional[str] = None  # explicit ElevenLabs voice_id (also on projects table, not just scripts)


# ---------- Share ----------
class ShareUpdate(BaseModel):
    title_override: Optional[str] = Field(default=None, max_length=200)

# --- Billing models (Paddle) ---
"""Billing models - Paddle subscription & credit transactions."""
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


def _now():
    return datetime.now(timezone.utc)


class Subscription(BaseModel):
    user_id: str
    paddle_customer_id: Optional[str] = None
    paddle_subscription_id: Optional[str] = None
    paddle_price_id: Optional[str] = None
    plan: str = Field(default="free", description="free/starter/creator/pro/agency")
    status: str = Field(default="active", description="active/canceled/past_due/inactive")
    credits_monthly: int = 30
    credits_remaining: int = 30
    current_period_end: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    class Config:
        extra = "allow"


class CreditTransaction(BaseModel):
    user_id: str
    amount: int  # positive credit, negative debit
    reason: str = Field(description="e.g. purchase starter, auto-attach scene 02, monthly reset")
    created_at: datetime = Field(default_factory=_now)

    class Config:
        extra = "allow"
