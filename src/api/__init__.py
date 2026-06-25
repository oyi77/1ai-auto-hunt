"""REST API layer for 1ai-auto-hunt.

Exposes the FastAPI application with all hunt routers mounted,
JWT authentication, CORS, and OpenAPI documentation.
"""

from src.api.app import create_app

__all__ = ["create_app"]
