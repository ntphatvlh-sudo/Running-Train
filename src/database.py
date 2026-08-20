import os
import urllib.parse

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

server = os.getenv("DB_SERVER")
database = os.getenv("DB_NAME")
driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
trusted_connection = os.getenv("DB_TRUSTED_CONNECTION", "yes").lower()
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
    f"TrustServerCertificate={trust_certificate}",
]

if trusted_connection == "yes":
    parts.append("Trusted_Connection=yes")
else:
    username = os.getenv("DB_USERNAME")
    password = os.getenv("DB_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            "Thiếu DB_USERNAME hoặc DB_PASSWORD trong file .env"
        )

    parts.extend([
        f"UID={username}",
        f"PWD={password}",
    ])

odbc_connection = ";".join(parts) + ";"
encoded_connection = urllib.parse.quote_plus(odbc_connection)

engine = create_engine(
    f"mssql+pyodbc:///?odbc_connect={encoded_connection}",
    pool_pre_ping=True,
    future=True,
)


def test_connection() -> dict:
    with engine.connect() as connection:
        result = connection.execute(
            text(
                """
                SELECT
                    @@SERVERNAME AS ServerName,
                    DB_NAME() AS DatabaseName,
                    SUSER_SNAME() AS LoginName
                """
            )
        ).mappings().one()

        return dict(result)