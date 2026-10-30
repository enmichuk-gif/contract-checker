#!/usr/bin/env python3
"""Точка входа: python3 run.py [--host 127.0.0.1] [--port 8000]."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.server import main  # noqa: E402

if __name__ == "__main__":
    main()
