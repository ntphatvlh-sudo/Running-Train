from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email_normalized: str
    display_name: str | None

VALID_ACCESS_ROLES = frozenset(
    {
        "OWNER",
        "COACH",
        "RUNNER",
    }
)


@dataclass(frozen=True)
class AthleteAccess:
    app_user_id: int
    athlete_id: int
    full_name: str
    access_role: str


class AuthorizationError(RuntimeError):
    pass


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


def authorize_google_identity(
    connection: Connection,
    identity: GoogleIdentity,
) -> list[AthleteAccess]:
    result = connection.execute(
        text(
            """
            EXEC dbo.usp_AuthorizeGoogleLogin
                @GoogleSubject = :google_subject,
                @EmailNormalized = :email_normalized,
                @DisplayName = :display_name
            """
        ),
        {
            "google_subject": identity.subject,
            "email_normalized": identity.email_normalized,
            "display_name": identity.display_name,
        },
    )

    rows = result.mappings().all()

    if not rows:
        raise AuthorizationError(
            "No active athlete access was returned"
        )

    accesses: list[AthleteAccess] = []
    seen_athlete_ids: set[int] = set()

    for row in rows:
        app_user_id = int(row["AppUserID"])
        athlete_id = int(row["AthleteID"])
        full_name = str(row["FullName"]).strip()
        access_role = str(row["AccessRole"]).strip().upper()

        if not full_name:
            raise AuthorizationError(
                "Athlete access has no display name"
            )

        if access_role not in VALID_ACCESS_ROLES:
            raise AuthorizationError(
                f"Invalid athlete access role: {access_role}"
            )

        if athlete_id in seen_athlete_ids:
            raise AuthorizationError(
                "Duplicate athlete access was returned"
            )

        seen_athlete_ids.add(athlete_id)

        accesses.append(
            AthleteAccess(
                app_user_id=app_user_id,
                athlete_id=athlete_id,
                full_name=full_name,
                access_role=access_role,
            )
        )

    return accesses