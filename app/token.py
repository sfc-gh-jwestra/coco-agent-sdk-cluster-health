"""JWT token generation and validation for approval links."""

import time

import jwt

from .config import TOKEN_SECRET_KEY, TOKEN_TTL_HOURS


def generate_approval_token(table_name: str, action: str = "recluster") -> str:
    """Generate a signed JWT token for a remediation approval link.

    Args:
        table_name: Fully-qualified table name to recluster.
        action: The remediation action (default: "recluster").

    Returns:
        Encoded JWT string.
    """
    payload = {
        "table_name": table_name,
        "action": action,
        "iat": int(time.time()),
        "exp": int(time.time()) + (TOKEN_TTL_HOURS * 3600),
    }
    return jwt.encode(payload, TOKEN_SECRET_KEY, algorithm="HS256")


def validate_approval_token(token: str) -> dict:
    """Validate and decode an approval token.

    Args:
        token: The JWT token string from the approval link.

    Returns:
        Dict with 'table_name' and 'action' keys.

    Raises:
        jwt.ExpiredSignatureError: If the token has expired.
        jwt.InvalidTokenError: If the token is invalid.
    """
    payload = jwt.decode(token, TOKEN_SECRET_KEY, algorithms=["HS256"])
    return {
        "table_name": payload["table_name"],
        "action": payload["action"],
    }
