from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from jwt.exceptions import InvalidTokenError
import requests

from .storage import connect, utc_now


JWT_ISSUER = "rbi-regulatory-intelligence"
JWT_COOKIE_NAME = "rbi_access_token"
JWT_COOKIE_PATH = "/api"
JWT_ALGORITHM = "HS256"
GOOGLE_STATE_COOKIE = "rbi_google_oauth_state"
GOOGLE_VERIFIER_COOKIE = "rbi_google_oauth_verifier"
GOOGLE_COOKIE_PATH = "/api/auth"
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_FLOW_MAX_AGE = 300
PASSWORD_HASH_N = 2**14
PASSWORD_HASH_R = 8
PASSWORD_HASH_P = 1
PASSWORD_HASH_LENGTH = 64
logger = logging.getLogger(__name__)


class AuthConfigurationError(RuntimeError):
    pass


router = APIRouter(prefix="/api/auth", tags=["authentication"])


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    local, separator, domain = normalized.partition("@")
    if (
        not separator
        or not local
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or any(character.isspace() for character in normalized)
    ):
        raise ValueError("Enter a valid email address.")
    return normalized


class RegistrationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name is required.")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email(value)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return normalize_email(value)


def _public_user(row: Any) -> dict[str, str]:
    return {"id": row["id"], "name": row["name"], "email": row["email"]}


def set_auth_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        key=JWT_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=jwt_cookie_secure(),
        samesite=jwt_cookie_samesite(),
        path=JWT_COOKIE_PATH,
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=JWT_COOKIE_NAME,
        httponly=True,
        secure=jwt_cookie_secure(),
        samesite=jwt_cookie_samesite(),
        path=JWT_COOKIE_PATH,
    )


def google_oauth_is_configured() -> bool:
    if not all(
        os.getenv(name, "").strip()
        for name in (
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_REDIRECT_URI",
        )
    ):
        return False
    try:
        _google_redirect_uri()
    except HTTPException:
        return False
    return True


def _google_redirect_uri() -> str:
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    try:
        parsed = urlsplit(redirect_uri)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in requires a valid secure redirect URI.",
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.path != "/api/auth/google/callback"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (
            parsed.scheme != "https"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        )
    ):
        raise HTTPException(
            status_code=503,
            detail="Google sign-in requires a valid secure redirect URI.",
        )
    return redirect_uri


def _frontend_url() -> str:
    frontend_url = os.getenv("RBI_FRONTEND_URL", "http://127.0.0.1:5173").strip()
    try:
        parsed = urlsplit(frontend_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Google sign-in frontend URL must use HTTPS.",
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (
            parsed.scheme != "https"
            and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        )
    ):
        raise HTTPException(
            status_code=503,
            detail="Google sign-in frontend URL must use HTTPS.",
        )
    return frontend_url.rstrip("/")


def _oauth_cookie(response: Response, key: str, value: str) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=GOOGLE_FLOW_MAX_AGE,
        httponly=True,
        secure=jwt_cookie_secure(),
        samesite=jwt_cookie_samesite(),
        path=GOOGLE_COOKIE_PATH,
    )


def _clear_google_oauth_cookies(response: Response) -> None:
    for key in (GOOGLE_STATE_COOKIE, GOOGLE_VERIFIER_COOKIE):
        response.delete_cookie(
            key=key,
            httponly=True,
            secure=jwt_cookie_secure(),
            samesite=jwt_cookie_samesite(),
            path=GOOGLE_COOKIE_PATH,
        )


def _google_callback_redirect(error: str | None = None) -> RedirectResponse:
    url = f"{_frontend_url()}/login"
    if error:
        url += f"?google_error={error}"
    response = RedirectResponse(url, status_code=303)
    _clear_google_oauth_cookies(response)
    return response


