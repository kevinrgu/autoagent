from __future__ import annotations

import base64
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None

try:  # pragma: no cover - only exercised on Windows
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows fallback
    msvcrt = None


REFRESH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
SUPPORTED_AUTH_MODES = {"chatgpt", "chatgptauthtokens", "chatgpt_auth_tokens"}


class CodexOAuthError(RuntimeError):
    """Raised when Codex/OpenAI OAuth state cannot be used."""


@dataclass
class CodexAuthState:
    auth_mode: str
    access_token: str
    refresh_token: str
    account_id: str | None
    id_token: str | None
    raw: dict[str, Any]


class CodexOAuthManager:
    def __init__(
        self,
        auth_path: str | Path | None = None,
        *,
        refresh_url: str = REFRESH_TOKEN_URL,
        client_id: str = CLIENT_ID,
        refresh_skew_seconds: int = 300,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.auth_path = Path(auth_path) if auth_path else self.default_auth_path()
        self.refresh_url = refresh_url
        self.client_id = client_id
        self.refresh_skew_seconds = refresh_skew_seconds
        self.timeout_seconds = timeout_seconds
        self.lock_path = self.auth_path.with_suffix(self.auth_path.suffix + ".lock")

    @staticmethod
    def default_auth_path() -> Path:
        if os.environ.get("CODEX_AUTH_JSON"):
            return Path(os.environ["CODEX_AUTH_JSON"]).expanduser()
        codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        return codex_home / "auth.json"

    def load_auth_state(self) -> CodexAuthState:
        data = self._read_json()
        auth_mode = str(data.get("auth_mode") or "").strip().lower()
        if auth_mode not in SUPPORTED_AUTH_MODES:
            raise CodexOAuthError(
                f"Expected Codex ChatGPT auth at {self.auth_path}, found auth_mode={auth_mode!r}."
            )

        tokens = data.get("tokens") or {}
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not access_token or not refresh_token:
            raise CodexOAuthError(f"Codex auth at {self.auth_path} is missing OAuth tokens.")

        account_id = tokens.get("account_id") or self._extract_account_id(access_token)
        id_token = tokens.get("id_token")
        return CodexAuthState(
            auth_mode=auth_mode,
            access_token=access_token,
            refresh_token=refresh_token,
            account_id=account_id,
            id_token=id_token,
            raw=data,
        )

    def get_default_headers(self) -> dict[str, str]:
        state = self.load_auth_state()
        headers: dict[str, str] = {}
        if state.account_id:
            headers["ChatGPT-Account-ID"] = state.account_id
        return headers

    def token_expired(self, token: str) -> bool:
        claims = self._decode_jwt_payload(token)
        exp = claims.get("exp")
        if not exp:
            return False
        now = datetime.now(timezone.utc).timestamp()
        return now >= float(exp) - self.refresh_skew_seconds

    async def get_access_token(self) -> str:
        state = self.load_auth_state()
        if not self.token_expired(state.access_token):
            return state.access_token
        try:
            refreshed = await self._refresh_access_token()
        except CodexOAuthError:
            raise
        except Exception as exc:
            raise CodexOAuthError(f"OAuth token refresh failed: {exc}") from exc
        return refreshed.access_token

    async def _refresh_access_token(self) -> CodexAuthState:
        with self._lock_file():
            state = self.load_auth_state()
            if not self.token_expired(state.access_token):
                return state

            response_payload = await self._request_refresh(state.refresh_token)
            updated = self._merge_refresh_payload(state.raw, response_payload)
            self._write_json(updated)
            return self.load_auth_state()

    async def _request_refresh(self, refresh_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.refresh_url,
                json={
                    "client_id": self.client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/json"},
            )

        if response.is_success:
            payload = response.json()
            if not payload.get("access_token"):
                raise CodexOAuthError("OAuth refresh succeeded but did not return an access_token.")
            return payload

        try:
            body = response.json()
        except Exception:
            body = response.text
        raise CodexOAuthError(f"OAuth refresh failed with {response.status_code}: {body}")

    def _merge_refresh_payload(self, auth_data: dict[str, Any], refresh_payload: dict[str, Any]) -> dict[str, Any]:
        updated = json.loads(json.dumps(auth_data))
        tokens = updated.setdefault("tokens", {})
        for key in ("access_token", "refresh_token", "id_token"):
            if refresh_payload.get(key):
                tokens[key] = refresh_payload[key]
        if not tokens.get("account_id"):
            tokens["account_id"] = self._extract_account_id(tokens.get("access_token", ""))
        updated["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return updated

    def _extract_account_id(self, token: str) -> str | None:
        claims = self._decode_jwt_payload(token)
        auth_claims = claims.get("https://api.openai.com/auth") or {}
        return auth_claims.get("chatgpt_account_id")

    def _decode_jwt_payload(self, token: str) -> dict[str, Any]:
        try:
            _header, payload, _signature = token.split(".", 2)
        except ValueError:
            return {}
        payload += "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
            parsed = json.loads(decoded.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _read_json(self) -> dict[str, Any]:
        if not self.auth_path.exists():
            raise CodexOAuthError(
                f"No Codex auth found at {self.auth_path}. Run `codex login` first or set CODEX_HOME/CODEX_AUTH_JSON."
            )
        try:
            return json.loads(self.auth_path.read_text())
        except OSError as exc:
            raise CodexOAuthError(f"Failed to read Codex auth file at {self.auth_path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CodexOAuthError(f"Codex auth file at {self.auth_path} is not valid JSON.") from exc

    def _write_json(self, payload: dict[str, Any]) -> None:
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.auth_path.with_suffix(self.auth_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2))
        os.replace(temp_path, self.auth_path)

    @contextmanager
    def _lock_file(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w") as lock_handle:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - only exercised on Windows
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - only exercised on Windows
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
