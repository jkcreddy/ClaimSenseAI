"""
ClaimSense AI Multi-Agent Orchestrator (ARCHITECTURE §3).
Coordinates the sequential 3-stage agent pipeline:
  [ Ingestion ] ──► [ Stage 1: Triage ] ──► [ Stage 2: Evidence ] ──► [ Stage 3: Policy ] ──► [ Output ]
Enforces fail-closed guardrails at entry and exit boundaries.
"""
import time
import logging
from typing import Any, Optional

from src.mcp.server import GovernedMCPDoor
from src.agents.triage_agent import TriageAgent
from src.agents.evidence_agent import EvidenceAgent
from src.agents.policy_agent import PolicyAgent
from src.agents.guardrails import quarantine_input_guardrail, citation_and_payout_guardrail
from src.models.schemas import TriageDossier, EvidenceDossier, AdjudicationOutcome
from src.api.schemas_ui import Assessment, PipelineAgent, PolicyCitation, TokenUsage
from src.tools.policy_retriever import get_clause

logger = logging.getLogger(__name__)


class OrchestratorResult:
    def __init__(
        self,
        assessment: Assessment,
        pipeline: list[PipelineAgent],
        policy_citations: list[PolicyCitation],
        triage_dossier: Optional[TriageDossier] = None,
        evidence_dossier: Optional[EvidenceDossier] = None,
        outcome: Optional[AdjudicationOutcome] = None,
    ):
        self.assessment = assessment
        self.pipeline = pipeline
        self.policy_citations = policy_citations
        self.triage_dossier = triage_dossier
        self.evidence_dossier = evidence_dossier
        self.outcome = outcome
        # Expose attributes for incident_drill assertion compatibility
        self.approved_payout = assessment.net_payable_estimate_usd
        self.decision = assessment.decision


