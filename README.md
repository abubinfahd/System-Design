# 🔗 Mini URL Shortener with Analytics (Milestone 1)

Welcome to the **Mini URL Shortener** project! This is the first milestone of building a highly scalable, low-latency URL shortener system.

In this first milestone, we have built a **functional** core utilizing a **Client-Server architecture** and a **Stateless** FastAPI application backed by a simple **SQLite Database**.

## Features implemented in Milestone 1

1. **Create Short URL:** Convert a long URL into a random 6-character short code.
2. **Redirect to Original URL:** Automatically redirect users from `/{short_code}` to the original URL.
3. **Track Clicks:** Record every time someone uses a short code.
4. **Get Analytics:** See how many times a particular short link has been clicked.

## Tech Stack

- **Backend Framework:** FastAPI (Python)
- **Database:** PostgreSQL (Upgraded in Milestone 2)
- **ORM:** SQLAlchemy
- **Validation:** Pydantic
- **Containerization:** Docker & Docker Compose

---

## Project Structure (Modular Design)

The project is structured in a modular way so it can easily grow in future milestones:

```text
url_shortner/
├── app/
│   ├── main.py               # Application entry point
│   ├── api/
│   │   └── routes.py         # API endpoints (/shorten, /{code}, /analytics)
│   ├── core/
│   │   └── config.py         # Environment variables and settings
│   ├── db/
│   │   ├── database.py       # Database connection setup
│   │   └── models.py         # SQLAlchemy tables (URL, Click)
│   ├── schemas/
│   │   └── schemas.py        # Request & Response Pydantic validation
│   └── services/
│       └── url_service.py    # Core business logic
├── Dockerfile                # Instructions to build the Docker image
├── docker-compose.yml        # Easy way to run the service locally
└── requirements.txt          # Python dependencies
```

---

## How to Run the Project

### Option 1: Using Docker (Recommended for Beginners)

If you have Docker Desktop installed, you can start the entire application with one command.

1. Open your terminal in the project directory.
2. Run the following command:

   ```bash
   docker-compose up --build
   ```

3. The API will now be running at `http://localhost:8000`.

*(Note: A PostgreSQL container will be spun up automatically. Database files are persisted via Docker named volume `postgres_data`).*

### Option 2: Running Locally without Docker

If you prefer to run it using standard Python on your machine:

1. Create a virtual environment and activate it:

   ```bash
   python -m venv venv
   venv\Scripts\activate  # On Windows
   ```

2. Copy the environment template and set up your PostgreSQL database connection:

   ```bash
   copy .env.example .env
   # Open .env and adjust the DATABASE_URL if needed (must point to a running PostgreSQL server)
   ```

3. Install the dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Start the server:

   ```bash
   uvicorn app.main:app --reload
   ```

---

## See DB

Make sure the server has run at least once to initialize the tables, then:

```bash
python view_db.py
```

## API Documentation (How to Test)

FastAPI automatically generates an interactive documentation page.
Once the server is running, simply go to:

👉 **[http://localhost:8000/docs](http://localhost:8000/docs)**

### Endpoints Overview

#### 1. Shorten a URL

- **Method:** `POST`
- **Path:** `/shorten`
- **Body:**

  ```json
  {
    "long_url": "https://www.google.com"
  }
  ```

- **Response:**

  ```json
  {
    "short_code": "aB3dE5",
    "long_url": "https://www.google.com/"
  }
  ```

#### 2. Test the Redirect

- **Method:** `GET`
- **Path:** `/{short_code}` (e.g., `http://localhost:8000/aB3dE5`)
- **Behavior:** This won't return JSON. It will issue an HTTP 302 redirect and take your browser directly to the `long_url`.

#### 3. View Analytics

- **Method:** `GET`
- **Path:** `/analytics/{short_code}` (e.g., `/analytics/aB3dE5`)
- **Response:**

  ```json
  {
    "short_code": "aB3dE5",
    "total_clicks": 1
  }
  ```

---

## Load Testing

To ensure the system can handle concurrent requests and maintain low latency, a load testing script is included.

```bash
# Make sure your server is running, then execute:
python load_test.py
```

The script will automatically:

1. Create a short URL.
2. Spin up **50 concurrent users**.
3. Send **500 requests** to the redirect endpoint.
4. Print latency metrics (min, max, average) and Requests Per Second (Req/s) to the terminal.
5. Save the final report to `load_test_result.txt` in the root folder.

---

## What's Next? (Future Milestones)

In the upcoming milestones, we will address **scalability** and **performance**:

- Migrating to **PostgreSQL**.
- Implementing **Base62** encoding to prevent collision risks.
- Adding a **Redis Cache** to optimize the read-heavy redirect path.
- Making click-tracking **eventually consistent** (e.g., using background tasks).

## Problem & Resolution (Fixed)

- **Problem:** Running the load test (`load_test.py`) populated the development/production database (`shortener.db` or `data/shortener.db`) with dummy URLs and thousands of clicks, which is bad practice and pollutes data.
- **Resolution:** Updated `load_test.py` with an automatic database cleanup mechanism. After running the load test, the script:
  1. Identifies the active SQLite database file location (`shortener.db` or `data/shortener.db`).
  2. Directly connects to the SQLite database.
  3. Finds the created test short code and deletes all its click records.
  4. Deletes the test short code itself, leaving the database exactly in its original clean state.

