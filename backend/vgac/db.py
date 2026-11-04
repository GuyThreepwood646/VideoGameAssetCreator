"""SQLAlchemy models and SQLite session factory.

Tables: project, asset, version, job, matching docs/API.md. Ids are uuid4 strings. JSON-valued
fields are stored as text columns with dict accessors. Schema management is create_all only
(no migrations): delete library/vgac.sqlite when the schema changes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import get_settings

STAGES = ("concept", "turnaround", "mesh", "prep", "rig", "export")
VERSION_STATUSES = ("queued", "running", "done", "error", "rejected")
JOB_STATUSES = ("queued", "running", "done", "error", "cancelled")
INSTANCES = ("a", "b", "host")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    style_lock_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    platform_target: Mapped[str] = mapped_column(String(50), nullable=False, default="pc_mid")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    assets: Mapped[list["Asset"]] = relationship(back_populates="project", cascade="all, delete-orphan")

    @property
    def style_lock(self) -> dict[str, Any]:
        return _loads(self.style_lock_json, {})

    @style_lock.setter
    def style_lock(self, value: dict[str, Any]) -> None:
        self.style_lock_json = _dumps(value or {})


class Asset(Base):
    __tablename__ = "asset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="humanoid")
    style_preset: Mapped[str] = mapped_column(String(50), nullable=False, default="painted")
    platform_target: Mapped[str] = mapped_column(String(50), nullable=False, default="pc_mid")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    base_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    variant_change: Mapped[str | None] = mapped_column(Text, nullable=True)
    concept_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="assets")
    versions: Mapped[list["Version"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="Version.created_at"
    )

    @property
    def parts(self) -> list[str]:
        return _loads(self.parts_json, [])

    @parts.setter
    def parts(self, value: list[str]) -> None:
        self.parts_json = _dumps(list(value or []))


class Version(Base):
    __tablename__ = "version"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("asset.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey("version.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    files_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="versions")
    parent: Mapped["Version | None"] = relationship(remote_side=[id])

    @property
    def params(self) -> dict[str, Any]:
        return _loads(self.params_json, {})

    @params.setter
    def params(self, value: dict[str, Any]) -> None:
        self.params_json = _dumps(value or {})

    @property
    def files(self) -> dict[str, str]:
        return _loads(self.files_json, {})

    @files.setter
    def files(self, value: dict[str, str]) -> None:
        self.files_json = _dumps(value or {})

    @property
    def metrics(self) -> dict[str, Any]:
        return _loads(self.metrics_json, {})

    @metrics.setter
    def metrics(self, value: dict[str, Any]) -> None:
        self.metrics_json = _dumps(value or {})


class Job(Base):
    __tablename__ = "job"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    instance: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def payload(self) -> dict[str, Any]:
        return _loads(self.payload_json, {})

    @payload.setter
    def payload(self, value: dict[str, Any]) -> None:
        self.payload_json = _dumps(value or {})


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        url = "sqlite:///" + settings.db_path.as_posix()
        _engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Dispose the engine so the next call rebuilds it from current settings (tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_columns(engine)


_ADDED_COLUMNS = {"asset": {"base_asset_id": "VARCHAR(36)", "variant_change": "TEXT", "concept_model": "VARCHAR(50)"}}


def _ensure_columns(engine) -> None:
    """Tiny forward-only migration for columns added after create_all shipped (SQLite ALTER TABLE ADD COLUMN)."""
    from sqlalchemy import text
    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
            for col, ddl in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Serialisation to the API shapes
# ---------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def project_to_dict(p: Project) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "created_at": _iso(p.created_at),
        "style_lock": p.style_lock,
        "platform_target": p.platform_target,
    }


def version_to_dict(v: Version) -> dict[str, Any]:
    return {
        "id": v.id,
        "asset_id": v.asset_id,
        "stage": v.stage,
        "parent_version_id": v.parent_version_id,
        "status": v.status,
        "params": v.params,
        "files": v.files,
        "metrics": v.metrics,
        "job_id": v.job_id,
        "error": v.error,
        "created_at": _iso(v.created_at),
        "selected": bool(v.selected),
    }


def asset_to_dict(a: Asset, with_stages: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": a.id,
        "project_id": a.project_id,
        "name": a.name,
        "category": a.category,
        "style_preset": a.style_preset,
        "platform_target": a.platform_target,
        "prompt": a.prompt,
        "parts": a.parts,
        "base_asset_id": a.base_asset_id,
        "variant_change": a.variant_change,
        "concept_model": a.concept_model,
        "created_at": _iso(a.created_at),
        "selected": {},
        "stages": {s: [] for s in STAGES},
    }
    if with_stages:
        for v in sorted(a.versions, key=lambda x: (x.created_at, x.id)):
            out["stages"].setdefault(v.stage, []).append(version_to_dict(v))
            if v.selected:
                out["selected"][v.stage] = v.id
    return out


def job_to_dict(j: Job) -> dict[str, Any]:
    return {
        "id": j.id,
        "instance": j.instance,
        "kind": j.kind,
        "asset_id": j.asset_id,
        "version_id": j.version_id,
        "status": j.status,
        "progress": float(j.progress or 0.0),
        "message": j.message or "",
        "created_at": _iso(j.created_at),
        "started_at": _iso(j.started_at),
        "finished_at": _iso(j.finished_at),
        "error": j.error,
    }
