"""
Stage 3: Policy & Adjudication Agent — The Money Step (ARCHITECTURE §3.1).
Responsibilities:
  - Executes grounded closed-book RAG retrieval over governed policy clauses (DEC-004).
  - Invokes pure-Python deterministic payout math: compute_settlement_payout() (DEC-002).
  - Evaluates decision outcome across 4-code vocabulary (approve, deny, flag_review, route_specialist).
  - Guarantees valid policy citations and the mandatory human confirmation disclaimer (DEC-001).
  - Produces typed AdjudicationOutcome.
"""
import os
from typing import Any, Optional
from src.models.schemas import EvidenceDossier, AdjudicationOutcome
from src.tools.policy_retriever import search_policy_clauses, get_clause
from src.tools.math_tools import compute_settlement_payout
from src.core.llm import BedrockLLMClient


class PolicyAgent:
    """Stage 3: Reasoning, policy matching, and adjudication agent."""

    def __init__(self, model_id: Optional[str] = None):
        self.agent_name = "PolicyAgent"
        self.model_id = model_id or os.getenv(
            "BEDROCK_REASONING_MODEL_ID",
            "bedrock/us.anthropic.claude-sonnet-4-6",
        )
        self.llm = BedrockLLMClient(reasoning_model_id=self.model_id)

    def run(self, evidence: EvidenceDossier) -> AdjudicationOutcome:
        """
        Executes policy adjudication reasoning.
        """
        triage = evidence.triage
        claim_id = triage.claim_id
        loss_cat = triage.loss_category
        peril = triage.peril_type

        # ── 1. Closed-Book Policy Retrieval (DEC-004) ─────────────────────────
        retrieved = search_policy_clauses(query=peril, loss_category=loss_cat, limit=4)
        retrieved_ids = {c["clause_id"] for c in retrieved}

        # ── 2. Coverage Applicability Check ───────────────────────────────────
        coverage_applies = True
        if evidence.coverage_type == "Liability Only":
            coverage_applies = False
            retrieved_ids.add("CLS-LIAB-003")
        elif evidence.policy_status != "Active":
            coverage_applies = False
            retrieved_ids.add("CLS-PER-006")

        # ── 3. Deterministic Financial Computation (DEC-002) ──────────────────
        deductible = evidence.deductible_usd or 0.0
        if coverage_applies and evidence.deductible_usd is not None:
            retrieved_ids.add("CLS-DED-004")
            if loss_cat == "collision":
                retrieved_ids.add("CLS-COLL-001")
            elif loss_cat == "comprehensive":
                retrieved_ids.add("CLS-COMP-002")

            payout_res = compute_settlement_payout(
                claimed_amount=triage.estimated_loss,
                vehicle_acv=evidence.vehicle_acv_usd,
                deductible=deductible,
                claim_id=claim_id,
            )
            net_payable = payout_res["net_payable"]
            deductible_applied = payout_res["deductible_applied"]
            if payout_res["senior_approval_required"]:
                retrieved_ids.add("CLS-PAY-012")
        else:
            net_payable = 0.0
            deductible_applied = 0.0

        # ── 4. Adjudication Decision Rules (DEC-003) ──────────────────────────
        flags = []
        key_findings = []
        required_actions = []
        evidence_gaps = []

        if evidence.fraud_risk_score >= 0.70:
            decision = "route_specialist"
            retrieved_ids.add("CLS-FRD-007")
            flags.append("high_fraud_score")
            key_findings.append(f"Vendor fraud score {evidence.fraud_risk_score:.2f} exceeds SIU threshold (≥ 0.70).")
            required_actions.append("Assign to Special Investigation Unit (SIU) for fraud investigation.")
            rationale = (
                f"Fraud score {evidence.fraud_risk_score:.2f} ≥ 0.70 triggers mandatory SIU referral under CLS-FRD-007. "
                f"Repair authorization suspended. Human adjuster confirmation is required."
            )

        elif not coverage_applies:
            decision = "deny"
            flags.append("coverage_not_in_force")
            if evidence.coverage_type == "Liability Only":
                key_findings.append("Policy is Liability Only; excludes all first-party vehicle damage (CLS-LIAB-003).")
                rationale = "Recommend denial of first-party physical damage under Liability Only policy (CLS-LIAB-003). Human adjuster confirmation is required."
            else:
                key_findings.append("Policy was not active or in force on the reported loss date (CLS-PER-006).")
                rationale = "Recommend denial: policy was not in force on incident date (CLS-PER-006). Human adjuster confirmation is required."
            required_actions.append("Issue formal notice of coverage denial citing policy exclusions.")

        elif evidence.is_total_loss_candidate:
            decision = "route_specialist"
            retrieved_ids.add("CLS-TL-005")
            flags.append("total_loss")
            key_findings.append(f"Repair estimate to ACV ratio ({evidence.total_loss_ratio:.1%}) meets total-loss threshold (≥ 75%).")
            required_actions.append("Assign to licensed total-loss appraiser for salvage and valuation review.")
            rationale = (
                f"Total-loss ratio {evidence.total_loss_ratio:.1%} ≥ 75% per CLS-TL-005. "
                f"Referred to total-loss appraiser for vehicle valuation. Human adjuster confirmation is required."
            )

        elif evidence.injuries_reported:
            decision = "flag_review"
            retrieved_ids.add("CLS-INJ-008")
            flags.append("injuries_reported")
            key_findings.append("Bodily injuries reported on collision file.")
            required_actions.append("Assign injury-qualified adjuster to review medical documentation.")
            rationale = "Bodily injuries reported: requires evaluation by injury-qualified adjuster per CLS-INJ-008. Human adjuster confirmation is required."

        elif not evidence.documents_complete:
            decision = "flag_review"
            retrieved_ids.add("CLS-DOC-009")
            flags.append("docs_incomplete")
            evidence_gaps.append("Required documents (FNOL or certified repair estimate) pending processing.")
            required_actions.append("Request completed repair estimate and documentation before authorization.")
            rationale = "Pending documentation: repair estimate or proof of loss incomplete per CLS-DOC-009. Human adjuster confirmation is required."

        # First-pass approval band (DEC-003)
        elif evidence.total_loss_ratio <= 0.35 and evidence.fraud_risk_score < 0.30 and coverage_applies:
            decision = "approve"
            retrieved_ids.add("CLS-PAY-012")
            flags.append("no_flags")
            key_findings.append(f"Clear first-pass collision claim. Loss ratio {evidence.total_loss_ratio:.1%} ≤ 35%.")
            required_actions.append("Confirm settlement payment to claimant or designated repair facility.")
            rationale = (
                f"Recommend first-pass approval at computed net payable ${net_payable:,.2f} "
                f"(repair estimate ${triage.estimated_loss:,.2f} less ${deductible:,.2f} deductible). "
                f"Human adjuster confirmation is required."
            )

        else:
            decision = "flag_review"
            flags.append("standard_review")
            key_findings.append("Claim requires discretionary review by licensed claims handler.")
            required_actions.append("Adjuster review of repair scope and subrogation potential.")
            rationale = f"Flagged for licensed adjuster review per standard operating guidelines. Human adjuster confirmation is required."

        if evidence.prior_claims_count_12mo > 0:
            key_findings.append(f"{evidence.prior_claims_count_12mo} prior claim(s) filed in past 12 months.")
        if evidence.police_report_filed:
            key_findings.append("Police accident report on file.")

        # Format citations
        formatted_citations = []
        for cid in sorted(retrieved_ids):
            clause = get_clause(cid)
            if clause:
                formatted_citations.append(f"{cid} §4.2" if cid == "CLS-COLL-001" else cid)

        # ── 5. Live Bedrock Reasoning Dispatch with Mock Fallback ────────────
        llm_response = self.llm.complete(
            prompt=(
                f"Claim {claim_id}: {peril} ({loss_cat}). Estimated loss: ${triage.estimated_loss:,.2f}. "
                f"ACV: ${evidence.vehicle_acv_usd:,.2f}. Deductible: ${deductible:,.2f}. "
                f"Computed net payable: ${net_payable:,.2f}. Decision: {decision}. "
                f"Provide concise adjuster rationale citing retrieved clauses: {sorted(retrieved_ids)}."
            ),
            system_prompt=(
                "You are ClaimSense AI's Policy & Adjudication Agent. "
                "Conclude every recommendation with: 'Human adjuster confirmation is required.'"
            ),
            model_id=self.model_id,
        )

        final_rationale = rationale
        if not llm_response.is_mock and llm_response.content.strip():
            llm_text = llm_response.content.strip()
            if "Human adjuster confirmation is required" not in llm_text:
                llm_text += " Human adjuster confirmation is required."
            final_rationale = llm_text

        return AdjudicationOutcome(
            claim_id=claim_id,
            decision=decision,
            net_payable=net_payable,
            deductible_applied=deductible_applied,
            policy_citations=formatted_citations,
            rationale=final_rationale,
            coverage_assessment=(
                f"Coverage in force under {evidence.coverage_type}. Loss category: {loss_cat}. "
                f"Applicable deductible: ${deductible:,.2f}." if coverage_applies else
                "Coverage not in force or loss excluded under policy terms."
            ),
            fraud_assessment=f"Fraud risk score: {evidence.fraud_risk_score:.2f} ({evidence.vendor_risk_status}).",
            key_findings=key_findings,
            required_actions=required_actions,
            evidence_gaps=evidence_gaps,
            flags=flags or ["no_flags"],
            statutory_deadline=triage.statutory_decision_deadline,
            handoff_required=True,
            model_id=self.model_id,
            is_mock=llm_response.is_mock,
            fallback_reason=llm_response.fallback_reason,
            llm_latency_ms=llm_response.latency_ms,
            input_tokens=llm_response.input_tokens,
            output_tokens=llm_response.output_tokens,
        )

