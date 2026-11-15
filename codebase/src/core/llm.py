"""
AWS Bedrock & LiteLLM Integration Layer with Automatic Mock Fallback (DEC-008).

Provides:
  - Tiered model dispatch (Claude 3.5 Haiku for Triage/Evidence, Claude 3.5 Sonnet for Adjudication).
  - Clean credential sanitization (handles placeholder tokens safely).
  - Automatic graceful fallback to the deterministic mock floor when offline or uncredentialed.
"""
import os
import time
import logging
from typing import Any, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class LLMResponse(BaseModel):
    content: str
    model_id: str
    is_mock: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    fallback_reason: Optional[str] = None


def _clean_aws_credentials():
    """Removes placeholder session tokens like '<your-session-token>' from os.environ."""
    token = os.getenv("AWS_SESSION_TOKEN", "")
    if token.startswith("<") or token == "None" or not token.strip():
        os.environ.pop("AWS_SESSION_TOKEN", None)


class BedrockLLMClient:
    """
    Tiered Bedrock caller via LiteLLM with fail-safe mock fallback.
    """

    def __init__(
        self,
        fast_model_id: Optional[str] = None,
        reasoning_model_id: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self.fast_model_id = fast_model_id or os.getenv(
            "BEDROCK_FAST_MODEL_ID",
            "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )
        self.reasoning_model_id = reasoning_model_id or os.getenv(
            "BEDROCK_REASONING_MODEL_ID",
            "bedrock/us.anthropic.claude-sonnet-4-6",
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        _clean_aws_credentials()

    def is_configured(self) -> bool:
        """Check if AWS credentials look configured."""
        key = os.getenv("AWS_ACCESS_KEY_ID", "")
        secret = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        if not key or not secret or key.startswith("<") or secret.startswith("<"):
            return False
        if os.getenv("MOCK_LLM", "false").lower() in ("true", "1", "yes"):
            return False
        return True

    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        model_id: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 1500,
    ) -> LLMResponse:
        """
        Executes a completion request.
        Attempts AWS Bedrock via LiteLLM; falls back to mock if unavailable.
        """
        chosen_model = model_id or self.reasoning_model_id
        t0 = time.monotonic()

        # If not configured or MOCK_LLM is forced, return mock response directly
        if not self.is_configured():
            return LLMResponse(
                content="",
                model_id=chosen_model,
                is_mock=True,
                input_tokens=0,
                output_tokens=0,
                latency_ms=1,
                fallback_reason="mock_mode_active",
            )

        try:
            import litellm

            _clean_aws_credentials()
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            logger.info("Calling Bedrock model %s in %s...", chosen_model, self.region)
            response = litellm.completion(
                model=chosen_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                aws_region_name=self.region,
            )

            latency_ms = int((time.monotonic() - t0) * 1000)
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
            out_tok = getattr(usage, "completion_tokens", 0) if usage else 0

            return LLMResponse(
                content=content,
                model_id=chosen_model,
                is_mock=False,
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=latency_ms,
            )

        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "Bedrock call failed (%s). Falling back to deterministic mock floor: %s",
                chosen_model,
                exc,
            )
            return LLMResponse(
                content="",
                model_id=chosen_model,
                is_mock=True,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                fallback_reason=str(exc),
            )

