from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    password_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    broker_account_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    broker_app_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_app_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
