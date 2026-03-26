"""
WSGI エントリポイント。
本番デプロイ時に使用（gunicorn / uvicorn --factory など）。

起動例:
  # 開発
  uvicorn app.main:app --reload

  # 本番 (gunicorn + uvicorn workers)
  gunicorn app.main:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000

  # または
  uvicorn wsgi:app --host 0.0.0.0 --port 8000
"""

import os
from dotenv import load_dotenv

# .env を読み込んで環境変数にセット
load_dotenv()

from app.main import app  # noqa: E402

__all__ = ["app"]
