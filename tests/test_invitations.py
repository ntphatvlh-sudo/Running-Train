from datetime import datetime

import pytest

from src.invitations import (
    NEW_RUNNER_INVITATION,
    InvitationRequest,
    InvitationResult,
    create_invitation,
    EXISTING_ATHLETE_INVITATION,
    InvitationError,
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


def test_create_new_runner_invitation_binds_and_returns() -> None:
    created_at = datetime(2026, 9, 11, 8, 30)

    request = InvitationRequest(
        invited_by_app_user_id=3,
        email_normalized="  Runner@Example.COM  ",
        access_role="runner",
        athlete_id=None,
        expires_at=None,
    )

    connection = RecordingConnection(
        [
            {
                "InvitationID": 7,
                "EmailNormalized": "runner@example.com",
                "InvitationType": "NEW_RUNNER",
                "AthleteID": None,
                "AthleteName": None,
                "AccessRole": "RUNNER",
                "Status": "PENDING",
                "InvitedByAppUserID": 3,
                "CreatedAt": created_at,
                "ExpiresAt": None,
            }
        ]
    )

    invitation = create_invitation(
        connection,
        request,
    )

    assert invitation == InvitationResult(
        invitation_id=7,
        email_normalized="runner@example.com",
        invitation_type=NEW_RUNNER_INVITATION,
        athlete_id=None,
        athlete_name=None,
        access_role="RUNNER",
        status="PENDING",
        invited_by_app_user_id=3,
        created_at=created_at,
        expires_at=None,
    )

    assert len(connection.calls) == 1

    statement, parameters = connection.calls[0]

    assert "public.create_invitation" in statement

    assert parameters == {
        "invited_by_app_user_id": 3,
        "email_normalized": "runner@example.com",
        "access_role": "RUNNER",
        "athlete_id": None,
        "expires_at": None,
    }


def test_create_existing_athlete_invitation_returns_access() -> None:
    created_at = datetime(2026, 9, 11, 8, 30)
    expires_at = datetime(2026, 9, 18, 8, 30)

    request = InvitationRequest(
        invited_by_app_user_id=3,
        email_normalized="coach@example.com",
        access_role="COACH",
        athlete_id=1,
        expires_at=expires_at,
    )

    connection = RecordingConnection(
        [
            {
                "InvitationID": 8,
                "EmailNormalized": "coach@example.com",
                "InvitationType": "EXISTING_ATHLETE",
                "AthleteID": 1,
                "AthleteName": "Nguyễn Tấn Phát",
                "AccessRole": "COACH",
                "Status": "PENDING",
                "InvitedByAppUserID": 3,
                "CreatedAt": created_at,
                "ExpiresAt": expires_at,
            }
        ]
    )

    invitation = create_invitation(
        connection,
        request,
    )

    assert invitation == InvitationResult(
        invitation_id=8,
        email_normalized="coach@example.com",
        invitation_type=(
            EXISTING_ATHLETE_INVITATION
        ),
        athlete_id=1,
        athlete_name="Nguyễn Tấn Phát",
        access_role="COACH",
        status="PENDING",
        invited_by_app_user_id=3,
        created_at=created_at,
        expires_at=expires_at,
    )

    _, parameters = connection.calls[0]

    assert parameters["athlete_id"] == 1
    assert parameters["access_role"] == "COACH"
    assert parameters["expires_at"] == expires_at


@pytest.mark.parametrize(
    ("access_role", "athlete_id", "expected_message"),
    [
        (
            "VIEWER",
            None,
            "access role is invalid",
        ),
        (
            "COACH",
            None,
            "must use RUNNER role",
        ),
    ],
)
def test_create_invitation_rejects_invalid_request(
    access_role: str,
    athlete_id: int | None,
    expected_message: str,
) -> None:
    request = InvitationRequest(
        invited_by_app_user_id=3,
        email_normalized="user@example.com",
        access_role=access_role,
        athlete_id=athlete_id,
        expires_at=None,
    )

    connection = RecordingConnection([])

    with pytest.raises(
        InvitationError,
        match=expected_message,
    ):
        create_invitation(
            connection,
            request,
        )

    assert connection.calls == []