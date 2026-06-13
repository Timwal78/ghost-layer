"""GHOST test suite — lifecycle, scope, expiry, crypto, tamper, proxy."""

from __future__ import annotations

import time

import pytest

from ghost import crypto, session as gsession
from ghost.proxy import possess
from ghost.session import ExpiredSessionError, ScopeError, SessionError
from ghost.store import ResidueStore


@pytest.fixture()
def store(tmp_path):
    s = ResidueStore(tmp_path / "residue.db")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    # Point ephemeral key storage at the per-test tmp dir.
    sdir = tmp_path / "sessions"
    monkeypatch.setattr(gsession, "SESSIONS_DIR", sdir)
    return sdir


# ---- crypto ---------------------------------------------------------------
def test_sign_and_verify_roundtrip():
    seed, pub = crypto.generate_keypair()
    sig = crypto.sign(seed, b"hello-ghost")
    assert crypto.verify(pub, b"hello-ghost", sig) is True
    assert crypto.verify(pub, b"tampered", sig) is False


def test_canonical_hash_is_order_independent():
    a = crypto.canonical_hash({"x": 1, "y": 2})
    b = crypto.canonical_hash({"y": 2, "x": 1})
    assert a == b


# ---- spawn ----------------------------------------------------------------
def test_spawn_creates_session_and_key(store, _isolate_sessions):
    res = gsession.spawn(store, intent="deploy_staging", ttl=60, scopes=["aws_ec2"])
    assert res["session_id"].startswith("gh_")
    assert res["scopes"] == ["aws_ec2"]
    key = _isolate_sessions / res["session_id"] / "ed25519_seed"
    assert key.exists()
    assert oct(key.stat().st_mode)[-3:] == "600"


# ---- act + scope ----------------------------------------------------------
def test_act_within_scope_succeeds(store):
    s = gsession.spawn(store, intent="i", ttl=60, scopes=["aws_ec2"])
    a = gsession.act(store, s["session_id"], tool="aws_ec2", action="RunInstances",
                     params={"count": 1})
    assert a["seq"] == 1
    assert a["tool"] == "aws_ec2"
    assert a["signature"]


def test_act_out_of_scope_blocked(store):
    s = gsession.spawn(store, intent="i", ttl=60, scopes=["aws_ec2"])
    with pytest.raises(ScopeError):
        gsession.act(store, s["session_id"], tool="stripe", action="Charge")


def test_act_no_scopes_allows_any(store):
    s = gsession.spawn(store, intent="i", ttl=60, scopes=[])
    a = gsession.act(store, s["session_id"], tool="anything", action="go")
    assert a["seq"] == 1


def test_act_increments_seq(store):
    s = gsession.spawn(store, intent="i", ttl=60)
    gsession.act(store, s["session_id"], tool="t", action="a1")
    a2 = gsession.act(store, s["session_id"], tool="t", action="a2")
    assert a2["seq"] == 2


# ---- expiry ---------------------------------------------------------------
def test_act_after_expiry_blocked(store):
    s = gsession.spawn(store, intent="i", ttl=1)
    time.sleep(1.1)
    with pytest.raises(ExpiredSessionError):
        gsession.act(store, s["session_id"], tool="t", action="a")


# ---- evaporate ------------------------------------------------------------
def test_evaporate_shreds_key_and_signs_chain(store, _isolate_sessions):
    s = gsession.spawn(store, intent="i", ttl=60)
    gsession.act(store, s["session_id"], tool="t", action="a")
    out = gsession.evaporate(store, s["session_id"])
    assert out["status"] == "evaporated"
    assert out["actions_executed"] == 1
    assert out["root_signature"]
    assert not (_isolate_sessions / s["session_id"]).exists()


def test_double_evaporate_errors(store):
    s = gsession.spawn(store, intent="i", ttl=60)
    gsession.evaporate(store, s["session_id"])
    with pytest.raises(SessionError):
        gsession.evaporate(store, s["session_id"])


def test_act_after_evaporate_blocked(store):
    s = gsession.spawn(store, intent="i", ttl=60)
    gsession.evaporate(store, s["session_id"])
    with pytest.raises(ExpiredSessionError):
        gsession.act(store, s["session_id"], tool="t", action="a")


# ---- replay + verification ------------------------------------------------
def test_replay_verifies_clean_session(store):
    s = gsession.spawn(store, intent="i", ttl=60)
    gsession.act(store, s["session_id"], tool="t", action="a1", params={"k": 1})
    gsession.act(store, s["session_id"], tool="t", action="a2", params={"k": 2})
    gsession.evaporate(store, s["session_id"])
    r = gsession.replay(store, s["session_id"])
    assert r["verified"] is True
    assert r["root_verified"] is True
    assert len(r["actions"]) == 2
    assert all(a["verified"] for a in r["actions"])


def test_replay_detects_tampered_action(store):
    s = gsession.spawn(store, intent="i", ttl=60)
    gsession.act(store, s["session_id"], tool="t", action="a1", params={"k": 1})
    gsession.evaporate(store, s["session_id"])
    # Tamper directly with the residue DB.
    store._conn.execute(
        "UPDATE actions SET params_hash = ? WHERE session_id = ?",
        ("deadbeef" * 8, s["session_id"]),
    )
    store._conn.commit()
    r = gsession.replay(store, s["session_id"])
    assert r["verified"] is False
    assert any(a["verified"] is False for a in r["actions"])


def test_replay_missing_session_errors(store):
    with pytest.raises(SessionError):
        gsession.replay(store, "gh_does_not_exist")


# ---- proxy ----------------------------------------------------------------
class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def test_proxy_strips_auth_and_injects_token(store):
    s = gsession.spawn(store, intent="i", ttl=60, scopes=["aws_ec2"])
    captured = {}

    def transport(method, url, headers=None, **kw):
        captured["headers"] = headers
        return _FakeResp(200, {"InstanceId": "i-123"})

    p = possess(store, s["session_id"], transport, token="ghtok_abc")
    resp = p.request("POST", "https://ec2.amazonaws.com", tool="aws_ec2",
                     action="RunInstances", params={"count": 1},
                     headers={"Authorization": "Bearer SECRET"})
    assert resp.status_code == 200
    assert "Authorization" not in captured["headers"]
    assert captured["headers"]["X-Ghost-Token"] == "ghtok_abc"
    assert captured["headers"]["X-Ghost-Session"] == s["session_id"]


def test_proxy_records_request_and_response_residue(store):
    s = gsession.spawn(store, intent="i", ttl=60, scopes=["aws_ec2"])

    def transport(method, url, headers=None, **kw):
        return _FakeResp(201, {"ok": True})

    p = possess(store, s["session_id"], transport, token="t")
    p.request("POST", "https://x", tool="aws_ec2", action="RunInstances")
    # one request entry + one response entry
    assert store.count_actions(s["session_id"]) == 2


def test_proxy_get_does_not_record(store):
    s = gsession.spawn(store, intent="i", ttl=60)

    def transport(method, url, headers=None, **kw):
        return _FakeResp(200, {})

    p = possess(store, s["session_id"], transport, token="t")
    p.request("GET", "https://x", tool="t", action="read")
    assert store.count_actions(s["session_id"]) == 0
