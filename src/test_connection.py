from database import test_connection as check_connection


def main() -> None:
    """Kiểm tra kết nối database khi chạy script trực tiếp."""
    try:
        result = check_connection()

        print(
            f"Kết nối {result['Backend']} thành công."
        )
        print(f"Server: {result['ServerName']}")
        print(f"Database: {result['DatabaseName']}")
        print(f"Login: {result['LoginName']}")

    except Exception as error:
        print("Kết nối database thất bại.")
        print(type(error).__name__)
        print(error)


if __name__ == "__main__":
    main()