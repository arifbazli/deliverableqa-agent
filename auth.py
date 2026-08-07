import os

TOKEN_ENV_VAR = "DELIVERABLEQA_TOKEN"


def get_expected_token() -> str | None:
    """Returns the configured bearer token, or None if auth is disabled.

    Auth is opt-in: with DELIVERABLEQA_TOKEN unset, the server requires no
    credentials at all — the default for local single-user usage on 127.0.0.1.
    Set it to require `Authorization: Bearer <token>` on every /api/* request.
    """
    token = os.environ.get(TOKEN_ENV_VAR)
    return token if token else None
