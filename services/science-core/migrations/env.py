from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from open_science_core import models

config = context.config
target_metadata = models.Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # SQLite batch migrations rebuild tables with DROP/RENAME. Leaving
        # foreign_keys enabled would apply ON DELETE CASCADE while dropping a
        # parent (for example tasks or answers), deleting legacy child rows
        # before Alembic can recreate the table. Disable enforcement outside a
        # transaction, restore it in all cases, then fail closed on violations.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
        # SQLAlchemy 2 autobegins even for PRAGMA statements. End that implicit
        # transaction so Alembic owns and commits the migration transaction,
        # including its alembic_version row.
        connection.commit()
        succeeded = False
        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                transaction_per_migration=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            succeeded = True
        finally:
            if connection.in_transaction():
                if succeeded:
                    connection.commit()
                else:
                    connection.rollback()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"Migration left {len(violations)} foreign-key violation(s)."
            )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
