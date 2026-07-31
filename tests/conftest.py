import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A shared password for test accounts. Kept short on purpose: scrypt is
# deliberately slow, and every login in the suite pays for it.
TEST_PASSWORD = "test-password"


def signin(client, db, role="admin", username=None, name=None):
    """Create an operator account and sign `client` in as it.

    The operator endpoints require a real session, so any test that touches
    them needs this. Returns the username.
    """
    username = username or role
    if db.get_user(username) is None:
        db.add_user(username, name or username.title(), role, TEST_PASSWORD)
    r = client.post("/api/login",
                    data={"username": username, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return username
