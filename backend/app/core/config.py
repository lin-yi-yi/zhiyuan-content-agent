"""应用配置"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAAS = os.getenv("SAAS_MODE", "false").lower() in {"true", "1", "yes", "on"}
ENV_PATH = Path(os.getenv("SAAS_ENV_FILE", str(PROJECT_ROOT / ".env.saas"))) if SAAS else PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH)


class Settings:
    AIHOT_ENABLED = os.getenv("AIHOT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    GITHUB_ENABLED = os.getenv("GITHUB_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    APP_ENV = os.getenv("APP_ENV", "local")
    APP_NAME = os.getenv("APP_NAME", "AI Content Growth Agent")
    BACKEND_CORS_ORIGINS = os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:5173")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///:memory:" if SAAS else f"sqlite:///{PROJECT_ROOT / 'content-agent.db'}")
    DEFAULT_LLM_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "local")
    DEFAULT_LLM_MODEL = os.getenv("DEFAULT_LLM_MODEL", "local-rule-based-v0")
    TOPIC_SCORE_PROVIDER = os.getenv("TOPIC_SCORE_PROVIDER", DEFAULT_LLM_PROVIDER)
    TOPIC_SCORE_MODEL = os.getenv("TOPIC_SCORE_MODEL", DEFAULT_LLM_MODEL)
    DRAFT_GENERATION_PROVIDER = os.getenv("DRAFT_GENERATION_PROVIDER", DEFAULT_LLM_PROVIDER)
    DRAFT_GENERATION_MODEL = os.getenv("DRAFT_GENERATION_MODEL", DEFAULT_LLM_MODEL)
    CARD_GENERATION_PROVIDER = os.getenv("CARD_GENERATION_PROVIDER", DEFAULT_LLM_PROVIDER)
    CARD_GENERATION_MODEL = os.getenv("CARD_GENERATION_MODEL", DEFAULT_LLM_MODEL)
    COMPLIANCE_CHECK_PROVIDER = os.getenv("COMPLIANCE_CHECK_PROVIDER", DEFAULT_LLM_PROVIDER)
    COMPLIANCE_CHECK_MODEL = os.getenv("COMPLIANCE_CHECK_MODEL", DEFAULT_LLM_MODEL)
    # Operator-supplied exact provider/model prices; no market-price defaults.
    MODEL_PRICING_JSON = os.getenv("MODEL_PRICING_JSON", "[]")


settings = Settings()
