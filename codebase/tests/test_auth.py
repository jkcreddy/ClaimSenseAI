"""
Tests for the JWT auth layer — POST /auth/login and GET /auth/me.

Covers:
  - successful login for every Cognito group
  - token shape (sub, cognito:groups, exp, token_use)
  - /auth/me returns correct identity from bearer token
  - wrong password → 401
  - unknown user → 401
  - missing Authorization header → 401
  - tampered token → 401
  - expired token → 401
"""
import time
import pytest
import jwt
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.config import JWT_SECRET, JWT_ALGORITHM

client = TestClient(app, raise_server_exceptions=True)


# ── helpers ───────────────────────────────────────────────────────────────────

def login(username: str, password: str):
    return client.post("/auth/login", json={"username": username, "password": password})


def decode(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ── login success ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("username,password,expected_group", [
    ("alice", "alice123",  "adjuster"),
    ("bob",   "bob123",    "adjuster"),
    ("carol", "carol123",  "senior-adjuster"),
    ("david", "david123",  "fraud-investigator"),
    ("eve",   "eve123",    "total-loss-appraiser"),
])
def test_login_success(username, password, expected_group):
    r = login(username, password)
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    assert body["token_type"] == "Bearer"
    assert body["user_id"] == username
    assert body["group"] == expected_group
    assert body["expires_in"] > 0


# ── JWT payload shape ─────────────────────────────────────────────────────────

def test_token_payload_shape():
    token = login("carol", "carol123").json()["access_token"]
    payload = decode(token)

    assert payload["sub"] == "carol"
    assert payload["cognito:groups"] == ["senior-adjuster"]
    assert payload["token_use"] == "access"
    assert "iat" in payload
    assert "exp" in payload
    assert payload["exp"] > payload["iat"]


def test_token_expiry_is_in_future():
    token = login("alice", "alice123").json()["access_token"]
    payload = decode(token)
    assert payload["exp"] > int(time.time())


# ── /auth/me ──────────────────────────────────────────────────────────────────

def test_me_returns_identity():
    token = login("david", "david123").json()["access_token"]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "david"
    assert body["group"] == "fraud-investigator"


@pytest.mark.parametrize("username,group", [
    ("alice", "adjuster"),
    ("carol", "senior-adjuster"),
    ("eve",   "total-loss-appraiser"),
])
def test_me_group_matches_login(username, group):
    token = login(username, f"{username}123").json()["access_token"]
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["group"] == group


# ── failure cases ─────────────────────────────────────────────────────────────

def test_wrong_password_returns_401():
    r = login("alice", "wrongpassword")
    assert r.status_code == 401


def test_unknown_user_returns_401():
    r = login("nobody", "pass")
    assert r.status_code == 401


def test_missing_auth_header_returns_401():
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_tampered_token_returns_401():
    token = login("alice", "alice123").json()["access_token"]
    tampered = token[:-6] + "XXXXXX"
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert r.status_code == 401


def test_expired_token_returns_401():
    now = int(time.time())
    expired_token = jwt.encode(
        {"sub": "alice", "cognito:groups": ["adjuster"], "iat": now - 7200, "exp": now - 3600, "token_use": "access"},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert r.status_code == 401


def test_token_signed_with_wrong_secret_returns_401():
    bad_token = jwt.encode(
        {"sub": "alice", "cognito:groups": ["adjuster"], "exp": int(time.time()) + 3600, "token_use": "access"},
        "wrong-secret",
        algorithm=JWT_ALGORITHM,
    )
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {bad_token}"})
    assert r.status_code == 401


def test_token_missing_group_claim_returns_401():
    token = jwt.encode(
        {"sub": "alice", "exp": int(time.time()) + 3600, "token_use": "access"},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
