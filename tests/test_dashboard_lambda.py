"""The dashboard behind API Gateway: same pages, session in a signed cookie.

The stdlib server's tests cover rendering and password checking; these cover
what is different on Lambda — that a cookie any instance can verify is issued,
that a forged or expired one is refused, and that the stage prefix is honoured
so links and form actions stay inside the deployment.
"""

from __future__ import annotations

import pytest

from src import dashboard, dashboard_lambda as dl


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "correct-horse")
    dashboard._attempts.clear()
    yield
    dashboard._attempts.clear()


def event(method="GET", path="/dev/dashboard", *, cookie=None, body=None, query=None, ip="1.2.3.4"):
    ev = {
        "rawPath": path,
        "requestContext": {"stage": "dev", "http": {"method": method, "path": path, "sourceIp": ip}},
        "queryStringParameters": query or {},
        "body": body or "",
    }
    if cookie:
        ev["cookies"] = [f"{dl.COOKIE}={cookie}"]
    return ev


def _cookie_of(resp):
    return resp["cookies"][0].split(";")[0].split("=", 1)[1]


def test_without_a_session_the_dashboard_redirects_to_login():
    resp = dl.lambda_handler(event())
    assert resp["statusCode"] == 303
    assert resp["headers"]["Location"] == "/dev/login"


def test_login_issues_a_cookie_that_opens_the_dashboard():
    resp = dl.lambda_handler(event("POST", "/dev/login", body="password=correct-horse"))
    assert resp["statusCode"] == 303 and resp["headers"]["Location"] == "/dev/dashboard"
    assert "HttpOnly" in resp["cookies"][0] and "Secure" in resp["cookies"][0]
    page = dl.lambda_handler(event(cookie=_cookie_of(resp)))
    assert page["statusCode"] == 200
    assert "לוח בקרה" in page["body"]


def test_wrong_password_is_refused_and_counted():
    resp = dl.lambda_handler(event("POST", "/dev/login", body="password=nope"))
    assert resp["statusCode"] == 401 and "cookies" not in resp
    for _ in range(4):
        dl.lambda_handler(event("POST", "/dev/login", body="password=nope"))
    assert dl.lambda_handler(event("POST", "/dev/login", body="password=correct-horse"))["statusCode"] == 429


def test_a_forged_or_expired_cookie_is_not_a_session(monkeypatch):
    good = dl.issue_session()
    expiry, _, mac = good.partition(".")
    assert not dl.valid_session(f"{int(expiry) + 999999}.{mac}"), "changing the expiry must break the MAC"
    assert not dl.valid_session(f"{expiry}.{mac[:-2]}AA")
    assert not dl.valid_session(good, now=int(expiry) + 1), "expired"
    assert dl.valid_session(good)
    monkeypatch.setenv("SIGN_LINK_SECRET", "another-deployment")
    assert not dl.valid_session(good), "a cookie from another stack's secret is worthless here"


def test_dashboard_cookie_is_not_a_signing_token():
    # Same secret, different MAC domain: the dashboard session must not verify
    # as anything the signing module would accept, and vice versa.
    from src.lib import signing

    with pytest.raises(signing.SigningError):
        signing.read_token(dl.issue_session())


def test_links_and_forms_stay_under_the_stage_prefix():
    login = dl.lambda_handler(event("GET", "/dev/login"))
    assert 'action="/dev/login"' in login["body"]
    resp = dl.lambda_handler(event("POST", "/dev/login", body="password=correct-horse"))
    page = dl.lambda_handler(event(cookie=_cookie_of(resp)))
    assert 'action="/dev/dashboard"' in page["body"] and 'href="/dev/logout"' in page["body"]


def test_logout_clears_the_cookie():
    resp = dl.lambda_handler(event("GET", "/dev/logout"))
    assert resp["statusCode"] == 303 and "Max-Age=0" in resp["cookies"][0]


def test_unknown_route_is_404_and_healthz_is_public():
    assert dl.lambda_handler(event("GET", "/dev/nope"))["statusCode"] == 404
    assert dl.lambda_handler(event("GET", "/dev/healthz"))["statusCode"] == 200
