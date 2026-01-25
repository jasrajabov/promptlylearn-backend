import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

load_dotenv()

SQLALCHEMY_DATABASE_URL: str = os.getenv("DATABASE_URL", "")

if not SQLALCHEMY_DATABASE_URL:
    logger.error("SQLALCHEMY_DATABASE_URL is not found in env variables")


engine = create_engine(
    SQLALCHEMY_DATABASE_URL, echo=False
)  # echo=True logs SQL queries
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
