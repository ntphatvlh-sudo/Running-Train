from dataclasses import dataclass
from datetime import date
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


AUTHORIZED_STATE = "AUTHORIZED"
ONBOARDING_REQUIRED_STATE = "ONBOARDING_REQUIRED"

VALID_AUTHORIZATION_STATES = frozenset(
    {
        AUTHORIZED_STATE,
        ONBOARDING_REQUIRED_STATE,
    }
)


@dataclass(frozen=True)
class AthleteAccess:
    app_user_id: int
    athlete_id: int
    full_name: str
    access_role: str

@dataclass(frozen=True)
class AuthorizationResult:
    app_user_id: int
    authorization_state: str
    accesses: tuple[AthleteAccess, ...]

@dataclass(frozen=True)
class RunnerOnboardingData:
    full_name: str
    date_of_birth: date | None
    sex: str | None
    height_cm: float
    weight_kg: float
    latest_pr_distance_m: int
    latest_pr_completed_duration_sec: int
    latest_pr_achieved_date: date

class AuthorizationError(RuntimeError):
    pass

class OnboardingError(RuntimeError):
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
) -> AuthorizationResult:
    result = connection.execute(
        text(
            """
            SELECT
                app_user_id AS "AppUserID",
                authorization_state AS "AuthorizationState",
                athlete_id AS "AthleteID",
                full_name AS "FullName",
                access_role AS "AccessRole"
            FROM public.authorize_google_login
            (
                :google_subject,
                :email_normalized,
                :display_name
            )
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
            "No authorization result was returned"
        )

    first_row = rows[0]

    try:
        app_user_id = int(first_row["AppUserID"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthorizationError(
            "Authorization result has an invalid AppUserID"
        ) from exc

    authorization_state = str(
        first_row.get("AuthorizationState") or ""
    ).strip().upper()

    if authorization_state not in VALID_AUTHORIZATION_STATES:
        raise AuthorizationError(
            "Authorization result has an invalid state"
        )

    for row in rows:
        try:
            row_app_user_id = int(row["AppUserID"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthorizationError(
                "Authorization result has an invalid AppUserID"
            ) from exc

        row_state = str(
            row.get("AuthorizationState") or ""
        ).strip().upper()

        if row_app_user_id != app_user_id:
            raise AuthorizationError(
                "Authorization result contains multiple users"
            )

        if row_state != authorization_state:
            raise AuthorizationError(
                "Authorization result contains multiple states"
            )

    if authorization_state == ONBOARDING_REQUIRED_STATE:
        if len(rows) != 1:
            raise AuthorizationError(
                "Onboarding result must contain one row"
            )

        onboarding_row = rows[0]
        access_role = str(
            onboarding_row.get("AccessRole") or ""
        ).strip().upper()

        if (
            onboarding_row.get("AthleteID") is not None
            or onboarding_row.get("FullName") is not None
            or access_role != "RUNNER"
        ):
            raise AuthorizationError(
                "Onboarding result has invalid athlete access"
            )

        return AuthorizationResult(
            app_user_id=app_user_id,
            authorization_state=authorization_state,
            accesses=(),
        )

    accesses: list[AthleteAccess] = []
    seen_athlete_ids: set[int] = set()

    for row in rows:
        try:
            athlete_id = int(row["AthleteID"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthorizationError(
                "Athlete access has an invalid AthleteID"
            ) from exc

        full_name_value = row.get("FullName")

        if (
            not isinstance(full_name_value, str)
            or not full_name_value.strip()
        ):
            raise AuthorizationError(
                "Athlete access has no display name"
            )

        full_name = full_name_value.strip()
        access_role = str(
            row.get("AccessRole") or ""
        ).strip().upper()

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

    return AuthorizationResult(
        app_user_id=app_user_id,
        authorization_state=authorization_state,
        accesses=tuple(accesses),
    )

def complete_runner_onboarding(
    connection: Connection,
    app_user_id: int,
    onboarding: RunnerOnboardingData,
) -> AthleteAccess:
    result = connection.execute(
        text(
            """
            SELECT
                app_user_id AS "AppUserID",
                authorization_state AS "AuthorizationState",
                athlete_id AS "AthleteID",
                full_name AS "FullName",
                access_role AS "AccessRole",
                personal_record_id AS "PersonalRecordID",
                latest_pr_distance_m AS "LatestPRDistanceM",
                latest_pr_completed_duration_sec
                    AS "LatestPRCompletedDurationSec",
                latest_pr_achieved_date
                    AS "LatestPRAchievedDate"
            FROM public.complete_runner_onboarding
            (
                :app_user_id,
                :full_name,
                :date_of_birth,
                :sex,
                :height_cm,
                :weight_kg,
                :latest_pr_distance_m,
                :latest_pr_completed_duration_sec,
                :latest_pr_achieved_date
            )
            """
        ),
        {
            "app_user_id": app_user_id,
            "full_name": onboarding.full_name,
            "date_of_birth": onboarding.date_of_birth,
            "sex": onboarding.sex,
            "height_cm": onboarding.height_cm,
            "weight_kg": onboarding.weight_kg,
            "latest_pr_distance_m": (
                onboarding.latest_pr_distance_m
            ),
            "latest_pr_completed_duration_sec": (
                onboarding.latest_pr_completed_duration_sec
            ),
            "latest_pr_achieved_date": (
                onboarding.latest_pr_achieved_date
            ),
        },
    )

    rows = result.mappings().all()

    if len(rows) != 1:
        raise OnboardingError(
            "Onboarding must return exactly one result"
        )

    row = rows[0]

    try:
        returned_app_user_id = int(row["AppUserID"])
        athlete_id = int(row["AthleteID"])
        personal_record_id = int(
            row["PersonalRecordID"]
        )
        returned_distance_m = int(
            row["LatestPRDistanceM"]
        )
        returned_duration_sec = int(
            row["LatestPRCompletedDurationSec"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OnboardingError(
            "Onboarding result contains invalid identifiers"
        ) from exc

    authorization_state = str(
        row.get("AuthorizationState") or ""
    ).strip().upper()

    access_role = str(
        row.get("AccessRole") or ""
    ).strip().upper()

    full_name_value = row.get("FullName")

    if returned_app_user_id != app_user_id:
        raise OnboardingError(
            "Onboarding returned a different app user"
        )

    if authorization_state != AUTHORIZED_STATE:
        raise OnboardingError(
            "Onboarding did not return AUTHORIZED"
        )

    if access_role != "RUNNER":
        raise OnboardingError(
            "Onboarding did not return RUNNER access"
        )

    if (
        not isinstance(full_name_value, str)
        or not full_name_value.strip()
    ):
        raise OnboardingError(
            "Onboarding returned no athlete name"
        )

    if athlete_id <= 0 or personal_record_id <= 0:
        raise OnboardingError(
            "Onboarding returned invalid record identifiers"
        )

    if (
        returned_distance_m
        != onboarding.latest_pr_distance_m
        or returned_duration_sec
        != onboarding.latest_pr_completed_duration_sec
        or row.get("LatestPRAchievedDate")
        != onboarding.latest_pr_achieved_date
    ):
        raise OnboardingError(
            "Onboarding returned different PR data"
        )

    return AthleteAccess(
        app_user_id=returned_app_user_id,
        athlete_id=athlete_id,
        full_name=full_name_value.strip(),
        access_role=access_role,
    )