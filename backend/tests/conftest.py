from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  -- 触发所有 ORM 模型注册到 Base.metadata
from app.db import Base


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """内存 SQLite session,每个测试 fresh 建表。StaticPool 保证内存库跨连接存活。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    test_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = test_session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
