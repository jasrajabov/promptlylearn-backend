from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
import sys
import os
from alembic import context

# Add your app folder to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.database import Base, SQLALCHEMY_DATABASE_URL  # your Base and DB URL
from src.models import *  # import all models so Alembic can detect them

# Alembic Config object
config = context.config

# Logging config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# IMPORTANT: set target_metadata to your Base.metadata
target_metadata = Base.metadata


# Offline migrations
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# Online migrations
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=SQLALCHEMY_DATABASE_URL,  # override URL
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
