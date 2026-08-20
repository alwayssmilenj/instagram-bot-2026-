"""Environment-backed configuration. Secrets stay in .env."""
import os
from dotenv import load_dotenv
from settings import BASE_DIR

load_dotenv(BASE_DIR / ".env")

USERNAME = os.getenv("IG_USERNAME", "").strip()
PASSWORD = os.getenv("IG_PASSWORD", "")
OWNER_USERNAME = os.getenv("OWNER_USERNAME", USERNAME).strip().lower().lstrip("@")
OWNER_USERNAMES = {OWNER_USERNAME} | {
    item.strip().lower().lstrip("@")
    for item in os.getenv("OWNER_USERNAMES", "").split(",")
    if item.strip()
}
OWNER_USER_IDS = {
    item.strip() for item in os.getenv("OWNER_USER_IDS", "").split(",") if item.strip()
}
PROXY_URL = os.getenv("PROXY_URL", "").strip()
CHROMIUM_HEADLESS = os.getenv("CHROMIUM_HEADLESS", "false").lower() in {"1", "true", "yes"}
ALLOW_SELF_COMMANDS = os.getenv("ALLOW_SELF_COMMANDS", "false").lower() in {"1", "true", "yes"}
POLL_SECONDS = max(10, int(os.getenv("POLL_SECONDS", "30")))
MAX_REPLIES_PER_HOUR = max(1, int(os.getenv("MAX_REPLIES_PER_HOUR", "30")))
MAX_GLOBAL_REPLIES_PER_HOUR = max(
    MAX_REPLIES_PER_HOUR,
    int(os.getenv("MAX_GLOBAL_REPLIES_PER_HOUR", "120")),
)
MIN_REPLY_INTERVAL_SECONDS = max(0.0, float(os.getenv("MIN_REPLY_INTERVAL_SECONDS", "1.0")))
HOME_ALERT_VOLUME_PERCENT = min(150, max(25, int(os.getenv("HOME_ALERT_VOLUME_PERCENT", "120"))))
LOGIN_TIMEOUT_SECONDS = max(60, int(os.getenv("LOGIN_TIMEOUT_SECONDS", "300")))
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip().rstrip("/")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b").strip()
NVIDIA_TIMEOUT_SECONDS = max(5, min(30, int(os.getenv("NVIDIA_TIMEOUT_SECONDS", "15"))))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "sk_e7f956b31a31aa34b0c45751ebb4d252d1d1de02490a677d").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "n7534fCgBXcPEM82JQYu").strip()
ELEVENLABS_OWNER_VOICE_ID = os.getenv("ELEVENLABS_OWNER_VOICE_ID", "n7534fCgBXcPEM82JQYu").strip()
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2").strip()
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "auto").strip().lower()
AI_BASE_URL = os.getenv("AI_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")
AI_MODEL = os.getenv("AI_MODEL", "ineffa:latest").strip() or "ineffa:latest"
AI_TIMEOUT_SECONDS = max(10, min(180, int(os.getenv("AI_TIMEOUT_SECONDS", "25"))))
AI_MAX_PROMPT_CHARS = max(100, min(4000, int(os.getenv("AI_MAX_PROMPT_CHARS", "1500"))))
AI_MAX_RESPONSE_CHARS = max(1800, min(10000, int(os.getenv("AI_MAX_RESPONSE_CHARS", "7200"))))
AI_MAX_TOKENS = max(24, min(256, int(os.getenv("AI_MAX_TOKENS", "64"))))
AI_MAX_GENERATION_SECONDS = max(5, min(30, int(os.getenv("AI_MAX_GENERATION_SECONDS", "10"))))
COMMAND_WORKERS = max(1, min(25, int(os.getenv("COMMAND_WORKERS", "3"))))
COMMAND_QUEUE_MAX = max(20, int(os.getenv("COMMAND_QUEUE_MAX", "500")))
MAX_RSS_MB = max(512, int(os.getenv("MAX_RSS_MB", "2048")))


def reload_config() -> None:
    load_dotenv(BASE_DIR / ".env", override=True)
    global USERNAME, PASSWORD, OWNER_USERNAME, OWNER_USERNAMES, OWNER_USER_IDS
    USERNAME = os.getenv("IG_USERNAME", "").strip()
    PASSWORD = os.getenv("IG_PASSWORD", "")
    OWNER_USERNAME = os.getenv("OWNER_USERNAME", USERNAME).strip().lower().lstrip("@")
    OWNER_USERNAMES = {OWNER_USERNAME} | {
        item.strip().lower().lstrip("@")
        for item in os.getenv("OWNER_USERNAMES", "").split(",")
        if item.strip()
    }
    OWNER_USER_IDS = {
        item.strip() for item in os.getenv("OWNER_USER_IDS", "").split(",") if item.strip()
    }


def is_owner(username: str = "", user_id: str | None = None) -> bool:
    normalized = str(username).strip().lower().lstrip("@")
    if normalized and (normalized == OWNER_USERNAME or normalized in OWNER_USERNAMES):
        return True
    if user_id is not None and str(user_id) in OWNER_USER_IDS:
        return True
    # Live fallback if .env was changed at runtime
    env_owner = os.getenv("OWNER_USERNAME", "").strip().lower().lstrip("@")
    env_owners = {env_owner} | {
        item.strip().lower().lstrip("@")
        for item in os.getenv("OWNER_USERNAMES", "").split(",")
        if item.strip()
    }
    if normalized and (normalized == env_owner or normalized in env_owners):
        return True
    return False


def validate_credentials() -> None:
    if not USERNAME or not PASSWORD:
        raise RuntimeError("Set IG_USERNAME and IG_PASSWORD in .env")

