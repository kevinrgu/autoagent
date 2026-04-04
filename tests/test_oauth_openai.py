import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from oauth_openai import CodexOAuthManager, CodexOAuthError


def make_jwt(*, exp: int | None = None, extra_claims: dict | None = None) -> str:
    header = {"alg": "none", "typ": "JWT"}
    payload = extra_claims.copy() if extra_claims else {}
    if exp is not None:
        payload["exp"] = exp

    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode(header)}.{encode(payload)}.signature"


def write_auth_json(path, *, access_token: str, refresh_token: str = "refresh-token", account_id: str = "workspace-1", auth_mode: str = "chatgpt"):
    path.write_text(
        json.dumps(
            {
                "auth_mode": auth_mode,
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "account_id": account_id,
                    "id_token": access_token,
                },
                "last_refresh": "2026-01-01T00:00:00Z",
            }
        )
    )


def test_loads_headers_from_codex_auth_file(tmp_path):
    token = make_jwt(exp=int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()))
    auth_path = tmp_path / "auth.json"
    write_auth_json(auth_path, access_token=token)

    manager = CodexOAuthManager(auth_path=auth_path)

    headers = manager.get_default_headers()

    assert headers == {"ChatGPT-Account-ID": "workspace-1"}


def test_token_expired_uses_jwt_expiration(tmp_path):
    expired = make_jwt(exp=int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()))
    fresh = make_jwt(exp=int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()))
    auth_path = tmp_path / "auth.json"
    write_auth_json(auth_path, access_token=fresh)
    manager = CodexOAuthManager(auth_path=auth_path)

    assert manager.token_expired(expired) is True
    assert manager.token_expired(fresh) is False


@pytest.mark.asyncio
async def test_refreshes_expired_token_and_persists_new_tokens(tmp_path, monkeypatch):
    expired = make_jwt(exp=int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()))
    fresh = make_jwt(exp=int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp()))
    auth_path = tmp_path / "auth.json"
    write_auth_json(auth_path, access_token=expired, refresh_token="old-refresh")

    manager = CodexOAuthManager(auth_path=auth_path)

    async def fake_refresh(refresh_token: str):
        assert refresh_token == "old-refresh"
        return {
            "access_token": fresh,
            "refresh_token": "new-refresh",
            "id_token": fresh,
        }

    monkeypatch.setattr(manager, "_request_refresh", fake_refresh)

    token = await manager.get_access_token()

    persisted = json.loads(auth_path.read_text())
    assert token == fresh
    assert persisted["tokens"]["access_token"] == fresh
    assert persisted["tokens"]["refresh_token"] == "new-refresh"


def test_rejects_non_chatgpt_auth_file(tmp_path):
    token = make_jwt(exp=int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()))
    auth_path = tmp_path / "auth.json"
    write_auth_json(auth_path, access_token=token, auth_mode="api_key")

    manager = CodexOAuthManager(auth_path=auth_path)

    with pytest.raises(CodexOAuthError):
        manager.load_auth_state()


def test_malformed_auth_json_raises_codex_oauth_error(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text("not-json")

    manager = CodexOAuthManager(auth_path=auth_path)

    with pytest.raises(CodexOAuthError):
        manager.load_auth_state()


@pytest.mark.asyncio
async def test_refresh_wraps_network_errors(tmp_path, monkeypatch):
    expired = make_jwt(exp=int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()))
    auth_path = tmp_path / "auth.json"
    write_auth_json(auth_path, access_token=expired)
    manager = CodexOAuthManager(auth_path=auth_path, refresh_url="https://example.invalid/oauth/token")

    async def boom(refresh_token: str):
        raise RuntimeError("network down")

    monkeypatch.setattr(manager, "_request_refresh", boom)

    with pytest.raises(CodexOAuthError):
        await manager.get_access_token()
