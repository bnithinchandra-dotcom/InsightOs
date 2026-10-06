from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from os import getenv


DATABASE_URL = getenv(
    "DATABASE_URL",
    "postgresql+psycopg://insightos:insightos_dev_password@postgres:5432/insightos",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass