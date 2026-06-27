import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


@pytest.fixture
def sqlite_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(sqlite_engine):
    with Session(sqlite_engine) as db_session:
        yield db_session


@pytest.fixture
def session_override(sqlite_engine):
    def override_session():
        with Session(sqlite_engine) as db_session:
            yield db_session

    return override_session
