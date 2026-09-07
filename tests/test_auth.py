import pytest

from src.auth import (
    AthleteAccess,
    AuthorizationError,
    GoogleIdentity,
    authorize_google_identity,
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

    accesses = authorize_google_identity(
        connection,
        identity,
    )

    assert accesses == [
        AthleteAccess(
            app_user_id=7,
            athlete_id=2,
            full_name="Runner Two",
            access_role="RUNNER",
        )
    ]

    sql, parameters = connection.calls[0]

    assert "dbo.usp_AuthorizeGoogleLogin" in sql
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
        match="No active athlete access",
    ):
        authorize_google_identity(connection, identity)


@pytest.mark.parametrize(
    ("row", "expected_message"),
    [
        (
            {
                "AppUserID": 7,
                "AthleteID": 2,
                "FullName": "   ",
                "AccessRole": "RUNNER",
            },
            "no display name",
        ),
        (
            {
                "AppUserID": 7,
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
                "AthleteID": 2,
                "FullName": "Runner Two",
                "AccessRole": "RUNNER",
            },
            {
                "AppUserID": 7,
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