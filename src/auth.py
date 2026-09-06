from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email_normalized: str
    display_name: str | None


def _required_text_claim(
    claims: Mapping[str, Any],
    claim_name: str,
) -> str:
    value = claims.get(claim_name)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Missing or invalid Google claim: {claim_name}"
        )

    return value.strip()


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()

    if (
        normalized.count("@") != 1
        or normalized.startswith("@")
        or normalized.endswith("@")
    ):
        raise ValueError("Invalid Google email address")

    return normalized


def parse_google_identity(
    claims: Mapping[str, Any],
) -> GoogleIdentity:
    if claims.get("email_verified") is not True:
        raise ValueError("Google email is not verified")

    subject = _required_text_claim(claims, "sub")
    email = _required_text_claim(claims, "email")

    display_name_value = claims.get("name")
    display_name = (
        display_name_value.strip()
        if isinstance(display_name_value, str)
        and display_name_value.strip()
        else None
    )

    return GoogleIdentity(
        subject=subject,
        email_normalized=normalize_email(email),
        display_name=display_name,
    )