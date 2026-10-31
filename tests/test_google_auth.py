import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth, storage


class GoogleOAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(self.temp_dir.name) / "auth.sqlite3"
        storage.initialize_storage()
        self.environment = patch.dict(
            os.environ,
            {
                "JWT_SECRET_KEY": "test-key-with-at-least-thirty-two-bytes",
                "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "60",
                "JWT_COOKIE_SECURE": "0",
                "GOOGLE_CLIENT_ID": "google-client-id",
                "GOOGLE_CLIENT_SECRET": "google-client-secret",
                "GOOGLE_REDIRECT_URI": (
                    "http://127.0.0.1:8000/api/auth/google/callback"
                ),
                "RBI_FRONTEND_URL": "http://127.0.0.1:5173",
            },
        )
        self.environment.start()
        app = FastAPI()
        app.include_router(auth.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.environment.stop()
        storage.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def _begin_google_flow(self):
        response = self.client.get("/api/auth/google", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        return query["state"][0]

    def _complete_google_flow(self, state, email="person@example.com"):
        token_response = Mock()
        token_response.json.return_value = {"access_token": "google-access-token"}
        profile_response = Mock()
        profile_response.json.return_value = {
            "sub": "google-subject",
            "email": email,
            "email_verified": True,
            "name": "Google Person",
        }
        with (
            patch("backend.auth.requests.post", return_value=token_response) as post,
            patch("backend.auth.requests.get", return_value=profile_response),
        ):
            response = self.client.get(
                "/api/auth/google/callback",
                params={"code": "one-time-code", "state": state},
                follow_redirects=False,
            )
        post.assert_called_once()
        self.assertEqual(
            post.call_args.kwargs["data"]["redirect_uri"],
            os.environ["GOOGLE_REDIRECT_URI"],
        )
        return response

    def test_google_sign_in_creates_verified_account_and_session(self):
        state = self._begin_google_flow()
        response = self._complete_google_flow(state)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "http://127.0.0.1:5173/")
        self.assertIn("rbi_access_token", response.cookies)
        current_user = self.client.get("/api/auth/me")
        self.assertEqual(
            current_user.json()["user"]["email"],
            "person@example.com",
        )
        with storage.connect() as connection:
            row = connection.execute(
                "SELECT google_sub FROM users WHERE email = ?",
                ("person@example.com",),
            ).fetchone()
        self.assertEqual(row["google_sub"], "google-subject")

    def test_access_token_session_expires_after_fifteen_minutes(self):
        with patch.dict(os.environ, {"JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15"}):
            token, max_age = auth.create_access_token("test-user")
            payload = auth.decode_access_token(token)

        self.assertEqual(max_age, 15 * 60)
        self.assertEqual(payload["exp"] - payload["iat"], 15 * 60)

    def test_validation_expires_older_tokens_after_fifteen_minutes(self):
        now = datetime.now(timezone.utc)
        issued_at = now - timedelta(minutes=16)
        token = auth.jwt.encode(
            {
                "sub": "existing-user",
                "iss": auth.JWT_ISSUER,
                "iat": issued_at,
                "exp": issued_at + timedelta(minutes=60),
            },
            os.environ["JWT_SECRET_KEY"],
            algorithm=auth.JWT_ALGORITHM,
        )

        with (
            patch.dict(os.environ, {"JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15"}),
            self.assertRaises(auth.jwt.ExpiredSignatureError),
        ):
            auth.decode_access_token(token)

    def test_google_sign_in_links_existing_verified_email_account(self):
        with storage.connect() as connection:
            connection.execute(
                "INSERT INTO users (id, name, email, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "existing-user",
                    "Existing Name",
                    "person@example.com",
                    auth.hash_password("existing-password"),
                    storage.utc_now(),
                ),
            )

        response = self._complete_google_flow(self._begin_google_flow())
        self.assertEqual(response.status_code, 303)
        with storage.connect() as connection:
            row = connection.execute(
                "SELECT id, google_sub FROM users WHERE email = ?",
                ("person@example.com",),
            ).fetchone()
        self.assertEqual(row["id"], "existing-user")
        self.assertEqual(row["google_sub"], "google-subject")

    def test_google_callback_rejects_mismatched_state_without_token_exchange(self):
        self._begin_google_flow()
        with patch("backend.auth.requests.post") as post:
            response = self.client.get(
                "/api/auth/google/callback",
                params={"code": "one-time-code", "state": "not-the-state"},
                follow_redirects=False,
            )
        post.assert_not_called()
        self.assertIn("google_error=invalid_state", response.headers["location"])

    def test_google_callback_rejects_unverified_email(self):
        state = self._begin_google_flow()
        token_response = Mock()
        token_response.json.return_value = {"access_token": "google-access-token"}
        profile_response = Mock()
        profile_response.json.return_value = {
            "sub": "google-subject",
            "email": "person@example.com",
            "email_verified": False,
            "name": "Google Person",
        }
        with (
            patch("backend.auth.requests.post", return_value=token_response),
            patch("backend.auth.requests.get", return_value=profile_response),
        ):
            response = self.client.get(
                "/api/auth/google/callback",
                params={"code": "one-time-code", "state": state},
                follow_redirects=False,
            )
        self.assertIn("google_error=unverified_email", response.headers["location"])
        with storage.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
