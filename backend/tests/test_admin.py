"""Roles and account administration."""

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli
from app.models import UserRole
from app.services.admin_service import set_role_by_email
from tests.conftest import DEFAULT_PASSWORD, RegisterFn, bearer

API = "/api/v1"
USERS = f"{API}/admin/users"


async def login(client: AsyncClient, email: str) -> AsyncClient:
    return await client.post(f"{API}/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})


@pytest.fixture
async def admin(register_user: RegisterFn, db: AsyncSession) -> dict[str, str]:
    body = await register_user(email="admin@example.com", full_name="Ada Admin")
    await set_role_by_email(db, "admin@example.com", UserRole.ADMIN)
    return bearer(body["access_token"])


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict:
    body = await register_user(email="alice@example.com", full_name="Alice")
    return {"headers": bearer(body["access_token"]), "id": body["user"]["id"]}


async def test_new_accounts_are_regular_users(client: AsyncClient, alice: dict) -> None:
    me = (await client.get(f"{API}/auth/me", headers=alice["headers"])).json()

    assert me["role"] == "user"


async def test_admin_lists_accounts_with_counts_but_no_content(
    client: AsyncClient, admin: dict, alice: dict
) -> None:
    kb = (
        await client.post(f"{API}/knowledge-bases", json={"name": "Salaries"}, headers=alice["headers"])
    ).json()
    await client.post(
        f"{API}/documents/upload",
        data={"knowledge_base_id": kb["id"]},
        files={"file": ("payroll.txt", b"Confidential payroll figures.")},
        headers=alice["headers"],
    )
    await client.post(f"{API}/chat", json={"message": "Hello"}, headers=alice["headers"])

    response = await client.get(USERS, headers=admin)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    row = next(user for user in body["items"] if user["email"] == "alice@example.com")
    assert (row["knowledge_bases"], row["documents"], row["conversations"]) == (1, 1, 1)
    assert row["role"] == "user" and row["is_active"] is True
    for private in ("Salaries", "payroll", "Confidential", "Hello"):
        assert private not in response.text


async def test_search_matches_email_or_name_and_treats_wildcards_literally(
    client: AsyncClient, admin: dict, alice: dict
) -> None:
    by_email = (await client.get(USERS, params={"search": "ALICE@"}, headers=admin)).json()
    by_name = (await client.get(USERS, params={"search": "ada adm"}, headers=admin)).json()
    wildcard = (await client.get(USERS, params={"search": "%"}, headers=admin)).json()

    assert [u["email"] for u in by_email["items"]] == ["alice@example.com"]
    assert [u["email"] for u in by_name["items"]] == ["admin@example.com"]
    assert wildcard == {"items": [], "total": 0}


async def test_pagination_reports_the_total(
    client: AsyncClient, admin: dict, register_user: RegisterFn
) -> None:
    for number in range(3):
        await register_user(email=f"user{number}@example.com")

    page = (await client.get(USERS, params={"limit": 2, "offset": 2}, headers=admin)).json()

    assert page["total"] == 4 and len(page["items"]) == 2


async def test_disabling_an_account_ends_its_sessions_at_once(
    client: AsyncClient, admin: dict, alice: dict, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    response = await client.patch(f"{USERS}/{alice['id']}", json={"is_active": False}, headers=admin)

    assert response.status_code == 200 and response.json()["is_active"] is False
    # Her existing token stops working on the next request, and she cannot log in again.
    assert (await client.get(f"{API}/auth/me", headers=alice["headers"])).status_code == 401
    assert (await login(client, "alice@example.com")).status_code == 403

    await client.patch(f"{USERS}/{alice['id']}", json={"is_active": True}, headers=admin)
    assert (await login(client, "alice@example.com")).status_code == 200

    [record, _] = [r for r in caplog.records if r.getMessage() == "admin_user_updated"]
    assert record.target_user_id == alice["id"] and record.is_active is False and record.actor_id
    assert "alice@example.com" not in str(record.__dict__)


async def test_role_changes_apply_to_existing_sessions(client: AsyncClient, admin: dict, alice: dict) -> None:
    assert (await client.get(USERS, headers=alice["headers"])).status_code == 403

    await client.patch(f"{USERS}/{alice['id']}", json={"role": "admin"}, headers=admin)
    assert (await client.get(USERS, headers=alice["headers"])).status_code == 200  # same token

    await client.patch(f"{USERS}/{alice['id']}", json={"role": "user"}, headers=admin)
    assert (await client.get(USERS, headers=alice["headers"])).status_code == 403


async def test_an_admin_cannot_lock_themselves_out(client: AsyncClient, admin: dict) -> None:
    me = (await client.get(f"{API}/auth/me", headers=admin)).json()

    demote = await client.patch(f"{USERS}/{me['id']}", json={"role": "user"}, headers=admin)
    disable = await client.patch(f"{USERS}/{me['id']}", json={"is_active": False}, headers=admin)
    unchanged = await client.patch(f"{USERS}/{me['id']}", json={"role": "admin"}, headers=admin)

    assert demote.status_code == 409 and disable.status_code == 409
    assert unchanged.status_code == 200
    assert (await client.get(f"{API}/auth/me", headers=admin)).json()["role"] == "admin"


async def test_invalid_updates(client: AsyncClient, admin: dict, alice: dict) -> None:
    unknown = await client.patch(
        f"{USERS}/00000000-0000-0000-0000-000000000000", json={"role": "admin"}, headers=admin
    )
    empty = await client.patch(f"{USERS}/{alice['id']}", json={}, headers=admin)
    bad_role = await client.patch(f"{USERS}/{alice['id']}", json={"role": "owner"}, headers=admin)

    assert unknown.status_code == 404
    assert empty.status_code == 422 and bad_role.status_code == 422


# --- command line -----------------------------------------------------------------------------


async def test_set_role_by_email(db: AsyncSession, alice: dict) -> None:
    user = await set_role_by_email(db, "  Alice@Example.com ", UserRole.ADMIN)
    assert user.role is UserRole.ADMIN

    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError, match="Sign up first"):
        await set_role_by_email(db, "nobody@example.com", UserRole.ADMIN)


def test_cli_parses_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_set_role(email: str, role: UserRole) -> int:
        calls.append((email, role))
        return 0

    monkeypatch.setattr(cli, "_set_role", fake_set_role)

    assert cli.main(["promote", "a@example.com"]) == 0
    assert cli.main(["demote", "a@example.com"]) == 0
    assert calls == [("a@example.com", UserRole.ADMIN), ("a@example.com", UserRole.USER)]
    with pytest.raises(SystemExit):
        cli.main(["delete", "a@example.com"])
