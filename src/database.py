import os
import urllib.parse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


load_dotenv()


def _create_postgres_engine(database_url: str) -> Engine:
    normalized_url = database_url.strip()

    if normalized_url.startswith("postgres://"):
        normalized_url = normalized_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )
    elif normalized_url.startswith("postgresql://"):
        normalized_url = normalized_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
    elif not normalized_url.startswith(
        "postgresql+psycopg://"
    ):
        raise RuntimeError(
            "DATABASE_URL không phải PostgreSQL URL hợp lệ"
        )

    return create_engine(
        normalized_url,
        pool_pre_ping=True,
        pool_recycle=300,
        future=True,
    )


def _create_sql_server_engine() -> Engine:
    server = os.getenv("DB_SERVER")
    database = os.getenv("DB_NAME")

    driver = os.getenv(
        "DB_DRIVER",
        "ODBC Driver 18 for SQL Server",
    )

    trusted_connection = os.getenv(
        "DB_TRUSTED_CONNECTION",
        "yes",
    ).lower()

    trust_certificate = os.getenv(
        "DB_TRUST_SERVER_CERTIFICATE",
        "yes",
    ).lower()

    if not server or not database:
        raise RuntimeError(
            "Thiếu DB_SERVER hoặc DB_NAME trong file .env"
        )

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        "Encrypt=yes",
        (
            "TrustServerCertificate="
            f"{trust_certificate}"
        ),
    ]

    if trusted_connection == "yes":
        parts.append("Trusted_Connection=yes")
    else:
        username = os.getenv("DB_USERNAME")
        password = os.getenv("DB_PASSWORD")

        if not username or not password:
            raise RuntimeError(
                "Thiếu DB_USERNAME hoặc DB_PASSWORD "
                "trong file .env"
            )

        parts.extend(
            [
                f"UID={username}",
                f"PWD={password}",
            ]
        )

    odbc_connection = ";".join(parts) + ";"

    encoded_connection = urllib.parse.quote_plus(
        odbc_connection
    )

    return create_engine(
        (
            "mssql+pyodbc:///?odbc_connect="
            f"{encoded_connection}"
        ),
        pool_pre_ping=True,
        future=True,
    )


database_url = os.getenv("DATABASE_URL")

if database_url:
    DATABASE_BACKEND = "postgresql"
    engine = _create_postgres_engine(database_url)
else:
    DATABASE_BACKEND = "sqlserver"
    engine = _create_sql_server_engine()


def test_connection() -> dict:
    if DATABASE_BACKEND == "postgresql":
        query = text(
            """
            SELECT
                'postgresql' AS "Backend",
                inet_server_addr()::text AS "ServerName",
                current_database() AS "DatabaseName",
                current_user AS "LoginName"
            """
        )
    else:
        query = text(
            """
            SELECT
                'sqlserver' AS Backend,
                @@SERVERNAME AS ServerName,
                DB_NAME() AS DatabaseName,
                SUSER_SNAME() AS LoginName
            """
        )

    with engine.connect() as connection:
        result = connection.execute(query).mappings().one()
        return dict(result)