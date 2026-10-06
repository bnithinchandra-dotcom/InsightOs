from fastapi import FastAPI
from sqlalchemy import text

from app.database import engine


app = FastAPI(
    title="InsightOS API",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "InsightOS",
        "version": "0.1.0",
    }


@app.get("/health/database")
def database_health_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "database": "postgresql",
        }

    except Exception as error:
        return {
            "status": "unhealthy",
            "database": "postgresql",
            "error": str(error),
        }