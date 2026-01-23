from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import logging
import time
import os

from src.routes import (
    authentication,
    chat,
    course,
    roadmap,
    tasks,
    quiz,
    payment,
    user,
    admin,
)
from src.config.logging_config import setup_logging

setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Application startup - Initializing services...")
    yield
    logger.info("Application shutdown - Cleaning up...")


app = FastAPI(lifespan=lifespan)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session Middleware for OAuth
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY"),
    max_age=3600,
    same_site="lax",
    https_only=False,
)
logger.info("Session middleware configured")


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests and responses."""
    request_logger = logging.getLogger("app.requests")

    # Log incoming request
    request_logger.info(
        f"{request.method} {request.url.path}",
        extra={
            "method": request.method,
            "path": request.url.path,
            "client_ip": request.client.host if request.client else "unknown",
        },
    )

    start_time = time.time()

    try:
        response = await call_next(request)
        process_time = time.time() - start_time

        # Log response
        request_logger.info(
            f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration": f"{process_time:.3f}s",
            },
        )

        return response

    except Exception:
        process_time = time.time() - start_time
        request_logger.exception(
            f"✗ {request.method} {request.url.path} - Error after {process_time:.3f}s"
        )
        raise


# Include routers
app.include_router(user.router)
app.include_router(authentication.router)
app.include_router(chat.router)
app.include_router(quiz.router)
app.include_router(tasks.router)
app.include_router(course.router)
app.include_router(roadmap.router)
app.include_router(payment.router)
app.include_router(admin.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    logger.debug("Health check called")
    return {"status": "healthy"}


# Optional: Add global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all unhandled exceptions."""
    error_logger = logging.getLogger("app.errors")
    error_logger.exception(
        f"Unhandled exception on {request.method} {request.url.path}: {str(exc)}"
    )
    return {
        "detail": "Internal server error",
        "path": request.url.path,
    }


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting FastAPI application via main...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)
