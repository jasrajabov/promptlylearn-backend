"""
Simple logging configuration for FastAPI
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(level: str = "INFO") -> None:
    """
    Configure application-wide logging with timestamps.
    Call this once at application startup.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Create logs directory
    Path("logs").mkdir(exist_ok=True)

    # Create formatters
    console_formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(console_formatter)

    # File handler (all logs: DEBUG, INFO, WARNING, ERROR, CRITICAL) - rotates at 10MB
    file_handler = RotatingFileHandler(
        "logs/app.log", maxBytes=10485760, backupCount=10
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Error file handler (errors only) - rotates at 10MB
    error_handler = RotatingFileHandler(
        "logs/errors.log",
        maxBytes=10485760,  # 10MB
        backupCount=5,
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture everything at root level
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)

    # Reduce uvicorn access log noise (optional)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    # Suppress passlib bcrypt version warnings
    logging.getLogger("passlib").setLevel(logging.ERROR)