def _google_userinfo(code: str, verifier: str) -> dict[str, Any]:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = _google_redirect_uri()
    try:
        token_response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
            timeout=(3, 10),
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = (
            token_data.get("access_token")
            if isinstance(token_data, dict)
            else None
        )
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Google did not return an access token.")
        profile_response = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=(3, 10),
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "Google sign-in profile request failed (%s).",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="Google sign-in could not be completed. Please try again.",
        ) from exc

    if (
        not isinstance(profile, dict)
        or not isinstance(profile.get("sub"), str)
        or not profile["sub"]
        or profile.get("email_verified") is not True
        or not isinstance(profile.get("email"), str)
        or len(profile["email"]) > 254
    ):
        raise HTTPException(
            status_code=401,
            detail="Google did not provide a verified email address.",
        )
    try:
        profile["email"] = normalize_email(profile["email"])
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail="Google did not provide a valid email address.",
        ) from exc
    return profile


def _google_account(profile: dict[str, Any]) -> dict[str, str]:
    google_sub = profile["sub"]
    email = profile["email"]
    name = str(profile.get("name") or email.partition("@")[0]).strip()[:120]
    name = name or email.partition("@")[0]
    with connect() as connection:
        row = connection.execute(
            "SELECT id, name, email, google_sub FROM users WHERE google_sub = ?",
            (google_sub,),
        ).fetchone()
        if row:
            connection.execute(
                "UPDATE users SET name = ?, email = ? WHERE id = ?",
                (name, email, row["id"]),
            )
        else:
            row = connection.execute(
                "SELECT id, name, email, google_sub FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            if row and row["google_sub"] and row["google_sub"] != google_sub:
                raise HTTPException(
                    status_code=409,
                    detail="This email address is linked to a different Google account.",
                )
            if row:
                connection.execute(
                    "UPDATE users SET name = ?, google_sub = ? WHERE id = ?",
                    (name, google_sub, row["id"]),
                )
            else:
                user_id = secrets.token_urlsafe(24)
                connection.execute(
                    "INSERT INTO users "
                    "(id, name, email, password_hash, created_at, google_sub) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        name,
                        email,
                        hash_password(secrets.token_urlsafe(48)),
                        utc_now(),
                        google_sub,
                    ),
                )
                row = connection.execute(
                    "SELECT id, name, email FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
    return _public_user(row)


def token_from_request(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.casefold() == "bearer" and credentials.strip():
        return credentials.strip()
    return request.cookies.get(JWT_COOKIE_NAME)


def user_from_request(request: Request) -> dict[str, str]:
    token = token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    try:
        payload = decode_access_token(token)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Authentication token is invalid or expired.") from exc
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication token is invalid or expired.")
    with connect() as connection:
        row = connection.execute(
            "SELECT id, name, email FROM users WHERE id = ?", (payload["sub"],)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Authentication token is invalid or expired.")
    return _public_user(row)


@router.post("/register", status_code=201)
def register(request: RegistrationRequest, response: Response) -> dict[str, Any]:
    user_id = secrets.token_urlsafe(24)
    try:
        token, max_age = create_access_token(user_id)
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        with connect() as connection:
            connection.execute(
                "INSERT INTO users (id, name, email, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    user_id,
                    request.name,
                    request.email,
                    hash_password(request.password),
                    utc_now(),
                ),
            )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed: users.email" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="An account with this email address already exists.",
            ) from exc
        raise
    set_auth_cookie(response, token, max_age)
    return {
        "user": {"id": user_id, "name": request.name, "email": request.email},
        "authenticated": True,
    }


@router.post("/login")
def login(request: LoginRequest, response: Response) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, name, email, password_hash FROM users WHERE email = ?",
            (request.email,),
        ).fetchone()
    if not row or not verify_password(request.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    try:
        token, max_age = create_access_token(row["id"])
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    set_auth_cookie(response, token, max_age)
    return {"user": _public_user(row), "authenticated": True}


@router.get("/providers")
def auth_providers() -> dict[str, bool]:
    return {"google": google_oauth_is_configured()}


@router.get("/google")
def google_login() -> RedirectResponse:
    if not google_oauth_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Google sign-in is not configured on this server.",
        )
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    challenge = challenge.rstrip(b"=").decode("ascii")
    response = RedirectResponse(
        f"{GOOGLE_AUTHORIZATION_URL}?"
        + urlencode(
            {
                "client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(),
                "redirect_uri": _google_redirect_uri(),
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        ),
        status_code=303,
    )
    _oauth_cookie(response, GOOGLE_STATE_COOKIE, state)
    _oauth_cookie(response, GOOGLE_VERIFIER_COOKIE, verifier)
    return response


@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    expected_state = request.cookies.get(GOOGLE_STATE_COOKIE, "")
    verifier = request.cookies.get(GOOGLE_VERIFIER_COOKIE, "")
    if (
        not state
        or not expected_state
        or not hmac.compare_digest(state, expected_state)
        or not verifier
    ):
        return _google_callback_redirect("invalid_state")
    if error or not code:
        return _google_callback_redirect("cancelled")
    if not auth_is_configured():
        return _google_callback_redirect("server_configuration")

    try:
        profile = _google_userinfo(code, verifier)
        user = _google_account(profile)
        token, max_age = create_access_token(user["id"])
    except HTTPException as exc:
        if exc.status_code == 409:
            return _google_callback_redirect("account_conflict")
        if exc.status_code == 401:
            return _google_callback_redirect("unverified_email")
        if exc.status_code == 503:
            return _google_callback_redirect("server_configuration")
        if exc.status_code == 502:
            return _google_callback_redirect("provider_error")
        raise
    except sqlite3.IntegrityError:
        logger.exception("Google sign-in account could not be saved.")
        return _google_callback_redirect("account_conflict")

    response = RedirectResponse(_frontend_url() + "/", status_code=303)
    _clear_google_oauth_cookies(response)
    set_auth_cookie(response, token, max_age)
    return response


@router.get("/me")
def current_user(request: Request) -> dict[str, Any]:
    return {"user": user_from_request(request)}


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    clear_auth_cookie(response)
    return {"status": "signed_out"}


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY", "").strip()
    if len(secret.encode("utf-8")) < 32:
        raise AuthConfigurationError(
            "JWT_SECRET_KEY must contain at least 32 bytes. Generate a random key "
            "and set it in the server-side .env file."
        )
    return secret


def token_lifetime_minutes() -> int:
    try:
        minutes = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
    except ValueError as exc:
        raise AuthConfigurationError(
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be a whole number."
        ) from exc
    if not 5 <= minutes <= 1440:
        raise AuthConfigurationError(
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be between 5 and 1440."
        )
    return minutes


def jwt_cookie_secure() -> bool:
    return os.getenv("JWT_COOKIE_SECURE", "1").strip().lower() in {
        "1", "true", "yes", "on",
    }


def jwt_cookie_samesite() -> str:
    value = os.getenv("JWT_COOKIE_SAMESITE", "").strip().lower()
    if value in {"lax", "strict", "none"}:
        return value
    return "none" if jwt_cookie_secure() else "lax"


def auth_is_configured() -> bool:
    try:
        _jwt_secret()
        token_lifetime_minutes()
    except AuthConfigurationError:
        return False
    return True


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    password_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_HASH_N,
        r=PASSWORD_HASH_R,
        p=PASSWORD_HASH_P,
        dklen=PASSWORD_HASH_LENGTH,
    )
    encode = lambda value: base64.urlsafe_b64encode(value).decode("ascii")
    return (
        f"scrypt${PASSWORD_HASH_N}${PASSWORD_HASH_R}${PASSWORD_HASH_P}"
        f"${encode(salt)}${encode(password_hash)}"
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, n_value, r_value, p_value, salt_value, digest_value = (
            password_hash.split("$")
        )
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_value), int(r_value), int(p_value)
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_access_token(user_id: str) -> tuple[str, int]:
    lifetime = token_lifetime_minutes()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": user_id,
            "iss": JWT_ISSUER,
            "iat": now,
            "exp": now + timedelta(minutes=lifetime),
        },
        _jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )
    return token, lifetime * 60


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except (InvalidTokenError, AuthConfigurationError):
        raise
    if not isinstance(payload.get("sub"), str):
        return None
    issued_at = payload.get("iat")
    if not isinstance(issued_at, (int, float)):
        raise InvalidTokenError("Access token has an invalid issue time.")
    if datetime.now(timezone.utc).timestamp() >= (
        issued_at + token_lifetime_minutes() * 60
    ):
        raise jwt.ExpiredSignatureError("Access token has expired.")
    return payload
