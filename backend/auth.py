from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from jwt.exceptions import InvalidTokenError

from .storage import connect, utc_now


JWT_ISSUER = "rbi-regulatory-intelligence"
JWT_COOKIE_NAME = "rbi_access_token"
JWT_COOKIE_PATH = "/api"
JWT_ALGORITHM = "HS256"
PASSWORD_HASH_N = 2**14
PASSWORD_HASH_R = 8
PASSWORD_HASH_P = 1
PASSWORD_HASH_LENGTH = 64


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
        samesite="lax",
        path=JWT_COOKIE_PATH,
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=JWT_COOKIE_NAME,
        httponly=True,
        secure=jwt_cookie_secure(),
        samesite="lax",
        path=JWT_COOKIE_PATH,
    )


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
        minutes = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
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
    return os.getenv("JWT_COOKIE_SECURE", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


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
    return payload
