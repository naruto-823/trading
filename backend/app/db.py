from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

db_path = Path(settings.database_url.replace("sqlite:///", ""))
db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False}, echo=False)


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # 显式触发模型导入，确保 Base.metadata 注册所有表（新增模型只需在 models/__init__.py 暴露即可）
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()


def _apply_lightweight_migrations() -> None:
    """SQLite 不支持自动列扩展，这里幂等补齐新增列。"""
    insp = inspect(engine)
    added: list[tuple[str, str, str]] = [
        ("account_snapshot", "realized_day_pnl", "FLOAT NOT NULL DEFAULT 0.0"),
        ("account_snapshot", "realized_day_pnl_by_market", "TEXT"),
        ("account_snapshot", "max_finance_amount", "FLOAT NOT NULL DEFAULT 0.0"),
        ("account_snapshot", "remaining_finance_amount", "FLOAT NOT NULL DEFAULT 0.0"),
        ("account_snapshot", "init_margin", "FLOAT NOT NULL DEFAULT 0.0"),
        ("account_snapshot", "maintenance_margin", "FLOAT NOT NULL DEFAULT 0.0"),
        ("account_snapshot", "buy_power", "FLOAT NOT NULL DEFAULT 0.0"),
        ("account_snapshot", "margin_call", "INTEGER NOT NULL DEFAULT 0"),
        ("account_snapshot", "risk_level", "INTEGER NOT NULL DEFAULT 0"),
        ("account_snapshot", "cash_infos_json", "TEXT"),
        ("account_snapshot", "outstanding_debt", "FLOAT NOT NULL DEFAULT 0.0"),
        ("account_snapshot", "fx_rates_json", "TEXT"),
        # Quick Assess 评分（macro_pusher / jin10_browser 推送前的相关性门控）
        ("event_notification", "relevance", "VARCHAR(20)"),
        ("event_notification", "relevance_score", "INTEGER"),
        ("event_notification", "relevance_reason", "TEXT"),
    ]
    for table, column, ddl in added:
        if table not in insp.get_table_names():
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if column in existing:
            continue
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {column} {ddl}'))
