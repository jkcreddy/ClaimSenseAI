from fastapi import APIRouter
from src.api.fixtures import load_claim

router = APIRouter()


@router.get("/{claim_id}")
async def get_claim_status(claim_id: str):
    claim = load_claim(claim_id)
    if not claim:
        return {"error": "not_found", "claim_id": claim_id}
    return {"claim_id": claim_id, "status": claim["claim_status"]}


@router.get("/")
async def list_claims():
    from src.api.fixtures import _load_claims
    claims = _load_claims()
    return [
        {"claim_id": c["claim_id"], "status": c["claim_status"], "assigned_adjuster": c["assigned_adjuster"]}
        for c in claims.values()
    ]
