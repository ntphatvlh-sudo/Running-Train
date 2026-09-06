import pytest

from src.auth import (
    GoogleIdentity,
    normalize_email,
    parse_google_identity,
)


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