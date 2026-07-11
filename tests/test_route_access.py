"""
Route access control — turns the manual security audit into permanent
regression tests. If any of these ever start failing, a future change
has silently reopened a gap we deliberately closed.
"""
import pytest


# (method, path, expected_status_for_cashier) — cashier is the lowest role
# that can still log in, so it's the most useful one to sweep broadly.
OWNER_MANAGER_ONLY_GET_ROUTES = [
    "/reports",
    "/reports/eod",
    "/export/csv/inventory",
    "/export/csv/customers",
    "/export/csv/invoices",
    "/export/csv/tax-report",
    "/export/csv/reorder",
    "/products/reorder",
    "/products/reorder/print",
    "/staff",
    "/audit",
    "/suppliers",
    "/purchase-orders",
]

OWNER_ONLY_GET_ROUTES = [
    "/settings",
]


@pytest.mark.parametrize("path", OWNER_MANAGER_ONLY_GET_ROUTES)
def test_owner_manager_only_routes_block_cashier(cashier_client, path):
    resp = cashier_client.get(path, follow_redirects=False)
    assert resp.status_code == 403, f"{path} should block cashiers, got {resp.status_code}"


@pytest.mark.parametrize("path", OWNER_MANAGER_ONLY_GET_ROUTES)
def test_owner_manager_only_routes_allow_manager(manager_client, path):
    resp = manager_client.get(path, follow_redirects=False)
    assert resp.status_code == 200, f"{path} should allow managers, got {resp.status_code}"


@pytest.mark.parametrize("path", OWNER_ONLY_GET_ROUTES)
def test_owner_only_routes_block_manager(manager_client, path):
    resp = manager_client.get(path, follow_redirects=False)
    assert resp.status_code == 403, f"{path} should block managers, got {resp.status_code}"


@pytest.mark.parametrize("path", OWNER_ONLY_GET_ROUTES)
def test_owner_only_routes_allow_owner(owner_client, path):
    resp = owner_client.get(path, follow_redirects=False)
    assert resp.status_code == 200, f"{path} should allow the owner, got {resp.status_code}"


def test_product_add_blocked_for_cashier(cashier_client):
    resp = cashier_client.post("/products/add", data={
        "sku": "HACK-1", "name": "Hacked Item", "price": "0.01", "cost": "0",
    }, follow_redirects=False)
    assert resp.status_code == 403


def test_product_add_allowed_for_manager(manager_client):
    resp = manager_client.post("/products/add", data={
        "sku": "TEST-SKU-1", "name": "Test Widget", "price": "9.99", "cost": "3.00", "stock": "10",
    }, follow_redirects=False)
    assert resp.status_code == 303  # redirect back to /products on success


def test_protected_routes_redirect_anonymous_to_login(anon_client):
    for path in ["/", "/pos", "/repairs", "/reports", "/staff", "/settings"]:
        resp = anon_client.get(path, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login", f"{path} should redirect anonymous users to /login"


def test_manager_cannot_edit_owner_account(manager_client, db_session):
    from app.models import Staff
    owner = db_session.query(Staff).filter(Staff.role == "owner").first()
    resp = manager_client.post(f"/staff/{owner.id}/edit", data={
        "name": owner.name, "role": "owner", "new_pin": "9999",
    }, follow_redirects=False)
    assert resp.status_code == 403


def test_manager_cannot_self_promote_to_owner(manager_client, db_session):
    from app.models import Staff
    manager = db_session.query(Staff).filter(Staff.id == "test-manager").first()
    resp = manager_client.post(f"/staff/{manager.id}/edit", data={
        "name": manager.name, "role": "owner",
    }, follow_redirects=False)
    assert resp.status_code == 403
    db_session.refresh(manager)
    assert manager.role == "manager", "role must not have changed despite the blocked request"


def test_manager_cannot_deactivate_owner(manager_client, db_session):
    from app.models import Staff
    owner = db_session.query(Staff).filter(Staff.role == "owner").first()
    resp = manager_client.post(f"/staff/{owner.id}/toggle", follow_redirects=False)
    assert resp.status_code == 403
    db_session.refresh(owner)
    assert owner.active is True
