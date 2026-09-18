from datetime import date

import pytest

from src.auth import (
    AUTHORIZED_STATE,
    ONBOARDING_REQUIRED_STATE,
    AthleteAccess,
    AuthorizationError,
    AuthorizationResult,
    GoogleIdentity,
    OnboardingError,
    RunnerOnboardingData,
    authorize_google_identity,
    complete_runner_onboarding,
    normalize_email,
    parse_google_identity,
)


class FakeMappingResult:
    def __init__(
        self,
        rows: list[dict[str, object]],
    ) -> None:
        self.rows = rows

    def mappings(self) -> "FakeMappingResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows


class RecordingConnection:
    def __init__(
        self,
        rows: list[dict[str, object]],
    ) -> None:
        self.rows = rows
        self.calls: list[
            tuple[str, dict[str, object]]
        ] = []

    def execute(
        self,
        statement: object,
        parameters: dict[str, object],
    ) -> FakeMappingResult:
        self.calls.append(
            (
                str(statement),
                parameters.copy(),
            )
        )

        return FakeMappingResult(self.rows)

def test_parse_google_identity_normalizes_valid_claims() -> None:
    identity = parse_google_identity(
        {
            "sub": "google-subject-123",
            "email": "  Runner@Example.COM  ",
            "email_verified": True,
            "name": "  Nguyen Tan Phat  ",
        }
    )

    assert identity == GoogleIdentity(
        subject="google-subject-123",
        email_normalized="runner@example.com",
        display_name="Nguyen Tan Phat",
    )


def test_parse_google_identity_allows_missing_display_name() -> None:
    identity = parse_google_identity(
        {
            "sub": "google-subject-123",
            "email": "runner@example.com",
            "email_verified": True,
            "name": "   ",
        }
    )

    assert identity.display_name is None


@pytest.mark.parametrize(
    "email_verified",
    [False, None, "true"],
)
def test_parse_google_identity_rejects_unverified_email(
    email_verified: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="Google email is not verified",
    ):
        parse_google_identity(
            {
                "sub": "google-subject-123",
                "email": "runner@example.com",
                "email_verified": email_verified,
            }
        )


@pytest.mark.parametrize(
    "claims",
    [
        {
            "email": "runner@example.com",
            "email_verified": True,
        },
        {
            "sub": "google-subject-123",
            "email_verified": True,
        },
        {
            "sub": "   ",
            "email": "runner@example.com",
            "email_verified": True,
        },
        {
            "sub": "google-subject-123",
            "email": "   ",
            "email_verified": True,
        },
    ],
)
def test_parse_google_identity_rejects_missing_required_claims(
    claims: dict[str, object],
) -> None:
    with pytest.raises(
        ValueError,
        match="Missing or invalid Google claim",
    ):
        parse_google_identity(claims)


@pytest.mark.parametrize(
    "email",
    [
        "runner.example.com",
        "@example.com",
        "runner@",
    ],
)
def test_normalize_email_rejects_invalid_address(
    email: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="Invalid Google email address",
    ):
        normalize_email(email)


def test_authorize_google_identity_binds_parameters() -> None:
    connection = RecordingConnection(
        [
            {
                "AppUserID": 7,
                "AuthorizationState": "AUTHORIZED",
                "AthleteID": 2,
                "FullName": "Runner Two",
                "AccessRole": "RUNNER",
            }
        ]
    )
    identity = GoogleIdentity(
        subject="google-subject-123",
        email_normalized="runner@example.com",
        display_name="Runner Two",
    )

    authorization = authorize_google_identity(
        connection,
        identity,
    )

    assert authorization == AuthorizationResult(
        app_user_id=7,
        authorization_state=AUTHORIZED_STATE,
        accesses=(
            AthleteAccess(
                app_user_id=7,
                athlete_id=2,
                full_name="Runner Two",
                access_role="RUNNER",
            ),
        ),
    )

    sql, parameters = connection.calls[0]

    assert "public.authorize_google_login" in sql
    assert ":google_subject" in sql
    assert ":email_normalized" in sql
    assert ":display_name" in sql
    assert identity.subject not in sql
    assert identity.email_normalized not in sql
    assert parameters == {
        "google_subject": "google-subject-123",
        "email_normalized": "runner@example.com",
        "display_name": "Runner Two",
    }


