"""
Compatibility module for the ASTA FastAPI application.

The real application lives in app.main. This module is kept only so older
commands that referenced ``app.llm.router:app`` continue to work.
"""

from app.main import app

__all__ = ["app"]
