# PanicTrace 🫀

> **Bachelor Thesis Project** — FH Hagenberg, 2026  
> A voice-driven panic attack tracking and visualization system built on AWS and Django.

---

## Overview

PanicTrace is a full-stack health-tracking application developed as part of a bachelor thesis. It allows users to log panic attack episodes hands-free via **Amazon Alexa** and analyze their data through an interactive **Django web dashboard**.

The system captures 13 clinical symptoms defined by the DSM-5 panic attack criteria, along with severity, duration, trigger, and time of occurrence — enabling users and researchers to identify patterns over time.

---

## System Architecture

```
User
 │
 ▼
Voice Interaction Layer (Amazon Alexa)
 │
 ▼
Processing Component (AWS Lambda)
 │
 ▼
Structured Data Storage (Amazon DynamoDB)
 │                        │
 ▼                        ▼ (conceptual)
Visualisation Module      ML Extension
(Django Dashboard)
```

The architecture consists of four main layers:

- **Amazon Alexa** — Voice interface for hands-free episode logging
- **AWS Lambda** — Serverless processing and symptom extraction (see [`lambda/`](./lambda/))
- **Amazon DynamoDB** — NoSQL storage for all panic attack episodes (`eu-west-1`, table: `PanicAttacks`)
- **Django Dashboard** — Web-based visualization and export module (this repository)

A conceptual ML extension is planned for future pattern recognition and predictive analysis.

---

## Features

### Dashboard (`views.py`)
- Loads all episodes from DynamoDB with full pagination support
- Date range filtering
- Decodes 13 DSM-5 symptoms into human-readable labels
- Computes statistics: total episodes, average severity, average duration, most common trigger and symptom

### Charts & Visualizations
- **Symptom frequency** — Top 10 most reported symptoms
- **Hourly distribution** — Attack frequency by hour of day (0–23)
- **Weekday distribution** — Episodes by day of the week
- **Monthly trend** — Episode count over months
- **Trigger analysis** — Frequency and average severity per trigger
- **Severity over time** — Average severity per date
- **Duration vs. Severity** — Scatter plot correlation

### Export
- **CSV export** — All raw episode data as a downloadable `.csv`
- **PDF report** — Multi-section PDF via ReportLab including:
  - Overview statistics tiles
  - Symptom ranking table with visual bars
  - Trigger analysis with average severity
  - Temporal distribution (weekday + time-of-day blocks)
  - Full episode table (rows with severity ≥ 8 highlighted in red)

### Manual Entry
- Web form to add episodes manually (authenticated users only)
- Supports all 13 symptom checkboxes, trigger, severity, duration, and date/time fields

---

## Tracked Symptoms

| Field | Label |
|---|---|
| `chest_pain` | Chest Pain |
| `chills_or_heat` | Chills/Heat |
| `depersonalization` | Depersonalization |
| `derealization` | Derealization |
| `dizziness` | Dizziness |
| `fear_losing_control` | Fear of Losing Control |
| `fear_of_dying` | Fear of Dying |
| `nausea` | Nausea |
| `palpitations` | Palpitations |
| `paresthesia` | Numbness/Tingling |
| `shortness_of_breath` | Shortness of Breath |
| `sweating` | Sweating |
| `trembling` | Trembling |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Voice Interface | Amazon Alexa Skills Kit |
| Serverless Backend | AWS Lambda (Python) |
| Database | Amazon DynamoDB (`eu-west-1`) |
| Web Framework | Django |
| AWS SDK | boto3 |
| PDF Generation | ReportLab |
| Frontend Charts | Chart.js |

---

## Project Structure

```
PanicTrace_Django/
├── dashboard/                  # Django app — all application logic
│   ├── templates/              # HTML templates
│   ├── views.py                # Main logic: dashboard, charts, export, manual entry
│   ├── urls.py                 # App-level URL routing
│   ├── models.py               # Empty — no local DB needed (data lives in DynamoDB)
│   └── ...
├── PanicTrace_Django/          # Django project configuration
│   ├── settings.py             # Project settings (INSTALLED_APPS, ALLOWED_HOSTS, etc.)
│   ├── urls.py                 # Global URL routing
│   ├── wsgi.py
│   └── asgi.py
├── Lambda_Func/                # AWS Lambda function (independent from Django)
│   └── ...                     # Upload this to AWS Lambda — requires ask-sdk-core 1.19
├── templates/                  # Global templates
├── manage.py
└── README.md
```

> **`Lambda_Func/`** is completely independent from the Django app. It contains the Alexa skill handler that needs to be deployed directly to **AWS Lambda**. It requires the `ask-sdk-core==1.19` library, which must be included in the Lambda deployment package.

---

---

## Prerequisites

Before running the project, make sure you have the following in place:

- **Python 3.10+**
- **Django** — `pip install django`
- **boto3** — `pip install boto3`
- **ReportLab** — `pip install reportlab` (required for PDF export)
- An **AWS account** with:
  - A DynamoDB table named `PanicAttacks` in region `eu-west-1`
  - `userId` (String) as partition key and `timestamp` (String) as sort key
  - AWS credentials configured — either via environment variables, `~/.aws/credentials`, or an IAM role

### AWS Credentials

Set your credentials as environment variables:

```bash
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_DEFAULT_REGION=eu-west-1
```

Or configure via the AWS CLI:

```bash
aws configure
```

> **Note:** Region (`eu-west-1`) and table name (`PanicAttacks`) are hardcoded in `views.py` inside `get_table()`. Update them there if your setup differs.

---

## AWS Error Handling

The application includes structured error handling for all boto3 operations, covering:

- Missing or incomplete credentials (`NoCredentialsError`, `PartialCredentialsError`)
- IAM permission issues (`AccessDeniedException`)
- Table not found (`ResourceNotFoundException`)
- Expired session tokens (`ExpiredTokenException`)
- Network/endpoint resolution failures (`EndpointResolutionError`)

All errors are surfaced directly in the Django messages framework with human-readable descriptions.

---

## Academic Context

This project was developed as part of a bachelor thesis at **FH Hagenberg (2026)**. The goal is to provide people who suffer from panic disorder with a low-friction, voice-first tool for longitudinal self-monitoring, combined with a data dashboard that can support both self-reflection and clinical consultation.

---

## License

This project is part of an academic thesis. Please contact the author before reusing or distributing any part of the codebase.