"""Short SQLite acceptance boundary for legacy committing domain helpers.

A real outer transaction owns durability. Helper commits release SAVEPOINTs,
never the outer transaction; no provider await is permitted inside this scope.
"""
from contextlib import contextmanager
from contextvars import ContextVar

# Propagated only through the claimed outbox coroutine, never a process global.
compression_post_commit_fence: ContextVar[tuple[str, str, int] | None] = ContextVar("compression_post_commit_fence", default=None)
isolated_mock_execution: ContextVar[bool] = ContextVar("isolated_mock_execution", default=False)

from sqlmodel import Session


@contextmanager
def acceptance_transaction(request_session: Session):
    bind = request_session.get_bind()
    request_session.rollback()  # End only the route's read/autobegin transaction.
    with bind.connect() as connection:
        if connection.dialect.name == 'sqlite':
            # Explicit BEGIN is essential: pysqlite's deferred BEGIN otherwise
            # lets the first released savepoint become durable prematurely.
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False) as session:
                yield session
                session.commit()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    request_session.expire_all()
