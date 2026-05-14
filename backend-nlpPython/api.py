"""
Disaster Alert API — root entry point.

Run from the project root:
    python api.py
    OR: uvicorn api:app --reload --host 0.0.0.0 --port 8000

Swagger docs:  http://localhost:8000/docs
ReDoc:         http://localhost:8000/redoc
"""

from nlp.api import app  # noqa: F401  — re-export FastAPI app

import uvicorn

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
