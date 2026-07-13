from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(
            f"sqlite:///{path.as_posix()}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        event.listen(self.engine, "connect", self._configure_connection)
        self._sessions = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    @staticmethod
    def _configure_connection(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    def initialize(self) -> None:
        migration_root = Path(__file__).with_name("migrations")
        config = Config()
        config.set_main_option("script_location", migration_root.as_posix())
        config.set_main_option("sqlalchemy.url", f"sqlite:///{self.path.as_posix()}")
        existing = set(inspect(self.engine).get_table_names())
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            if existing and "alembic_version" not in existing:
                command.stamp(config, "head")
            else:
                command.upgrade(config, "head")

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def sqlite_version(engine: Engine) -> str:
    with engine.connect() as connection:
        return str(connection.exec_driver_sql("select sqlite_version()").scalar_one())
