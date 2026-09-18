from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.auth import normalize_email


NEW_RUNNER_INVITATION = "NEW_RUNNER"
EXISTING_ATHLETE_INVITATION = "EXISTING_ATHLETE"

VALID_INVITATION_TYPES = frozenset(
    {
        NEW_RUNNER_INVITATION,
        EXISTING_ATHLETE_INVITATION,
    }
)

VALID_INVITATION_ROLES = frozenset(
    {
        "OWNER",
        "COACH",
        "RUNNER",
    }
)


@dataclass(frozen=True)
class InvitationRequest:
    invited_by_app_user_id: int
    email_normalized: str
    access_role: str
    athlete_id: int | None
    expires_at: datetime | None


@dataclass(frozen=True)
class InvitationResult:
    invitation_id: int
    email_normalized: str
    invitation_type: str
    athlete_id: int | None
    athlete_name: str | None
    access_role: str
    status: str
    invited_by_app_user_id: int
    created_at: datetime
    expires_at: datetime | None


class InvitationError(RuntimeError):
    pass

def create_invitation(
    connection: Connection,
    request: InvitationRequest,
) -> InvitationResult:
    try:
        email_normalized = normalize_email(
            request.email_normalized
        )
    except ValueError as exc:
        raise InvitationError(
            "Invitation email is invalid"
        ) from exc

    access_role = request.access_role.strip().upper()

    if request.invited_by_app_user_id <= 0:
        raise InvitationError(
            "Inviting AppUserID is invalid"
        )

    if access_role not in VALID_INVITATION_ROLES:
        raise InvitationError(
            "Invitation access role is invalid"
        )

    if (
        request.athlete_id is None
        and access_role != "RUNNER"
    ):
        raise InvitationError(
            "New-athlete invitation must use RUNNER role"
        )

    if (
        request.athlete_id is not None
        and request.athlete_id <= 0
    ):
        raise InvitationError(
            "Invitation AthleteID is invalid"
        )

    result = connection.execute(
        text(
            """
            SELECT
                invitation_id AS "InvitationID",
                email_normalized AS "EmailNormalized",
                invitation_type AS "InvitationType",
                athlete_id AS "AthleteID",
                athlete_name AS "AthleteName",
                access_role AS "AccessRole",
                status AS "Status",
                invited_by_app_user_id
                    AS "InvitedByAppUserID",
                created_at AS "CreatedAt",
                expires_at AS "ExpiresAt"
            FROM public.create_invitation
            (
                :invited_by_app_user_id,
                :email_normalized,
                :access_role,
                :athlete_id,
                :expires_at
            )
            """
        ),
        {
            "invited_by_app_user_id": (
                request.invited_by_app_user_id
            ),
            "email_normalized": email_normalized,
            "access_role": access_role,
            "athlete_id": request.athlete_id,
            "expires_at": request.expires_at,
        },
    )

    rows = result.mappings().all()

    if len(rows) != 1:
        raise InvitationError(
            "Invitation must return exactly one result"
        )

    row = rows[0]

    try:
        invitation_id = int(row["InvitationID"])
        returned_inviter_id = int(
            row["InvitedByAppUserID"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvitationError(
            "Invitation result contains invalid identifiers"
        ) from exc

    returned_email = str(
        row.get("EmailNormalized") or ""
    ).strip().lower()

    invitation_type = str(
        row.get("InvitationType") or ""
    ).strip().upper()

    returned_role = str(
        row.get("AccessRole") or ""
    ).strip().upper()

    status = str(
        row.get("Status") or ""
    ).strip().upper()

    returned_athlete_id_value = row.get("AthleteID")

    try:
        returned_athlete_id = (
            None
            if returned_athlete_id_value is None
            else int(returned_athlete_id_value)
        )
    except (TypeError, ValueError) as exc:
        raise InvitationError(
            "Invitation result contains invalid AthleteID"
        ) from exc

    athlete_name_value = row.get("AthleteName")

    athlete_name = (
        athlete_name_value.strip()
        if isinstance(athlete_name_value, str)
        and athlete_name_value.strip()
        else None
    )

    created_at = row.get("CreatedAt")
    expires_at = row.get("ExpiresAt")

    if invitation_id <= 0:
        raise InvitationError(
            "Invitation result has an invalid InvitationID"
        )

    if returned_inviter_id != request.invited_by_app_user_id:
        raise InvitationError(
            "Invitation returned a different inviting user"
        )

    if returned_email != email_normalized:
        raise InvitationError(
            "Invitation returned a different email"
        )

    if invitation_type not in VALID_INVITATION_TYPES:
        raise InvitationError(
            "Invitation result has an invalid type"
        )

    expected_type = (
        NEW_RUNNER_INVITATION
        if request.athlete_id is None
        else EXISTING_ATHLETE_INVITATION
    )

    if invitation_type != expected_type:
        raise InvitationError(
            "Invitation returned a different type"
        )

    if returned_athlete_id != request.athlete_id:
        raise InvitationError(
            "Invitation returned a different athlete"
        )

    if (
        returned_athlete_id is None
        and athlete_name is not None
    ):
        raise InvitationError(
            "New-runner invitation returned an athlete name"
        )

    if (
        returned_athlete_id is not None
        and athlete_name is None
    ):
        raise InvitationError(
            "Existing-athlete invitation has no athlete name"
        )

    if returned_role != access_role:
        raise InvitationError(
            "Invitation returned a different access role"
        )

    if status != "PENDING":
        raise InvitationError(
            "Invitation did not return PENDING status"
        )

    if not isinstance(created_at, datetime):
        raise InvitationError(
            "Invitation result has an invalid CreatedAt"
        )

    if (
        expires_at is not None
        and not isinstance(expires_at, datetime)
    ):
        raise InvitationError(
            "Invitation result has an invalid ExpiresAt"
        )

    return InvitationResult(
        invitation_id=invitation_id,
        email_normalized=returned_email,
        invitation_type=invitation_type,
        athlete_id=returned_athlete_id,
        athlete_name=athlete_name,
        access_role=returned_role,
        status=status,
        invited_by_app_user_id=returned_inviter_id,
        created_at=created_at,
        expires_at=expires_at,
    )