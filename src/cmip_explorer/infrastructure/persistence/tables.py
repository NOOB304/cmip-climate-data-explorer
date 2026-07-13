from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    plan_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tasks: Mapped[list[TaskRow]] = relationship(back_populates="job")


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    file_key: Mapped[str] = mapped_column(Text, index=True)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    target_path: Mapped[str] = mapped_column(Text)
    expected_size: Mapped[int | None] = mapped_column(BigInteger)
    progress_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    checksum: Mapped[str | None] = mapped_column(String(128))
    checksum_type: Mapped[str | None] = mapped_column(String(32))
    confirmation_id: Mapped[str | None] = mapped_column(
        ForeignKey("confirmations.id", ondelete="RESTRICT")
    )
    failure_code: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    job: Mapped[JobRow] = relationship(back_populates="tasks")

    __table_args__ = (Index("ix_tasks_job_file", "job_id", "file_key"),)


class TaskEventRow(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConfirmationRow(Base):
    __tablename__ = "confirmations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(32))
    target_key: Mapped[str] = mapped_column(Text)
    failure_code: Mapped[str] = mapped_column(String(64))
    estimated_bytes: Mapped[int] = mapped_column(BigInteger)
    failure_snapshot_json: Mapped[str] = mapped_column(Text)
    plan_hash: Mapped[str] = mapped_column(String(64))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_confirmations_scope_target", "job_id", "scope", "target_key"),)


class BackendRow(Base):
    __tablename__ = "backends"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(Text, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    capabilities_json: Mapped[str] = mapped_column(Text, default="{}")


class RegionRow(Base):
    __tablename__ = "regions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    source_path: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_crs: Mapped[str] = mapped_column(Text)
    normalized_crs: Mapped[str] = mapped_column(Text)
    geometry_wkb: Mapped[bytes] = mapped_column()
    bbox_json: Mapped[str] = mapped_column(Text)
    repaired: Mapped[bool] = mapped_column(Boolean, default=False)
    selected_feature_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    year: Mapped[int | None] = mapped_column(Integer)