def test_authorize_google_identity_rejects_empty_result() -> None:
    connection = RecordingConnection([])
    identity = GoogleIdentity(
        subject="google-subject-123",
        email_normalized="runner@example.com",
        display_name=None,
    )

    with pytest.raises(
        AuthorizationError,
        match="No authorization result",
    ):
        authorize_google_identity(connection, identity)

def test_authorize_google_identity_returns_onboarding() -> None:
    connection = RecordingConnection(
        [
            {
                "AppUserID": 7,
                "AuthorizationState": (
                    "ONBOARDING_REQUIRED"
                ),
                "AthleteID": None,
                "FullName": None,
                "AccessRole": "RUNNER",
            }
        ]
    )
    identity = GoogleIdentity(
        subject="google-subject-123",
        email_normalized="runner@example.com",
        display_name="Runner Two",
    )

    authorization = authorize_google_identity(
        connection,
        identity,
    )

    assert authorization == AuthorizationResult(
        app_user_id=7,
        authorization_state=(
            ONBOARDING_REQUIRED_STATE
        ),
        accesses=(),
    )

@pytest.mark.parametrize(
    ("row", "expected_message"),
    [
        (
            {
                "AppUserID": 7,
                "AuthorizationState": "AUTHORIZED",
                "AthleteID": 2,
                "FullName": "   ",
                "AccessRole": "RUNNER",
            },
            "no display name",
        ),
        (
            {
                "AppUserID": 7,
                "AuthorizationState": "AUTHORIZED",
                "AthleteID": 2,
                "FullName": "Runner Two",
                "AccessRole": "VIEWER",
            },
            "Invalid athlete access role",
        ),
    ],
)
def test_authorize_google_identity_rejects_invalid_access(
    row: dict[str, object],
    expected_message: str,
) -> None:
    connection = RecordingConnection([row])
    identity = GoogleIdentity(
        subject="google-subject-123",
        email_normalized="runner@example.com",
        display_name=None,
    )

    with pytest.raises(
        AuthorizationError,
        match=expected_message,
    ):
        authorize_google_identity(connection, identity)


def test_authorize_google_identity_rejects_duplicate_athlete() -> None:
    connection = RecordingConnection(
        [
            {
                "AppUserID": 7,
                "AuthorizationState": "AUTHORIZED",
                "AthleteID": 2,
                "FullName": "Runner Two",
                "AccessRole": "RUNNER",
            },
            {
                "AppUserID": 7,
                "AuthorizationState": "AUTHORIZED",
                "AthleteID": 2,
                "FullName": "Runner Two",
                "AccessRole": "RUNNER",
            },
        ]
    )
    identity = GoogleIdentity(
        subject="google-subject-123",
        email_normalized="runner@example.com",
        display_name=None,
    )

    with pytest.raises(
        AuthorizationError,
        match="Duplicate athlete access",
    ):
        authorize_google_identity(connection, identity)


def test_complete_runner_onboarding_binds_and_returns_access() -> None:
    achieved_date = date(2026, 9, 1)

    onboarding = RunnerOnboardingData(
        full_name="Runner Two",
        date_of_birth=None,
        sex=None,
        height_cm=170.0,
        weight_kg=65.0,
        latest_pr_distance_m=5000,
        latest_pr_completed_duration_sec=1500,
        latest_pr_achieved_date=achieved_date,
    )

    connection = RecordingConnection(
        [
            {
                "AppUserID": 7,
                "AuthorizationState": "AUTHORIZED",
                "AthleteID": 2,
                "FullName": "Runner Two",
                "AccessRole": "RUNNER",
                "PersonalRecordID": 11,
                "LatestPRDistanceM": 5000,
                "LatestPRCompletedDurationSec": 1500,
                "LatestPRAchievedDate": achieved_date,
            }
        ]
    )

    access = complete_runner_onboarding(
        connection,
        app_user_id=7,
        onboarding=onboarding,
    )

    assert access == AthleteAccess(
        app_user_id=7,
        athlete_id=2,
        full_name="Runner Two",
        access_role="RUNNER",
    )

    assert len(connection.calls) == 1

    statement, parameters = connection.calls[0]

    assert "public.complete_runner_onboarding" in statement

    assert parameters == {
        "app_user_id": 7,
        "full_name": "Runner Two",
        "date_of_birth": None,
        "sex": None,
        "height_cm": 170.0,
        "weight_kg": 65.0,
        "latest_pr_distance_m": 5000,
        "latest_pr_completed_duration_sec": 1500,
        "latest_pr_achieved_date": achieved_date,
    }