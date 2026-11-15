"""
Tests for the JWT-forwarding chain:
  POST /auth/login → JWT → POST /invocations → GovernedMCPDoor → CMS tools

Covers:
  - JWT flows from /invocations header into MCP context
  - MCP get_claim enforces RBAC (own claim allowed, other adjuster's denied)
  - MCP get_claim for senior-adjuster is unrestricted
  - MCP get_policy returns data when caller has claim access
  - MCP get_documents returns file list for accessible claim
  - /invocations with no JWT still returns a rule-engine response (graceful)
  - /invocations body identity is overridden by verified JWT (DEC-005)
"""
import pytest
import jwt as pyjwt
from fastapi.testclient import TestClient

from src.api.main import app
from src.mcp.server import GovernedMCPDoor
from src.mcp.auth_context import set_jwt, reset_jwt
from src.core.config import JWT_SECRET, JWT_ALGORITHM

client = TestClient(app, raise_server_exceptions=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def get_token(username: str, password: str) -> str:
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()["access_token"]


def invoke(claim_id: str, token: str | None = None, user_id: str = "alice", group: str = "adjuster"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/invocations", json={
        "prompt": f"Assess {claim_id}",
        "claim_id": claim_id,
        "user_id": user_id,
        "group": group,
    }, headers=headers)


# ── /invocations end-to-end ───────────────────────────────────────────────────

def test_invocation_with_valid_jwt_returns_200():
    token = get_token("alice", "alice123")
    r = invoke("CLM-2024-1800", token)   # alice's own claim
    assert r.status_code == 200
    assert r.json()["status"] in {"approved", "denied", "flag_review", "route_specialist"}


def test_invocation_jwt_overrides_body_identity():
    """JWT sub=alice overrides body user_id=eve (DEC-005)."""
    token = get_token("alice", "alice123")
    # Body says eve but JWT says alice — alice owns CLM-2024-1800
    r = client.post("/invocations", json={
        "prompt": "Assess CLM-2024-1800",
        "claim_id": "CLM-2024-1800",
        "user_id": "eve",       # attacker tries to spoof identity in body
        "group": "adjuster",
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["status"] != "denied"   # alice has access, so not denied


def test_invocation_adjuster_denied_other_claim():
    """alice cannot access bob's claim CLM-2024-2457."""
    token = get_token("alice", "alice123")
    r = invoke("CLM-2024-2457", token)
    assert r.status_code == 200
    assert r.json()["status"] == "denied"


def test_invocation_senior_adjuster_unrestricted():
    """carol (senior-adjuster) can access any claim."""
    token = get_token("carol", "carol123")
    r = invoke("CLM-2024-2457", token, user_id="carol", group="senior-adjuster")
    assert r.status_code == 200
    assert r.json()["status"] != "denied"


def test_invocation_no_jwt_returns_rule_engine_response():
    """No JWT → graceful rule-engine fallback, not a 500."""
    r = invoke("CLM-2024-1800", token=None)
    assert r.status_code == 200
    assert r.json()["status"] in {"approved", "denied", "flag_review", "route_specialist"}


# ── GovernedMCPDoor unit tests (JWT via context) ──────────────────────────────

@pytest.fixture
def alice_door():
    """GovernedMCPDoor with alice's JWT bound to context."""
    token = get_token("alice", "alice123")
    ctx = set_jwt(token)
    yield GovernedMCPDoor()
    reset_jwt(ctx)


@pytest.fixture
def carol_door():
    """GovernedMCPDoor with carol's (senior-adjuster) JWT bound to context."""
    token = get_token("carol", "carol123")
    ctx = set_jwt(token)
    yield GovernedMCPDoor()
    reset_jwt(ctx)


def test_mcp_get_claim_own_claim(alice_door):
    result = alice_door.get_claim("CLM-2024-1800")
    assert result.error is None
    assert result.caller_id == "alice"
    assert result.caller_group == "adjuster"
    assert result.data["claim_id"] == "CLM-2024-1800"


def test_mcp_get_claim_other_adjuster_denied(alice_door):
    result = alice_door.get_claim("CLM-2024-2457")   # bob's claim
    assert result.error is not None
    assert "denied" in result.error.lower()
    assert result.data == {}


def test_mcp_get_claim_senior_adjuster_unrestricted(carol_door):
    result = carol_door.get_claim("CLM-2024-2457")   # not carol's claim
    assert result.error is None
    assert result.data["claim_id"] == "CLM-2024-2457"


def test_mcp_get_policy(alice_door):
    claim = alice_door.get_claim("CLM-2024-1800")
    policy_id = claim.data["policy_id"]
    result = alice_door.get_policy(policy_id)
    assert result.error is None
    assert result.data["policy_id"] == policy_id
    assert result.caller_id == "alice"


def test_mcp_get_documents_own_claim(alice_door):
    result = alice_door.get_documents("CLM-2024-1800")
    assert result.error is None
    assert result.data["count"] > 0
    assert all("doc_id" in d for d in result.data["documents"])


def test_mcp_get_documents_denied(alice_door):
    result = alice_door.get_documents("CLM-2024-2457")   # bob's claim
    assert result.error is not None
    assert result.data == {}


def test_mcp_caller_identity_stamped_on_every_result(alice_door):
    """Every tool result carries caller_id and caller_group."""
    for result in [
        alice_door.get_claim("CLM-2024-1800"),
        alice_door.get_documents("CLM-2024-1800"),
    ]:
        assert result.caller_id == "alice"
        assert result.caller_group == "adjuster"


def test_mcp_no_jwt_in_context_raises():
    """Calling MCP tool without setting JWT context raises RuntimeError."""
    door = GovernedMCPDoor()
    with pytest.raises(RuntimeError, match="No JWT in context"):
        door.get_claim("CLM-2024-1800")
