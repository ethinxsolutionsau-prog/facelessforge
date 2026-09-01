import os
DAILY_CAPS = {
    "x": 10,
    "instagram": 6,
    "tiktok": 8,
    "youtube": 6,
    "reddit": 5,
    "product_hunt": 1,
    "linkedin": 5,
}
# Vault tokens - all from env file /opt/ethinx/deployment/secrets-vault/facelessforge-deploy.env
TOKENS = {
    "x_bearer": os.getenv("X_BEARER_TOKEN",""),
    "ig_token": os.getenv("INSTAGRAM_ACCESS_TOKEN",""),
    "tiktok_token": os.getenv("TIKTOK_ACCESS_TOKEN",""),
    "yt_refresh": os.getenv("YOUTUBE_REFRESH_TOKEN",""),
    "reddit_client": os.getenv("REDDIT_CLIENT_ID",""),
    "ph_token": os.getenv("PRODUCT_HUNT_TOKEN",""),
    "linkedin_token": os.getenv("LINKEDIN_ACCESS_TOKEN",""),
}
REDIS_URL = os.getenv("REDIS_URL","redis://localhost:6379")
