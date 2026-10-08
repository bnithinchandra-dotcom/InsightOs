from os import getenv

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://"
    f"{getenv('POSTGRES_USER', 'insightos')}:{getenv('POSTGRES_PASSWORD', 'change_me')}@"
    f"{getenv('POSTGRES_HOST', 'postgres')}:{getenv('POSTGRES_PORT', '5432')}/"
    f"{getenv('POSTGRES_DB', 'insightos')}"
)


def get_database_url() -> str:
    return getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


DATABASE_URL = get_database_url()

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