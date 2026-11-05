from httpx import AsyncClient


async def _token(client: AsyncClient, username: str = "u1") -> str:
    res = await client.post("/auth/register", json={"username": username, "password": "pw"})
    return res.json()["access_token"]


async def test_get_cv_empty(client: AsyncClient):
    token = await _token(client)
    res = await client.get("/users/cv", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == {"cv_text": None, "has_cv": False}


def H(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


async def test_extract_returns_text_and_stores_nothing(client: AsyncClient):
    t = await _token(client, "e1")
    res = await client.post("/users/cv/extract", headers=H(t), files={"file": ("cv.txt", b"Python dev", "text/plain")})
    assert res.json() == {"text": "Python dev"}
    assert (await client.get("/users/cv", headers=H(t))).json()["has_cv"] is False


async def test_extract_unsupported_format(client: AsyncClient):
    t = await _token(client, "e2")
    res = await client.post("/users/cv/extract", headers=H(t), files={"file": ("cv.xlsx", b"x", "application/vnd.ms-excel")})
    assert res.status_code == 400


async def test_extract_requires_auth(client: AsyncClient):
    res = await client.post("/users/cv/extract", files={"file": ("cv.txt", b"text", "text/plain")})
    assert res.status_code == 401


async def test_redact_stores_nothing(client: AsyncClient):
    t = await _token(client, "r1")
    res = await client.post("/users/cv/redact", headers=H(t), json={"text": "mail: a@b.com"})
    assert res.json() == {"text": "mail: [EMAIL]", "removed": {"email": 1}}
    assert (await client.get("/users/cv", headers=H(t))).json()["has_cv"] is False


async def test_save_with_private_info_needs_confirm(client: AsyncClient):
    t = await _token(client, "s1")
    res = await client.put("/users/cv", headers=H(t), json={"text": "mail: a@b.com"})
    assert res.status_code == 409 and res.json() == {"detail": {"findings": {"email": 1}}}
    assert (await client.get("/users/cv", headers=H(t))).json()["has_cv"] is False
    res = await client.put("/users/cv", headers=H(t), json={"text": "mail: a@b.com", "confirm": True})
    assert res.json() == {"cv_text": "mail: a@b.com", "has_cv": True}


async def test_save_clean_text(client: AsyncClient):
    t = await _token(client, "s2")
    res = await client.put("/users/cv", headers=H(t), json={"text": "Python dev, 5 years"})
    assert res.status_code == 200
    assert (await client.get("/users/cv", headers=H(t))).json()["cv_text"] == "Python dev, 5 years"


async def test_save_replaces_previous(client: AsyncClient):
    t = await _token(client, "s5")
    await client.put("/users/cv", headers=H(t), json={"text": "old cv"})
    await client.put("/users/cv", headers=H(t), json={"text": "new cv content"})
    assert (await client.get("/users/cv", headers=H(t))).json()["cv_text"] == "new cv content"


async def test_save_empty_is_refused(client: AsyncClient):
    t = await _token(client, "s3")
    assert (await client.put("/users/cv", headers=H(t), json={"text": "  \n"})).status_code == 400


async def test_old_upload_endpoint_is_gone(client: AsyncClient):
    t = await _token(client, "s4")
    res = await client.post("/users/cv", headers=H(t), files={"file": ("cv.txt", b"x", "text/plain")})
    assert res.status_code == 405
