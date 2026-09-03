from __future__ import annotations

import hmac

from fastapi import Request


SESSION_KEY = "prorok_authenticated"


def is_authenticated(request: Request) -> bool:
    return request.session.get(SESSION_KEY) is True


def verify_password(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate, expected)


def sign_in(request: Request) -> None:
    request.session.clear()
    request.session[SESSION_KEY] = True


def sign_out(request: Request) -> None:
    request.session.clear()
