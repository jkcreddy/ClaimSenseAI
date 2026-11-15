"""
Domain schemas — shared across MCP tools, agents, and the invocation handler.
"""
from typing import Optional, Any
from pydantic import BaseModel


# ── Identity ──────────────────────────────────────────────────────────────────

class AdjusterIdentity(BaseModel):
    adjuster_id: str
    email: str
    role: str        # adjuster | senior-adjuster | fraud-investigator | total-loss-appraiser
    licensed_states: list[str] = []


class ClaimSubmissionRequest(BaseModel):
    """FNOL claim submission request payload (used in incident drills & intake)."""
    idempotency_key: str
    policy_number: str
    claimant_id: str
    jurisdiction_state: str
    peril_type: str
    loss_date: Any
    claimed_amount: float
    description: str


# ── MCP tool inputs ───────────────────────────────────────────────────────────

class GetClaimInput(BaseModel):
    claim_id: str

class GetPolicyInput(BaseModel):
    policy_id: str

class GetDocumentsInput(BaseModel):
    claim_id: str


# ── MCP tool outputs ──────────────────────────────────────────────────────────

class ClaimRecord(BaseModel):
    claim_id: str
    policy_id: str
    vehicle_vin: str
    vehicle_year: int
    vehicle_make: str
    vehicle_model: str
    vehicle_acv_usd: float
    incident_date: str
    incident_type: str
    incident_description: str
    damage_description: str
    repair_estimate_usd: float
    injuries_reported: bool
    police_report_filed: bool
    assigned_adjuster: str
    prior_claims_12mo: int
    fraud_risk_score: float
    claim_status: str
    created_at: str


class PolicyRecord(BaseModel):
    policy_id: str
    holder_first: str
    holder_last: str
    vehicle_vin: str
    vehicle_year: int
    vehicle_make: str
    vehicle_model: str
    vehicle_acv_usd: float
    coverage_type: str
    collision_coverage: bool
    comprehensive_coverage: bool
    collision_deductible_usd: Optional[float]
    comprehensive_deductible_usd: Optional[float]
    liability_limit: str
    medpay_limit_usd: Optional[float]
    policy_status: str
    policy_start: str
    policy_end: str


class DocumentRecord(BaseModel):
    doc_id: str
    claim_id: str
    doc_type: str
    filename: str


class MCPToolResult(BaseModel):
    """Envelope returned by every CMS tool call."""
    tool: str
    caller_id: str          # sub from the forwarded JWT
    caller_group: str       # cognito:groups[0] from the forwarded JWT
    data: dict              # the actual payload
    error: Optional[str] = None


# ── Typed Handoff Dossiers across Agent Pipeline (DEC-008) ────────────────────

class TriageDossier(BaseModel):
    """Output of Stage 1: Triage & Classification Agent."""
    claim_id: str
    policy_id: str
    peril_type: str
    loss_category: str                          # collision | comprehensive | unknown
    statutory_ack_deadline: str                 # 15 calendar days from FNOL
    statutory_decision_deadline: str            # 30 calendar days from FNOL
    estimated_loss: float
    claimant_description: str
    urgency: str = "normal"                     # low | normal | high | immediate


class EvidenceDossier(BaseModel):
    """Output of Stage 2: Evidence Collection Agent."""
    triage: TriageDossier
    policy_status: str
    coverage_type: str
    vehicle_vin: str
    vehicle_acv_usd: float
    deductible_usd: Optional[float] = None
    prior_claims_count_12mo: int = 0
    fraud_risk_score: float = 0.0
    injuries_reported: bool = False
    police_report_filed: bool = False
    documents_complete: bool = True
    documents: list[dict] = []
    total_loss_ratio: float = 0.0
    is_total_loss_candidate: bool = False
    vendor_risk_status: str = "good"            # good | watch | bad


class AdjudicationOutcome(BaseModel):
    """Output of Stage 3: Policy & Adjudication Agent."""
    claim_id: str
    decision: str                               # approve | deny | flag_review | route_specialist
    net_payable: float
    deductible_applied: float
    policy_citations: list[str] = []
    rationale: str
    coverage_assessment: Optional[str] = None
    fraud_assessment: Optional[str] = None
    key_findings: list[str] = []
    required_actions: list[str] = []
    evidence_gaps: list[str] = []
    flags: list[str] = []
    statutory_deadline: str
    handoff_required: bool = True
    model_id: Optional[str] = None
    is_mock: bool = True
    fallback_reason: Optional[str] = None
    llm_latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
