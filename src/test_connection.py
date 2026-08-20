from database import test_connection

try:
    result = test_connection()

    print("Kết nối SQL Server thành công.")
    print(f"Server: {result['ServerName']}")
    print(f"Database: {result['DatabaseName']}")
    print(f"Login: {result['LoginName']}")

except Exception as error:
    print("Kết nối thất bại.")
    print(type(error).__name__)
    print(error)