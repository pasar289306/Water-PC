import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="aqua_test_")
os.environ["AQUA_DATA_DIR"] = _tmp
os.environ["AQUA_DB"] = os.path.join(_tmp, "test.db")
os.environ["AQUA_ADMIN_PASSWORD"] = "AdminPass123"
os.environ["AQUA_SECRET"] = "test-secret-key-for-unit-tests-only-0123456789"
os.environ["AQUA_DEMO"] = "1"

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin(client):
    t = client.post("/api/auth/login", json={"username": "admin", "password": "AdminPass123"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="session")
def viewer(client, admin):
    client.post("/api/users", headers=admin, json={"username": "viewer1", "password": "ViewerPass1", "role": "viewer"})
    t = client.post("/api/auth/login", json={"username": "viewer1", "password": "ViewerPass1"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}
