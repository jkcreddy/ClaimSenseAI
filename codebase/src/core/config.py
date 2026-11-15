"""
Central config — reads from environment variables with sensible local-dev defaults.
In production these are injected by ECS task definition / Secrets Manager.
"""
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root or codebase
_env_file = Path(__file__).parents[2] / ".env"
if _env_file.exists():
    load_dotenv(dotenv_path=_env_file)
else:
    load_dotenv()

# ── Cognito (real deployment) ─────────────────────────────────────────────────
COGNITO_REGION      = os.getenv("COGNITO_REGION", "us-east-1")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "us-east-1_LOCALDEV")
COGNITO_CLIENT_ID   = os.getenv("COGNITO_CLIENT_ID", "localdevclientid")

# ── JWT signing (local dev uses HS256 symmetric key) ─────────────────────────
# In production, verify against Cognito's JWKS endpoint instead.
JWT_SECRET = os.getenv("JWT_SECRET", "claimsense-local-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = int(os.getenv("JWT_EXPIRY_SECONDS", "3600"))

# ── Bedrock ───────────────────────────────────────────────────────────────────
BEDROCK_FAST_MODEL_ID = os.getenv("BEDROCK_FAST_MODEL_ID", "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0")
BEDROCK_REASONING_MODEL_ID = os.getenv("BEDROCK_REASONING_MODEL_ID", "bedrock/us.anthropic.claude-sonnet-4-6")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
