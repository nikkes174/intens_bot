from datetime import date
from typing import Optional

from sqlalchemy import Integer, BigInteger, String, Date, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from tgbot.db.db import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    user_name: Mapped[Optional[str]] = mapped_column(String(255))

    payment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payment_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    create_date: Mapped[Optional[date]] = mapped_column(Date)
