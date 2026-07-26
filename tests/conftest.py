"""
在导入 fastapi_service 之前设置测试用环境变量与独立数据目录。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_root = Path(__file__).resolve().parent / ".pytest_rag_data"
_root.mkdir(parents=True, exist_ok=True)
os.environ["RAG_ACCESS_TOKEN"] = "pytest-token-secure"
os.environ["RAG_REQUIRE_AUTH"] = "0"
os.environ["RAG_DATA_DIR"] = str(_root)
os.environ["RAG_CACHE_ROOT"] = str(_root / "vector_cache")
os.environ["RAG_UPLOAD_DIR"] = str(_root / "uploads")


@pytest.fixture()
def client() -> TestClient:
    from fastapi_service import app

    return TestClient(app)
