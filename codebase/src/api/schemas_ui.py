"""
Response schemas that match the ClaimSense_Console.html frontend contract exactly.
Field names are taken verbatim from the compiled React bundle.
"""
from typing import Optional
from pydantic import BaseModel


# ── /config ──────────────────────────────────────────────────────────────────

class AdjudicationConfig(BaseModel):
    enabled: bool
    model_id: str
    region: str
    effort: Optional[str] = None
    provider_label: str = "AWS Bedrock · LiteLLM"


class ConfigResponse(BaseModel):
    adjudication: AdjudicationConfig


# ── /invocations ─────────────────────────────────────────────────────────────

class InvocationRequest(BaseModel):
    prompt: str          # e.g. "Assess CLM-2024-1800"
    claim_id: str
    user_id: str
    group: str           # adjuster | senior-adjuster | fraud-investigator | total-loss-appraiser


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None


class PolicyCitation(BaseModel):
    clause_id: str       # e.g. "CLS-COLL-001"
    statement: str


class PipelineAgent(BaseModel):
    agent: str           # e.g. "TriageAgent"
    latency_ms: int
    usage: TokenUsage
    rag_backend: Optional[str] = None


class Assessment(BaseModel):
    # Decision
    decision: str                        # approve | deny | flag_review | route_specialist
    validation_status: str               # PASSED | GUARDRAIL_BLOCKED | CITATION_MISSING
    confidence: Optional[float] = None   # 0.0–1.0
    net_payable_estimate_usd: float

    # Rationale
    rationale: str
    coverage_assessment: Optional[str] = None
    fraud_assessment: Optional[str] = None

    # Lists
    key_findings: list[str] = []
    required_actions: list[str] = []
    evidence_gaps: list[str] = []
    flags: list[str] = []                # deterministic signal flags
    citations: list[str] = []            # e.g. ["CLS-COLL-001 §4.2"]

    # Baseline comparison (DEC-002 / DEC-003)
    baseline_rules_decision: str
    agrees_with_baseline: bool
    disputes_computed_payout: bool = False

    # Model metadata
    engine: str                          # model id or "rules-fallback"
    model_id: Optional[str] = None
    fallback_reason: Optional[str] = None
    latency_ms: Optional[int] = None
    usage: Optional[TokenUsage] = None

    # Pipeline breakdown
    pipeline: list[PipelineAgent] = []
    policy_citations: list[PolicyCitation] = []

    # Deterministic signals (nested under assessment.signals)
    signals: Optional[dict] = None


class ClaimDetail(BaseModel):
    claim_id: str
    incident_type: str
    vehicle_year: int
    vehicle_make: str
    vehicle_model: str
    claim_status: str


class PolicyDetail(BaseModel):
    policy_id: str
    coverage_type: str
    policy_status: str
    collision_deductible_usd: Optional[float] = None


class InvocationResponse(BaseModel):
    # Top-level envelope
    status: str          # approved | denied | flag_review | route_specialist | not_found | llm_unavailable
    response: str        # human-readable summary sentence shown in "Agent Response" panel
    data_backend: str    # "mcp" | "fixtures"

    # Nested sections rendered by the UI
    assessment: Optional[Assessment] = None
    claim: Optional[ClaimDetail] = None
    policy: Optional[PolicyDetail] = None