class ClaimSenseOrchestrator:
    """
    OpenAI Agents SDK 3-Stage Pipeline Harness.
    Dual execution support:
      - Deterministic mock floor: Fast, zero external keys, 100% reproducible for CI.
      - Bedrock / LiteLLM mode: Dispatches reasoning prompts when model credentials present.
    """

    def __init__(
        self,
        mcp_door: Optional[GovernedMCPDoor] = None,
        fast_model_id: Optional[str] = None,
        smart_model_id: Optional[str] = None,
    ):
        self.door = mcp_door or GovernedMCPDoor()
        self.triage_agent = TriageAgent(model_id=fast_model_id)
        self.evidence_agent = EvidenceAgent(mcp_door=self.door, model_id=fast_model_id)
        self.policy_agent = PolicyAgent(model_id=smart_model_id)

    def process_claim(
        self,
        claim_input: Any,
        prompt: str = "",
        signals: Optional[dict] = None,
        baseline_decision: str = "flag_review",
    ) -> OrchestratorResult:
        """Synchronous runner for the 3-stage agent pipeline."""
        t_start = time.monotonic()

        # Normalise input into dict
        if isinstance(claim_input, str):
            claim_res = self.door.get_claim(claim_input)
            claim_data = claim_res.data
            claim_id = claim_input
        elif hasattr(claim_input, "model_dump"):
            claim_data = claim_input.model_dump()
            claim_id = claim_data.get("claim_id", claim_data.get("policy_number", "CLM-UNKNOWN"))
        elif isinstance(claim_input, dict):
            claim_data = claim_input
            claim_id = claim_data.get("claim_id", "CLM-UNKNOWN")
        else:
            raise ValueError(f"Unsupported claim input type: {type(claim_input)}")

        # ── 1. Input Guardrail: XPIA & Injection Screen ───────────────────────
        guard_input = {
            "prompt": prompt or f"Assess {claim_id}",
            "claim_id": claim_id,
            "incident_description": claim_data.get("incident_description", ""),
            "damage_description": claim_data.get("damage_description", ""),
        }
        input_guard = quarantine_input_guardrail(guard_input)
        if not input_guard.passed:
            logger.warning("Orchestrator input quarantine tripped: %s", input_guard.reason)
            return self._build_quarantined_result(claim_id, input_guard.reason, baseline_decision)

        pipeline_agents: list[PipelineAgent] = []

        # ── 2. Stage 1: Triage & Classification Agent (Claude 3.5 Haiku) ──────
        t0 = time.monotonic()
        triage_dossier = self.triage_agent.run(claim_data)
        triage_ms = max(1, int((time.monotonic() - t0) * 1000))
        pipeline_agents.append(
            PipelineAgent(
                agent="TriageAgent",
                latency_ms=triage_ms,
                usage=TokenUsage(input_tokens=1800, output_tokens=350),
            )
        )

        # ── 3. Stage 2: Evidence Collection Agent (Claude 3.5 Haiku) ──────────
        t0 = time.monotonic()
        evidence_dossier = self.evidence_agent.run(triage_dossier)
        evidence_ms = max(1, int((time.monotonic() - t0) * 1000))
        pipeline_agents.append(
            PipelineAgent(
                agent="EvidenceAgent",
                latency_ms=evidence_ms,
                usage=TokenUsage(input_tokens=3200, output_tokens=600),
            )
        )

        # ── 4. Stage 3: Policy & Adjudication Agent (Claude 3.5 Sonnet v2) ────
        t0 = time.monotonic()
        outcome = self.policy_agent.run(evidence_dossier)
        policy_ms = outcome.llm_latency_ms if outcome.llm_latency_ms > 0 else max(1, int((time.monotonic() - t0) * 1000))
        policy_in_tok = outcome.input_tokens if outcome.input_tokens > 0 else 6500
        policy_out_tok = outcome.output_tokens if outcome.output_tokens > 0 else 1200
        pipeline_agents.append(
            PipelineAgent(
                agent="PolicyAgent",
                latency_ms=policy_ms,
                usage=TokenUsage(input_tokens=policy_in_tok, output_tokens=policy_out_tok),
                rag_backend="policy_retriever:closed_book_v2024.1",
            )
        )

        # ── 5. Output Guardrail: Citation-or-Block & Payout Check ─────────────
        out_guard = citation_and_payout_guardrail(outcome.model_dump(), signals)
        validation_status = "PASSED"
        decision = outcome.decision
        rationale = outcome.rationale

        if not out_guard.passed:
            logger.warning("Orchestrator output guardrail tripped: %s", out_guard.reason)
            validation_status = out_guard.status
            decision = "flag_review"
            outcome.flags.append("guardrail_tripwire")
            rationale += f" [Guardrail fail-closed: {out_guard.reason}]"

        # Build policy citation objects
        policy_citations: list[PolicyCitation] = []
        for cite in outcome.policy_citations:
            cid = cite.split(" ")[0]
            clause = get_clause(cid)
            statement = clause["title"] if clause else cite
            policy_citations.append(PolicyCitation(clause_id=cid, statement=statement))

        total_ms = max(1, int((time.monotonic() - t_start) * 1000))
        total_in_tok = 1800 + 3200 + policy_in_tok
        total_out_tok = 350 + 600 + policy_out_tok

        assessment = Assessment(
            decision=decision,
            validation_status=validation_status,
            confidence=0.98 if decision == "approve" else 0.95,
            net_payable_estimate_usd=outcome.net_payable,
            rationale=rationale,
            coverage_assessment=outcome.coverage_assessment,
            fraud_assessment=outcome.fraud_assessment,
            key_findings=outcome.key_findings,
            required_actions=outcome.required_actions,
            evidence_gaps=outcome.evidence_gaps,
            flags=outcome.flags,
            citations=outcome.policy_citations,
            baseline_rules_decision=baseline_decision,
            agrees_with_baseline=(decision == baseline_decision),
            disputes_computed_payout=False,
            engine=outcome.model_id if not outcome.is_mock else "openai-agents-sdk",
            model_id=outcome.model_id,
            fallback_reason=outcome.fallback_reason,
            latency_ms=total_ms,
            usage=TokenUsage(input_tokens=total_in_tok, output_tokens=total_out_tok),
            pipeline=pipeline_agents,
            policy_citations=policy_citations,
            signals=signals,
        )

        return OrchestratorResult(
            assessment=assessment,
            pipeline=pipeline_agents,
            policy_citations=policy_citations,
            triage_dossier=triage_dossier,
            evidence_dossier=evidence_dossier,
            outcome=outcome,
        )

    async def process_claim_async(
        self,
        claim_input: Any,
        prompt: str = "",
        signals: Optional[dict] = None,
        baseline_decision: str = "flag_review",
    ) -> OrchestratorResult:
        """Async runner for integration with FastAPI handlers."""
        return self.process_claim(
            claim_input=claim_input,
            prompt=prompt,
            signals=signals,
            baseline_decision=baseline_decision,
        )

    def _build_quarantined_result(
        self, claim_id: str, reason: str, baseline_decision: str
    ) -> OrchestratorResult:
        assessment = Assessment(
            decision="flag_review",
            validation_status="GUARDRAIL_BLOCKED",
            confidence=0.0,
            net_payable_estimate_usd=0.0,
            rationale=f"Quarantined by input guardrail: {reason}",
            key_findings=["Prompt injection or adversarial attack signature detected."],
            required_actions=["Refer to CISO / SIU security review.", "Quarantine input files."],
            flags=["prompt_injection_detected", "guardrail_quarantined"],
            citations=[],
            baseline_rules_decision=baseline_decision,
            agrees_with_baseline=True,
            engine="guardrail-tripwire",
            fallback_reason=reason,
        )
        return OrchestratorResult(assessment=assessment, pipeline=[], policy_citations=[])

