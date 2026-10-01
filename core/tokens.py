"""Delegated customer tokens (ADR-0003).

The agent service mints a short-lived token after OTP verification; the MCP server accepts tool calls only with a
valid token and scopes every query to its `sub`. The model never sees the token, and no tool takes a customer ID.
"""
from __future__ import annotations

import time
import uuid

import jwt

AUDIENCE = "insureassist-mcp"
ISSUER = "insureassist-agent"
SCOPE = "self-service"


class TokenError(Exception):
    pass


def mint(customer_ref: str, msisdn: str, secret: str, minutes: int = 15, now: float | None = None) -> str:
    issued = int(now if now is not None else time.time())
    return jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": customer_ref, "msisdn": msisdn, "scope": SCOPE,
                       "iat": issued, "exp": issued + minutes * 60, "jti": uuid.uuid4().hex}, secret, algorithm="HS256")


def verify(token: str | None, secret: str) -> dict:
    """Returns the claims or raises TokenError (missing, bad signature, wrong audience/issuer/scope, expired)."""
    if not token:
        raise TokenError("missing delegated token")
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "sub", "aud", "iss"]})
    except jwt.PyJWTError as e:
        raise TokenError(str(e)) from e
    if claims.get("scope") != SCOPE:
        raise TokenError("wrong scope")
    return claims
