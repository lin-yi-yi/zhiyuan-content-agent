"""Separate control database for the single-host SaaS pilot.

No imports or writes touch the legacy business database. Callers own commits;
multi-row membership changes use SQLite BEGIN IMMEDIATE transactions.
"""
import importlib
import importlib.util
import os
from pathlib import Path
from threading import RLock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class ControlBase(DeclarativeBase):
    pass


CONTROL_LOCK = RLock()
_engines = {}
_factories = {}


def _control_path():
    directory = Path(os.getenv("SAAS_DATA_DIR", str(Path(__file__).resolve().parents[3] / ".data" / "saas"))).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / "control.db"


def get_control_engine():
    path = str(_control_path())
    with CONTROL_LOCK:
        if path not in _engines:
            engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30}, pool_pre_ping=True)

            @event.listens_for(engine, "connect")
            def _pragmas(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")

            _engines[path] = engine
            _factories[path] = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        return _engines[path]


def init_control_db():
    importlib.import_module("app.saas.models")
    if importlib.util.find_spec("app.saas.commerce_models") is not None:
        importlib.import_module("app.saas.commerce_models")
    engine = get_control_engine()
    ControlBase.metadata.create_all(engine)
    os.chmod(_control_path(), 0o600)


def session_factory():
    get_control_engine()
    return _factories[str(_control_path())]()


def get_control_db():
    with session_factory() as db:
        yield db


def close_control_db():
    with CONTROL_LOCK:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _factories.clear()
