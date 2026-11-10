from __future__ import annotations

from typing import TYPE_CHECKING
from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.orm import Base

if TYPE_CHECKING:
    from schemas.product import CanonicalProduct


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False, default="")
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    height: Mapped[float | None] = mapped_column(Float, nullable=True)
    daily_calorie_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    diet_type: Mapped[str | None] = mapped_column(String, nullable=True)
    allergens: Mapped[str | None] = mapped_column(String, nullable=True)
    goal_type: Mapped[str | None] = mapped_column(String, nullable=True)
    goal_target_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=30)
    goal_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    scan_history: Mapped[list["ScanHistory"]] = relationship("ScanHistory", back_populates="user")
    food_logs: Mapped[list["FoodLog"]] = relationship("FoodLog", back_populates="user")
    ocr_jobs: Mapped[list["OCRJob"]] = relationship("OCRJob", back_populates="user")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_canonical_id", "canonical_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    barcode: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    brand: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    canonical_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nutriscore: Mapped[str | None] = mapped_column(String, nullable=True)
    ingredients: Mapped[str | None] = mapped_column(Text, nullable=True)
    additives: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance and completeness tracking
    data_source: Mapped[str | None] = mapped_column(String(50), nullable=True, default="local_db")
    data_confidence: Mapped[str | None] = mapped_column(String(20), nullable=True, default="UNVERIFIED")
    nutrition_completeness: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    ingredients_completeness: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    last_fetched_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_updated_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    field_provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String, nullable=False)

    nutrition: Mapped["Nutrition | None"] = relationship(
        "Nutrition",
        back_populates="product",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    aliases: Mapped[list["ProductBarcodeAlias"]] = relationship(
        "ProductBarcodeAlias",
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_canonical(self) -> CanonicalProduct:
        from database.canonical_adapter import db_product_to_canonical
        return db_product_to_canonical(self)


class Nutrition(Base):
    __tablename__ = "nutrition"
    __table_args__ = (
        Index("ix_nutrition_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)

    calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    saturated_fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    trans_fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    sugar: Mapped[float | None] = mapped_column(Float, nullable=True)
    salt: Mapped[float | None] = mapped_column(Float, nullable=True)
    sodium: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein: Mapped[float | None] = mapped_column(Float, nullable=True)
    fiber: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs: Mapped[float | None] = mapped_column(Float, nullable=True)

    product: Mapped[Product] = relationship("Product", back_populates="nutrition")


class ProductBarcodeAlias(Base):
    __tablename__ = "product_barcode_aliases"
    __table_args__ = (
        Index("ix_product_barcode_aliases_product_id", "product_id"),
        Index("ix_product_barcode_aliases_barcode", "alias_barcode", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    alias_barcode: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    alias_type: Mapped[str | None] = mapped_column(String(50), nullable=True, default="alternate")
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)

    product: Mapped[Product] = relationship("Product", back_populates="aliases")


class ScanHistory(Base):
    __tablename__ = "scan_history"
    __table_args__ = (
        Index("ix_scan_history_user_time", "user_id", "scan_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, default=1)
    barcode: Mapped[str] = mapped_column(String, nullable=False)
    scan_time: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[str] = mapped_column(String, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="scan_history")


class FoodLog(Base):
    __tablename__ = "daily_food_log"
    __table_args__ = (
        Index("ix_daily_food_log_user_consumed", "user_id", "consumed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, default=1)
    barcode: Mapped[str] = mapped_column(String, nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    saturated_fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    trans_fat: Mapped[float | None] = mapped_column(Float, nullable=True)
    sugar: Mapped[float | None] = mapped_column(Float, nullable=True)
    salt: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein: Mapped[float | None] = mapped_column(Float, nullable=True)
    fiber: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs: Mapped[float | None] = mapped_column(Float, nullable=True)
    consumed_at: Mapped[str] = mapped_column(String, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="food_logs")


class OCRJob(Base):
    __tablename__ = "ocr_jobs"
    __table_args__ = (
        Index("ix_ocr_jobs_user_id", "user_id"),
        Index("ix_ocr_jobs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="ocr_jobs")
