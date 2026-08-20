
# Running Training Analytics

A SQL Server and Streamlit project for analyzing running-plan adherence, supporting training habits, wellness readiness, and adaptive plan recommendations.

## Main features

- Import Run Log, Strength Log, Mobility Log, and Daily Wellness from Excel.
- Store and analyze data in Microsoft SQL Server.
- Calculate running completion by workout, week, and plan.
- Analyze Strength and Mobility completion separately.
- Recommend plan reduction when run completion is below 75%.
- Allow controlled 5% progression after two strong consecutive weeks.
- Protect race periods from automatic progression.
- Preview changes before applying them.
- Store complete workout version history.
- Roll back the latest applied adjustment.
- Display results through a Streamlit Dashboard.

## Adaptive-plan rules

### Reduction

| Run completion | Recommendation |
|---:|---|
| 75% or higher | Keep current plan |
| 65%–74% | Light reduction |
| 50%–64% | Moderate reduction |
| Below 50% | Strong reduction |

Strength and Mobility are supporting analysis signals. They do not independently trigger the below-75% reduction rule.

### Controlled progression

A 5% progression is available only when:

- Running completion is at least 90%.
- Two complete consecutive weeks are available.
- Each week has enough wellness data.
- Average fatigue, stress, and soreness are at most 2.
- Average readiness is at least 80.
- No race occurs within the next 14 days.
- No recent progression conflicts with the safety rules.

Progression increases distance and duration by the same percentage while preserving target pace.

## Technology

- Python
- Pandas
- SQLAlchemy
- pyodbc
- Microsoft SQL Server
- SQL Server Management Studio
- Streamlit
- Plotly
- Excel

## Project structure

```text
Running-Train/
├── dashboard/
│   └── app.py
├── data/
│   └── README.md
├── sql/
│   ├── 01_database_schema.sql
│   └── 02_validate_objects.sql
├── src/
│   ├── database.py
│   ├── import_excel.py
│   └── test_connection.py
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt

```

## Local setup

### 1. Clone the repository

```powershell
git clone https://github.com/ntphatvlh-sudo/Running-Train.git
cd Running-Train
```

### 2. Create a virtual environment

```powershell
py -3.12 -m venv .venv
```

The virtual environment does not need to be activated. Commands below call its Python executable directly.

### 3. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

### 4. Prepare SQL Server

1. Open SQL Server Management Studio.
2. Connect to the target SQL Server instance.
3. Open and execute `sql/01_database_schema.sql`.
4. Open and execute `sql/02_validate_objects.sql`.
5. Confirm that required object IDs are not `NULL`.

### 5. Configure the database connection

Copy the example environment file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set the correct SQL Server instance:

```env
DB_SERVER=localhost\SQLEXPRESS
DB_NAME=RunningTrainingDB
DB_DRIVER=ODBC Driver 18 for SQL Server
DB_TRUSTED_CONNECTION=yes
DB_TRUST_SERVER_CERTIFICATE=yes
```

Do not commit `.env`.

### 6. Add the personal workbook

Place the workbook at:

```text
data/running_data.xlsx
```

Personal `.xlsx` files are excluded from Git.

### 7. Test SQL Server connectivity

```powershell
.\.venv\Scripts\python.exe .\src\test_connection.py
```

### 8. Validate the Excel import

```powershell
.\.venv\Scripts\python.exe .\src\import_excel.py --dry-run
```

### 9. Import log data

```powershell
.\.venv\Scripts\python.exe .\src\import_excel.py
```

Normal imports do not use `--sync-calendar`, so Excel does not overwrite adaptive workout changes stored in SQL Server.

### 10. Run the dashboard

```powershell
.\.venv\Scripts\python.exe -m streamlit run .\dashboard\app.py
```

Open `http://localhost:8501` if the browser does not open automatically.

## Data safety

- `.env` is local and must not be committed.
- Personal Excel workbooks are excluded from Git.
- Preview an adaptive-plan change before applying it.
- Applied adjustments retain workout version history.
- Use rollback only for the latest applicable adjustment.