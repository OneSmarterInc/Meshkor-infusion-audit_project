"""API tests: Django test client against the pipeline output built from TEST data."""
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"


@pytest.fixture
def client(built, db):
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "auditapi.settings")
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.test import Client
    user = get_user_model().objects.create_superuser(
        username="audit-test-admin", email="audit-test@example.invalid", password="test-only-password"
    )
    test_client = Client()
    test_client.force_login(user)
    return test_client


def test_meta(client):
    r = client.get("/api/meta").json()
    assert r["asp_quarters"] == ["2026Q3", "2026Q4"]
    assert r["hospitals"] == 4
    assert {s["status"] for s in r["statuses"]} >= {"ok", "unit_unresolved"}


def test_unified_health_endpoint(client):
    response = client.get("/api/health/")
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "ok"
    assert response.json()["checks"] == {"api": "ok", "database": "ok", "parquet": "ok"}


def test_backend_get_route_matrix_returns_expected_success(client):
    paths = [
        "/api/v1/dashboard/summary/",
        "/api/v1/dashboard/price-trends/",
        "/api/v1/sources/",
        "/api/v1/sources/jobs/",
        "/api/v1/reference/",
        "/api/v1/reference/asp/",
        "/api/v1/drug-scope/",
        "/api/v1/unit-validations/",
        "/api/v1/settings/",
        "/api/v1/audit-logs/",
        "/api/v1/hospital-files/",
        "/api/v1/hospitals/",
        "/api/v1/drugs/",
        "/api/v1/comparisons/",
        "/api/v1/comparisons.csv",
        "/api/v1/comparisons.xlsx",
        "/api/v1/quality/",
        "/api/v1/reports/",
        "/api/v1/reports/market.pdf",
        "/api/v1/auth/otp/",
    ]
    failures = []
    for path in paths:
        response = client.get(path)
        if response.status_code != 200:
                failures.append(f"{path}: {response.status_code} {response.content[:300]!r}")
    assert not failures, "; ".join(failures)


@pytest.mark.django_db
def test_reviewer_can_see_current_draft_needed_for_report_release(client):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.test import Client
    from pricing import data
    from pricing.models import ReportRecord
    from pricing.permissions import REVIEWER_GROUP

    builder = get_user_model().objects.get(username="audit-test-admin")
    report = ReportRecord.objects.create(
        name="Current test report", status="generated", built_by=builder,
        source_hash=data.comparison_sha256(), config_hash=data.config_sha256(),
    )
    reviewer = get_user_model().objects.create_user(
        username="release-reviewer", password="test-only-password", is_active=True
    )
    reviewer.groups.add(Group.objects.get_or_create(name=REVIEWER_GROUP)[0])
    reviewer_client = Client()
    reviewer_client.force_login(reviewer)

    response = reviewer_client.get("/api/v1/reports/")
    assert response.status_code == 200, response.content
    assert response.json()[0]["id"] == report.pk
    assert response.json()[0]["status"] == "generated"
    assert response.json()[0]["built_by"] == builder.username
    assert "metadata" not in response.json()[0]


def test_protected_analytics_require_authentication(client):
    from django.test import Client

    anonymous = Client()
    for path in ("/api/v1/dashboard/summary/", "/api/v1/comparisons/",
                 "/api/v1/sources/", "/api/v1/audit-logs/"):
        assert anonymous.get(path).status_code == 401


def test_two_role_model_keeps_viewer_out_of_admin_operations(client):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.test import Client
    from pricing.permissions import VIEWER_GROUP

    viewer = get_user_model().objects.create_user(
        username="viewer-only", password="long-test-password-123", is_active=True
    )
    viewer.groups.add(Group.objects.get_or_create(name=VIEWER_GROUP)[0])
    viewer_client = Client()
    viewer_client.force_login(viewer)
    assert viewer_client.get("/api/v1/dashboard/summary/").status_code == 403
    assert viewer_client.get("/api/v1/sources/").status_code == 403
    assert viewer_client.get("/api/v1/sources/jobs/").status_code == 403
    assert viewer_client.get("/api/v1/drug-selection-scope/").status_code == 403
    assert viewer_client.get("/api/v1/user-accounts/").status_code == 403


def test_staff_flag_alone_does_not_grant_application_admin(client):
    from django.contrib.auth import get_user_model
    from django.test import Client

    staff = get_user_model().objects.create_user(
        username="staff-no-role", password="long-test-password-123", is_active=True,
        is_staff=True,
    )
    staff_client = Client()
    staff_client.force_login(staff)
    assert staff_client.get("/api/v1/dashboard/summary/").status_code == 403


def test_root_superuser_is_protected_in_django_admin(client):
    from django.contrib import admin
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory
    from pricing.admin import ProtectedRootAdmin

    model = get_user_model()
    root = model.objects.create_superuser(
        username="root-admin-test", email="root-admin@example.invalid",
        password="test-only-root-password",
    )
    request = RequestFactory().get("/admin/auth/user/")
    request.user = model.objects.get(username="audit-test-admin")
    model_admin = ProtectedRootAdmin(model, admin.site)
    assert model_admin.has_delete_permission(request, root) is False
    assert "is_superuser" in model_admin.get_readonly_fields(request, root)
    assert "username" in model_admin.get_readonly_fields(request, root)


def test_root_admin_command_keeps_one_root_and_preserves_other_accounts(client):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.hashers import check_password
    from django.contrib.auth.models import Group
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from pricing.permissions import ADMIN_GROUP, VIEWER_GROUP

    User = get_user_model()
    root = User.objects.create_superuser(
        username="chosen-root", email="chosen-root@example.invalid", password="root-password-test"
    )
    old_admin = User.objects.create_superuser(
        username="old-admin", email="old-admin@example.invalid", password="old-admin-password-test"
    )
    old_admin.groups.add(Group.objects.get_or_create(name=VIEWER_GROUP)[0])
    call_command("configure_root_admin", username="chosen-root", verbosity=0)

    root.refresh_from_db()
    old_admin.refresh_from_db()
    assert User.objects.filter(is_superuser=True).count() == 1
    assert root.is_superuser and root.is_active and root.is_staff
    assert not old_admin.is_superuser and old_admin.is_active and old_admin.is_staff
    assert old_admin.groups.filter(name=ADMIN_GROUP).exists()
    assert not old_admin.groups.filter(name=VIEWER_GROUP).exists()
    assert check_password("old-admin-password-test", old_admin.password)

    with pytest.raises(CommandError):
        call_command("configure_root_admin", username="missing-account", verbosity=0)


def test_initialize_roles_creates_only_admin_and_viewer_and_user_management_permissions(client):
    from django.contrib.auth.models import Group
    from django.core.management import call_command
    from pricing.permissions import ADMIN_GROUP, VIEWER_GROUP

    call_command("initialize_roles", verbosity=0)
    admin_group = Group.objects.get(name=ADMIN_GROUP)
    assert Group.objects.filter(name__in=(ADMIN_GROUP, VIEWER_GROUP)).count() == 2
    assert set(admin_group.permissions.values_list("codename", flat=True)) >= {
        "add_user", "change_user", "view_user", "add_group", "change_group", "view_group",
    }


@pytest.mark.django_db
def test_four_role_backend_permission_matrix():
    from types import SimpleNamespace
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from pricing.permissions import (
        APPLICATION_ROLES, CanAdminister, CanAnalyze, CanReview, CanView,
        ADMIN_GROUP, VIEWER_GROUP,
    )

    User = get_user_model()
    for role in APPLICATION_ROLES:
        Group.objects.get_or_create(name=role)
    expected = {
        "SUPER_ADMIN": (True, True, True, True),
        "ANALYST": (True, True, False, False),
        "REVIEWER": (True, False, False, True),
        "CLIENT": (False, False, False, False),
    }
    for index, (role, decisions) in enumerate(expected.items()):
        user = User.objects.create_user(username=f"role-{index}", password="role-test-password")
        user.groups.add(Group.objects.get(name=role))
        request = SimpleNamespace(user=user, method="POST", path="/api/role-check/")
        actual = tuple(permission().has_permission(request, None) for permission in
                       (CanView, CanAnalyze, CanAdminister, CanReview))
        assert actual == decisions

    legacy_admin = User.objects.create_user(username="legacy-admin", password="legacy-test-password")
    legacy_admin.groups.add(Group.objects.get_or_create(name=ADMIN_GROUP)[0])
    legacy_viewer = User.objects.create_user(username="legacy-viewer", password="legacy-test-password")
    legacy_viewer.groups.add(Group.objects.get_or_create(name=VIEWER_GROUP)[0])
    admin_request = SimpleNamespace(user=legacy_admin, method="POST", path="/api/role-check/")
    viewer_request = SimpleNamespace(user=legacy_viewer, method="GET", path="/api/v1/plan-audits/")
    assert CanAdminister().has_permission(admin_request, None)
    assert CanView().has_permission(viewer_request, None)
    assert not CanAnalyze().has_permission(viewer_request, None)

    client_user = User.objects.create_user(username="scoped-client", password="client-test-password")
    client_user.groups.add(Group.objects.get(name="CLIENT"))
    client_plan_request = SimpleNamespace(user=client_user, method="GET", path="/api/v1/plan-audits/")
    client_public_request = SimpleNamespace(user=client_user, method="GET", path="/api/v1/hospitals/")
    assert CanView().has_permission(client_plan_request, None)
    assert not CanView().has_permission(client_public_request, None)


@pytest.mark.django_db
def test_viewer_login_requires_totp_enrollment_and_second_factor(client):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.test import Client, override_settings
    import time
    from pricing.otp import code_at

    user = get_user_model().objects.create_user(
        username="jwt-viewer", password="long-test-password-123", is_active=True
    )
    from pricing.permissions import VIEWER_GROUP
    user.groups.add(Group.objects.get_or_create(name=VIEWER_GROUP)[0])
    fresh_client = Client()
    with override_settings(TOTP_ENCRYPTION_KEY="F5wrMMH6I5kTnhPaaWqFvxLQmNY4J5kmWh0-e4jxQYc="):
        response = fresh_client.post("/api/v1/authentication/sign-in/", {
            "username": "jwt-viewer", "password": "long-test-password-123",
        }, content_type="application/json")
        assert response.status_code == 200
        assert response.json()["otp_setup_required"] is True
        assert "access" not in response.json()
        challenge = response.json()["challenge"]
        setup = fresh_client.post("/api/v1/authentication/totp/setup/", {
            "challenge": challenge,
        }, content_type="application/json")
        assert setup.status_code == 200
        assert "no-store" in setup.headers.get("Cache-Control", "")
        code, _ = code_at(setup.json()["secret"])
        confirmation = fresh_client.post("/api/v1/authentication/totp/confirmation/", {
            "challenge": challenge, "code": code,
        }, content_type="application/json")
        assert confirmation.status_code == 200
        assert confirmation.json()["user"]["roles"] == [VIEWER_GROUP]
        assert len(confirmation.json()["recovery_codes"]) == 10

        next_client = Client()
        password_response = next_client.post("/api/v1/authentication/sign-in/", {
            "username": "jwt-viewer", "password": "long-test-password-123",
        }, content_type="application/json")
        assert password_response.json()["otp_required"] is True
        next_code, _ = code_at(setup.json()["secret"], time.time() + 30)
        verified = next_client.post("/api/v1/authentication/totp/sign-in-verification/", {
            "challenge": password_response.json()["challenge"], "code": next_code,
        }, content_type="application/json")
        assert verified.status_code == 200
        assert "no-store" in verified.headers.get("Cache-Control", "")
        current_user = next_client.get(
            "/api/v1/authentication/current-user/",
            HTTP_AUTHORIZATION=f"Bearer {verified.json()['access']}")
        assert current_user.json()["authenticated"]


@pytest.mark.django_db
def test_admin_can_reset_viewer_password_and_viewer_must_enroll_totp(client):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.hashers import check_password
    from django.contrib.auth.models import Group
    from django.test import Client
    from pricing.permissions import VIEWER_GROUP

    viewer = get_user_model().objects.create_user(
        username="resettable-viewer", password="old-viewer-password-123", is_active=True
    )
    viewer.groups.add(Group.objects.get_or_create(name=VIEWER_GROUP)[0])
    new_password = "new-viewer-password-456"
    response = client.post(
        f"/api/v1/user-accounts/{viewer.pk}/management/",
        {"password": new_password}, content_type="application/json",
    )
    assert response.status_code == 200
    viewer.refresh_from_db()
    assert check_password(new_password, viewer.password)

    signed_in = Client().post("/api/v1/authentication/sign-in/", {
        "username": viewer.username, "password": new_password,
    }, content_type="application/json")
    assert signed_in.status_code == 200
    assert signed_in.json()["otp_setup_required"] is True


@pytest.mark.django_db
def test_admin_login_requires_totp_enrollment_before_session_refresh(client):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.test import Client, override_settings
    from pricing.otp import code_at
    from pricing.permissions import ADMIN_GROUP

    user = get_user_model().objects.create_user(
        username="jwt-admin", password="long-test-password-123", is_active=True
    )
    user.groups.add(Group.objects.get_or_create(name=ADMIN_GROUP)[0])
    fresh_client = Client()
    with override_settings(TOTP_ENCRYPTION_KEY="F5wrMMH6I5kTnhPaaWqFvxLQmNY4J5kmWh0-e4jxQYc="):
        response = fresh_client.post("/api/v1/authentication/sign-in/", {
            "username": "jwt-admin", "password": "long-test-password-123",
        }, content_type="application/json")
        assert response.status_code == 200
        assert response.json()["otp_setup_required"] is True
        challenge = response.json()["challenge"]

        setup = fresh_client.post("/api/v1/authentication/totp/setup/", {
            "challenge": challenge,
        }, content_type="application/json")
        assert setup.status_code == 200
        assert "no-store" in setup.headers.get("Cache-Control", "")
        assert setup.json()["otpauth_url"].startswith("otpauth://totp/")
        code, _ = code_at(setup.json()["secret"])
        confirmation = fresh_client.post("/api/v1/authentication/totp/confirmation/", {
            "challenge": challenge, "code": code,
        }, content_type="application/json")
        assert confirmation.status_code == 200
        assert "no-store" in confirmation.headers.get("Cache-Control", "")
        token = confirmation.json()["access"]
        assert confirmation.json()["token_type"] == "Bearer"
        current_user = fresh_client.get(
            "/api/v1/authentication/current-user/", HTTP_AUTHORIZATION=f"Bearer {token}")
        assert current_user.json()["authenticated"]

        # The HttpOnly Django session is the refresh credential and survives a page reload.
        refresh = fresh_client.post("/api/v1/authentication/access-token/refresh/", "{}",
                                    content_type="application/json")
        assert refresh.status_code == 200
        assert refresh.json()["access"]

        token_only = Client()
        rejected = token_only.post("/api/v1/authentication/access-token/refresh/", "{}",
                                   content_type="application/json",
                                   HTTP_AUTHORIZATION=f"Bearer {token}")
        assert rejected.status_code in {401, 403}


def test_password_login_requires_csrf_for_browser_sessions(client):
    from django.test import Client

    protected = Client(enforce_csrf_checks=True)
    csrf_response = protected.get("/api/v1/auth/csrf/")
    token = csrf_response.cookies["csrftoken"].value
    credentials = {"username": "audit-test-admin", "password": "test-only-password"}
    assert protected.post("/api/v1/auth/login/", credentials).status_code == 403
    accepted = protected.post("/api/v1/auth/login/", credentials, HTTP_X_CSRFTOKEN=token)
    assert accepted.status_code == 200


def test_invalid_bearer_token_is_rejected(client):
    response = client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION="Bearer not-a-jwt")
    assert response.status_code == 401


def test_drugs_headline_uses_ok_rows_only(client):
    rows = {d["hcpcs"]: d for d in client.get("/api/drugs").json()}
    assert set(rows) == {"J9271", "J9299", "J2350", "J1745", "J0897"}
    k = rows["J9271"]
    assert k["asp_payment_limit"] == 60.0 and k["asp_quarter"] == "2026Q4"
    # negotiated_dollar ok rows for J9271: 3.0 (tall), 4.1667 (NDC), 3.0, 2.0 (wide), 2.0 (json)
    assert k["rates"] == 5
    assert k["median_markup"] == pytest.approx(3.0)
    assert rows["J2350"]["rates"] == 0 and rows["J2350"]["review_rows"] == 1
    assert rows["J9299"]["median_markup"] is None


def test_drug_detail(client):
    d = client.get("/api/drugs/J9271").json()
    assert [a["quarter"] for a in d["asp"]] == ["2026Q3", "2026Q4"]
    assert len(d["hospitals"]) == 3
    assert client.get("/api/drugs/J0000").status_code == 404


@pytest.mark.parametrize("legacy_schema", [False, True], ids=["canonical", "legacy"])
def test_medicare_part_b_drugs_support_both_asp_schemas(
    client, tmp_path, monkeypatch, legacy_schema
):
    import polars as pl
    from pricing import data

    if legacy_schema:
        paths = data.files()
        legacy_path = tmp_path / "legacy_asp.parquet"
        pl.read_parquet(paths["asp"]).rename({
            "quarter": "period",
            "dosage_text": "dosage",
            "short_description": "description",
        }).write_parquet(legacy_path)
        paths["asp"] = legacy_path
        monkeypatch.setattr(data, "files", lambda: paths)

    response = client.get("/api/v1/medicare-part-b-drugs/")
    assert response.status_code == 200, response.content

    drugs = {row["hcpcs"]: row for row in response.json()}
    assert set(drugs) == {"J9271", "J9299", "J2350", "J1745", "J0897"}
    expected_limits = {
        "J9271": 60.0, "J9299": 30.0, "J2350": 100.0,
        "J1745": 40.0, "J0897": 20.0,
    }
    for code, payment_limit in expected_limits.items():
        assert drugs[code]["asp_quarter"] == "2026Q4"
        assert drugs[code]["asp_payment_limit"] == payment_limit
        assert drugs[code]["asp_dosage"] == ("10 MG" if code == "J1745" else "1 MG")

    catalog = client.get("/api/v1/reference/").json()
    asp_catalog = next(item for item in catalog if item["key"] == "asp")
    assert asp_catalog["periods"] == ["2026Q3", "2026Q4"]
    asp_page = client.get("/api/v1/reference/asp/", {"quarter": "2026Q4", "limit": 20})
    assert asp_page.status_code == 200
    assert asp_page.json()["total"] == 6

    detail = client.get("/api/v1/medicare-part-b-drugs/J9271/")
    assert detail.status_code == 200, detail.content
    assert [row["quarter"] for row in detail.json()["asp"]] == ["2026Q3", "2026Q4"]
    assert detail.json()["asp"][-1]["dosage_text"] == "1 MG"
    assert detail.json()["asp"][-1]["payment_limit"] == 60.0
    assert detail.json()["asp"][-1]["short_description"] == "Inj pembrolizumab"


def test_medicare_part_b_drugs_reject_unauthorized_users():
    from django.test import Client as DjangoClient

    response = DjangoClient().get("/api/v1/medicare-part-b-drugs/")
    assert response.status_code in {401, 403}


def test_rates_filter_and_sort(client):
    r = client.get("/api/rates", {"hcpcs": "J9271", "price_type": "negotiated_dollar",
                                  "status": "ok", "sort": "markup_multiple"}).json()
    assert r["total"] == 5
    m = [x["markup_multiple"] for x in r["rows"]]
    assert m == sorted(m, reverse=True)
    # unsafe sort value falls back to markup_multiple
    assert client.get("/api/rates", {"sort": "1; DROP TABLE x"}).status_code == 200
    assert client.get("/api/rates", {"limit": "many"}).status_code == 400


def test_comparison_payer_filter_options_come_from_parquet(client):
    options = client.get("/api/v1/hospital-drug-price-comparisons/filter-options/")
    rows = client.get("/api/v1/hospital-drug-price-comparisons/", {"limit": 1000})
    assert options.status_code == 200, options.content
    assert rows.status_code == 200, rows.content
    actual = sorted({row["payer_group"] for row in rows.json()["rows"] if row.get("payer_group")})
    assert options.json()["payer_groups"] == actual


def test_invalid_pagination_is_a_client_error(client):
    assert client.get("/api/v1/reference/asp/", {"limit": "many"}).status_code == 400
    assert client.get("/api/v1/reference/asp/", {"offset": "many"}).status_code == 400
    assert client.get("/api/v1/sources/jobs/", {"limit": "many"}).status_code == 400
    assert client.get("/api/v1/reports/", {"limit": "many"}).status_code == 400
    assert client.get("/api/v1/audit-logs/", {"limit": "many"}).status_code == 400


def test_csv_export(client):
    r = client.get("/api/rates.csv", {"hcpcs": "J1745"})
    assert r.status_code == 200
    body = r.content.decode()
    assert body.startswith("# Infusion Drug Markup Audit export")
    assert "source_sha256" in body


def test_excel_comparison_export_is_a_real_workbook(client):
    response = client.get("/api/v1/comparisons.xlsx")
    assert response.status_code == 200
    content = b"".join(response.streaming_content)
    assert content.startswith(b"PK")
    assert response["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert int(response["X-Export-Records"]) > 0


def test_hospital_detail_and_quality(client):
    hs = client.get("/api/hospitals").json()
    key = hs[0]["hospital_key"]
    d = client.get(f"/api/hospitals/{key}").json()
    assert d["hospital_key"] == key and d["drugs"]
    assert len(client.get("/api/quality").json()) == 4


def test_hospital_inventory_includes_files_without_comparison_rows(client, built):
    import polars as pl

    path = built / "data/hpt/norm/hpt_file_quality.parquet"
    quality = pl.read_parquet(path)
    extra = {name: None for name in quality.columns}
    extra.update({"system": "test_system", "source_file": "data/raw/hospital_mrf/test_system/empty/empty.csv",
                  "source_member": "empty.csv", "source_sha256": "f" * 64,
                  "hospital_name": "Zero Row Test Hospital", "locations": "test location",
                  "items_scanned": 10, "format": "csv_tall", "error": None,
                  "file_last_updated": "2026-10-21", "cms_version": "2.2.0",
                  "drug_rows": 0, "hcpcs_found": ""})
    pl.concat([quality, pl.DataFrame([extra], schema=quality.schema)], how="vertical").write_parquet(path)

    rows = client.get("/api/hospitals").json()
    unmatched = next(row for row in rows if row["hospital_label"] == "Zero Row Test Hospital")
    assert unmatched["file_status"] == "no_selected_drug_rows"
    assert unmatched["rates"] == unmatched["drugs_priced"] == 0
    detail = client.get(f"/api/hospitals/{unmatched['hospital_key']}").json()
    assert detail["n_rows"] == 0 and detail["drugs"] == []


def test_hospital_upload_preserves_parser_metadata_path(client, built):
    from django.core.files.uploadedfile import SimpleUploadedFile

    response = client.post("/api/v1/sources/upload/", {
        "source_key": "hospital_mrf", "system": "Test System", "snapshot_date": "2026-10-21",
        "file": SimpleUploadedFile("test_hospital_prices.json", b"{}", content_type="application/json"),
    })
    assert response.status_code == 201, response.json()
    from pathlib import Path
    path = Path(response.json()["relative_path"])
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    assert path.is_file()
    assert path.parent == built / "data/raw_essential_inputs/hospital_mrf/test_system/2026-10-21"
    assert "test_hospital_prices" in path.name


def test_zip_upload_rejects_member_path_traversal(client):
    from io import BytesIO
    import zipfile
    from django.core.files.uploadedfile import SimpleUploadedFile

    content = BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("../outside.csv", "not safe")
    response = client.post("/api/v1/sources/upload/", {
        "source_key": "cms_asp",
        "file": SimpleUploadedFile("asp_prices.zip", content.getvalue(), content_type="application/zip"),
    })
    assert response.status_code == 400
    assert "unsafe member path" in response.json()["error"]


def test_viewer_cannot_read_source_inventory_or_job_logs(built, db):
    import os
    import sys
    from pathlib import Path
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.test import Client

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "auditapi.settings")
    import django
    django.setup()
    user = get_user_model().objects.create_user(username="audit-readonly", password="readonly-pass")
    from pricing.permissions import VIEWER_GROUP
    user.groups.add(Group.objects.get_or_create(name=VIEWER_GROUP)[0])
    viewer = Client()
    viewer.force_login(user)
    assert viewer.get("/api/v1/sources/").status_code == 403
    assert viewer.get("/api/v1/sources/jobs/").status_code == 403


def test_refresh_job_starts_worker_automatically(client, monkeypatch):
    import pricing.views

    calls = []
    monkeypatch.setattr(pricing.views.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    response = client.post("/api/v1/sources/jobs/", {"job_type": "refresh_data"},
                           content_type="application/json")
    assert response.status_code == 202, response.json()
    assert response.json()["status"] == "queued"
    assert calls and calls[0][0][0][2:] == ["process_jobs", "--once"]


def test_saved_comparison_is_available_as_archived_snapshot_when_source_is_missing(built):
    import json
    from pricing.data import require_current_scope

    info = json.loads((built / "data/mart/run_info.json").read_text(encoding="utf-8"))
    source = Path(info["drug_selection_source_file"])
    if not source.is_absolute():
        source = Path(__file__).resolve().parents[1] / source
    source.unlink()
    assert require_current_scope() == "archived_snapshot"


def test_reference_catalog_and_paged_table(client):
    response = client.get("/api/v1/reference/")
    assert response.status_code == 200
    catalog = {item["key"]: item for item in response.json()}
    assert catalog["asp"]["status"] == "loaded"
    assert catalog["asp"]["rows"] == 11
    assert catalog["crosswalk"]["status"] == "loaded"
    assert catalog["ndc-products"]["status"] == "missing"
    page = client.get("/api/v1/reference/asp/", {"quarter": "2026Q4", "limit": 2}).json()
    assert page["status"] == "loaded" and page["total"] == 6
    assert len(page["rows"]) == 2


def test_drug_scope_and_settings_are_data_backed(client):
    scope = client.get("/api/v1/drug-scope/").json()
    assert scope["status"] == "selected"
    assert scope["selected_count"] == 5
    assert len(scope["source_sha256"]) == 64
    settings = client.get("/api/v1/settings/").json()
    assert settings["report"]["target_drug_count"] == 20
    assert settings["hospital_taxonomy"]["state_code"] == "OH"


def test_analyst_unit_review_is_bound_to_current_scope(client, tmp_path):
    ok = client.post("/api/v1/drug-scope/J9271/review/", {
        "status": "validated", "billing_qty": "1", "billing_unit": "MG",
        "clinical_reviewer_name": "Test External Pharmacist",
        "notes": "Checked against the CMS dosage field.",
    }, content_type="application/json")
    assert ok.status_code == 200
    assert ok.json()["status"] == "validated"
    bad = client.post("/api/v1/drug-scope/J9271/review/", {
        "status": "validated", "billing_qty": "10", "billing_unit": "MG",
        "clinical_reviewer_name": "Test External Pharmacist", "notes": "Wrong unit.",
    }, content_type="application/json")
    assert bad.status_code == 400
    assert "CMS ASP dosage" in bad.json()["error"]


def test_claims_explorer_rejects_unauthenticated():
    from django.test import Client as DjangoClient

    response = DjangoClient().get("/api/v1/claims/")
    assert response.status_code in {401, 403}

    detail = DjangoClient().get("/api/v1/claims/CLM12345/")
    assert detail.status_code in {401, 403}


def test_claims_explorer_list_and_filtering(client):
    from pricing.models import ClaimsExplorerDataset, PlanOrganization

    organization = PlanOrganization.objects.create(
        organization_name="Claims Explorer Test Org", organization_type="employer",
    )
    dataset_id = "desynpuf_outpatient_claims_sample_1"
    inpatient_id = "desynpuf_inpatient_claims_sample_1"
    registration = client.post("/api/v1/claims/datasets/", {
        "organization_id": organization.pk, "dataset_identifier": dataset_id,
    }, content_type="application/json")
    assert registration.status_code == 201, registration.content
    assert registration.json()["created"] is True
    ClaimsExplorerDataset.objects.create(
        organization=organization, dataset_identifier=inpatient_id,
    )
    catalog = client.get("/api/v1/claims/datasets/", {"organization_id": organization.pk})
    assert catalog.status_code == 200
    assert {item["dataset_identifier"] for item in catalog.json()["datasets"]} == {
        dataset_id, inpatient_id,
    }
    assert len(catalog.json()["available_datasets"]) >= 2
    assert "normalized" not in str(catalog.json())

    missing_scope = client.get("/api/v1/claims/")
    assert missing_scope.status_code == 400
    response = client.get("/api/v1/claims/", {
        "organization_id": organization.pk, "dataset": dataset_id, "page_size": 10,
    })
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["dataset"] == dataset_id
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert "data_disclaimer" in data
    assert "total" in data
    assert "columns" in data
    assert "rows" in data
    assert len(data["rows"]) <= 10
    assert data["rows"]
    assert "source_lineage" not in data["rows"][0]
    assert "beneficiary_id" not in data["rows"][0]
    assert "normalized" not in str(data)

    claim_id = data["rows"][0]["claim_id"]
    detail_res = client.get(f"/api/v1/claims/{claim_id}/", {
        "organization_id": organization.pk, "dataset": dataset_id,
    })
    assert detail_res.status_code == 200, detail_res.content
    assert detail_res.json()["dataset"] == dataset_id
    assert detail_res.json()["claim"]["claim_id"] == claim_id
    assert "source_lineage" not in detail_res.json()["claim"]
    assert "beneficiary_id" not in detail_res.json()["claim"]
    assert all("source_lineage" not in line for line in detail_res.json()["lines"])

    inpatient_res = client.get("/api/v1/claims/", {
        "organization_id": organization.pk, "dataset": inpatient_id, "page_size": 5,
    })
    assert inpatient_res.status_code == 200
    assert inpatient_res.json()["dataset"] == inpatient_id

    bad_dataset = client.get("/api/v1/claims/", {
        "organization_id": organization.pk, "dataset": "invalid_dataset",
    })
    assert bad_dataset.status_code == 400

    unregistered = client.get("/api/v1/claims/", {
        "organization_id": organization.pk, "dataset": "unknown_dataset",
    })
    assert unregistered.status_code == 400

    hcpcs_res = client.get("/api/v1/claims/", {
        "organization_id": organization.pk, "dataset": dataset_id, "hcpcs": "J9271", "page_size": 5,
    })
    assert hcpcs_res.status_code == 200

    provider_res = client.get("/api/v1/claims/", {
        "organization_id": organization.pk, "dataset": dataset_id, "provider": "PRV", "page_size": 5,
    })
    assert provider_res.status_code == 200

    date_res = client.get("/api/v1/claims/", {
        "organization_id": organization.pk,
        "dataset": dataset_id,
        "service_date_from": "2008-01-01",
        "service_date_to": "2010-12-31",
        "page_size": 5,
    })
    assert date_res.status_code == 200
    sql_injection = client.get("/api/v1/claims/", {
        "organization_id": organization.pk, "dataset": dataset_id, "hcpcs": "J0000' OR 1=1 --",
    })
    assert sql_injection.status_code == 400


def test_organization_and_plan_lifecycle(client):
    from django.test import Client as DjangoClient

    # Unauthenticated rejected
    unauth = DjangoClient().get("/api/v1/organizations/")
    assert unauth.status_code in {401, 403}

    # Admin creates organization
    org_res = client.post("/api/v1/organizations/", {
        "organization_name": "Test Employer Health Plan",
        "organization_type": "employer",
    }, content_type="application/json")
    assert org_res.status_code == 201, org_res.content
    org_id = org_res.json()["id"]

    # Organization listed
    list_res = client.get("/api/v1/organizations/")
    assert list_res.status_code == 200
    assert any(o["id"] == org_id for o in list_res.json())

    # Admin creates plan within organization
    plan_res = client.post("/api/v1/plans/", {
        "organization_id": org_id,
        "plan_name": "Standard PPO",
        "plan_reference": "PLAN-PPO-001",
        "effective_start": "2024-01-01",
    }, content_type="application/json")
    assert plan_res.status_code == 201, plan_res.content
    plan_id = plan_res.json()["id"]

    # Plan listed for organization
    plans_list = client.get("/api/v1/plans/", {"organization_id": org_id})
    assert plans_list.status_code == 200
    assert any(p["id"] == plan_id for p in plans_list.json())


def test_claims_explorer_organization_scoping_and_privacy(client, db):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.test import Client as DjangoClient
    from pricing.models import ClaimsExplorerDataset, PlanOrganization, PlanOrganizationAccess

    # Create two organizations
    org1 = PlanOrganization.objects.create(organization_name="Scoped Org Alpha", organization_type="employer")
    org2 = PlanOrganization.objects.create(organization_name="Scoped Org Beta", organization_type="employer")

    # Analyst role group
    analyst_group, _ = Group.objects.get_or_create(name="ANALYST")
    user = get_user_model().objects.create_user(username="scoped-analyst", password="test-password")
    user.groups.add(analyst_group)

    analyst_client = DjangoClient()
    analyst_client.force_login(user)

    # User with no organization access assigned gets 403
    res_no_org = analyst_client.get("/api/v1/claims/")
    assert res_no_org.status_code == 400

    # Grant user access to Org 1 only
    PlanOrganizationAccess.objects.create(organization=org1, user=user)

    unauthorized_registration = analyst_client.post("/api/v1/claims/datasets/", {
        "organization_id": org1.pk,
        "dataset_identifier": "desynpuf_inpatient_claims_sample_1",
    }, content_type="application/json")
    assert unauthorized_registration.status_code == 403

    dataset_id = "desynpuf_outpatient_claims_sample_1"
    ClaimsExplorerDataset.objects.create(
        organization=org1, dataset_identifier=dataset_id,
    )
    ClaimsExplorerDataset.objects.create(
        organization=org2, dataset_identifier=dataset_id,
    )

    # User can query only the registered dataset under Org 1.
    res_org1 = analyst_client.get("/api/v1/claims/", {
        "organization_id": org1.pk, "dataset": dataset_id, "page_size": 5,
    })
    assert res_org1.status_code == 200
    assert res_org1.json()["organization_id"] == org1.pk
    assert "normalized" not in str(res_org1.json())

    # User CANNOT query Org 2 (cross-organization access denied)
    res_cross_org = analyst_client.get("/api/v1/claims/", {
        "organization_id": org2.pk, "dataset": dataset_id, "page_size": 5,
    })
    assert res_cross_org.status_code == 403

    # A guessed claim ID cannot bypass the organization or dataset boundary.
    detail_cross = analyst_client.get("/api/v1/claims/CLM12345/", {
        "organization_id": org2.pk, "dataset": dataset_id,
    })
    assert detail_cross.status_code == 403
    unregistered_org1 = analyst_client.get("/api/v1/claims/", {
        "organization_id": org1.pk,
        "dataset": "desynpuf_inpatient_claims_sample_1",
    })
    assert unregistered_org1.status_code == 403


def test_plan_audit_excludes_review_range_hospital_rates(client, monkeypatch):
    from decimal import Decimal
    from pricing import plan_audit_engine

    queries = []
    rows = [
        {
            "hcpcs": "J9271", "hospital_label": "Verified Hospital",
            "setting": "outpatient_hospital", "benchmark_quarter": "2026Q4",
            "price_per_billing_unit": "100.00", "billing_units": 1,
            "status": "review_range", "price_type": "negotiated_dollar",
            "source_file": "outlier.csv", "source_sha256": "a" * 64,
            "effective_start": "2026-01-01", "effective_end": "2026-12-31",
        },
        {
            "hcpcs": "J9271", "hospital_label": "Verified Hospital",
            "setting": "outpatient_hospital", "benchmark_quarter": "2026Q4",
            "price_per_billing_unit": "90.00", "billing_units": 1,
            "status": "ok", "price_type": "negotiated_dollar",
            "source_file": "validated.csv", "source_sha256": "b" * 64,
            "effective_start": "2026-01-01", "effective_end": "2026-12-31",
        },
    ]

    def query(sql, params, **kwargs):
        queries.append(sql)
        return [row for row in rows if row["status"] == "ok"] if "status = 'ok'" in sql else rows

    monkeypatch.setattr(plan_audit_engine, "_verified_site_names",
                        lambda: {"verified hospital": {"1000000004"}})
    monkeypatch.setattr(plan_audit_engine, "_rate_covers_quarter", lambda row, quarter: True)
    monkeypatch.setattr(plan_audit_engine.data, "query", query)

    result, errors = plan_audit_engine._hospital_rows(["J9271"], ["2026Q4"])
    assert errors == 0
    assert result[("J9271", "1000000004", "2026Q4", "outpatient_hospital")]["price"] == Decimal("90.00")
    assert len(queries) == 1
    assert "status = 'ok'" in queries[0]
    assert "review_range" not in queries[0]


def test_plan_audit_asp_lookup_never_falls_back_to_latest_quarter(client):
    from pricing.plan_audit_engine import _asp_rows

    historical, errors = _asp_rows(["J9271"], ["2008Q1"])
    assert historical == {}
    assert errors == 0


def test_end_to_end_plan_audit_workflow(client, db, tmp_path):
    import csv
    import io
    import json
    from decimal import Decimal
    import yaml
    from argparse import Namespace
    from client_extractor.src.cli import extract_claims
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client as DjangoClient

    # 1. Admin creates Organization and Plan
    org_res = client.post("/api/v1/organizations/", {
        "organization_name": "E2E Audit Health Plan",
        "organization_type": "employer",
    }, content_type="application/json")
    assert org_res.status_code == 201
    org_id = org_res.json()["id"]

    plan_ref = "PLAN-E2E-2026"
    plan_res = client.post("/api/v1/plans/", {
        "organization_id": org_id,
        "plan_name": "E2E Comprehensive PPO",
        "plan_reference": plan_ref,
        "effective_start": "2026-01-01",
        "effective_end": "2026-12-31",
    }, content_type="application/json")
    assert plan_res.status_code == 201
    plan_id = plan_res.json()["id"]

    # 2. Setup Reviewer user with organization access
    rev_group, _ = Group.objects.get_or_create(name="REVIEWER")
    rev_user = get_user_model().objects.create_user(username="e2e-reviewer", password="test-password")
    rev_user.groups.add(rev_group)
    grant_res = client.post(f"/api/v1/organizations/{org_id}/users/", {"user_id": rev_user.pk}, content_type="application/json")
    assert grant_res.status_code in {200, 201}

    rev_client = DjangoClient()
    rev_client.force_login(rev_user)

    # 3. Run the actual extractor on isolated fictional TEST claims.
    source_path = tmp_path / "claims_TEST.csv"
    claim_fields = [
        "claim_id", "service_date", "hcpcs_code", "site_npi", "units", "allowed_amount",
        "paid_amount", "member_id", "claim_status", "plan_id", "service_setting",
    ]
    with source_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=claim_fields)
        writer.writeheader()
        for index in range(11):
            writer.writerow({
                "claim_id": f"TEST-CLAIM-A-{index}", "service_date": "2026-10-15",
                "hcpcs_code": "J9271", "site_npi": "1000000004",
                "units": "5" if index == 10 else "0.5",
                "allowed_amount": "1400" if index == 0 else "10",
                "paid_amount": "1100" if index == 0 else "10",
                "member_id": f"TEST-MEMBER-A-{index}", "claim_status": "paid",
                "plan_id": "PLAN-TEST-ID", "service_setting": "outpatient_hospital",
            })
        for index in range(14):
            writer.writerow({
                "claim_id": f"TEST-CLAIM-B-{index}", "service_date": "2026-10-15",
                "hcpcs_code": "J9271", "site_npi": "1000000012",
                "units": "1.75" if index == 13 else "0.25",
                "allowed_amount": "470" if index == 0 else "10",
                "paid_amount": "370" if index == 0 else "10",
                "member_id": f"TEST-MEMBER-B-{index}", "claim_status": "paid",
                "plan_id": "PLAN-TEST-ID", "service_setting": "outpatient_hospital",
            })
    config_path = tmp_path / "client_config_TEST.yaml"
    config_path.write_text(yaml.safe_dump({
        "plan_id": "PLAN-TEST-ID", "plan_reference": plan_ref,
        "hcpcs_billing_units": {
            "J9271": {"qty": 1, "unit": "UNIT", "verification_status": "validated",
                      "source_reference": "TEST-only unit evidence", "source_sha256": "a" * 64},
        },
    }), encoding="utf-8")
    output_path = tmp_path / "extractor_output"
    assert extract_claims(Namespace(
        claims=source_path, config=config_path, output=output_path,
    )) == 0
    summary_path = output_path / "claims_summary.csv"
    manifest_path = output_path / "manifest.json"
    csv_bytes = summary_path.read_bytes()
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_bytes = manifest_path.read_bytes()

    legacy_manifest = dict(manifest_data)
    legacy_manifest.pop("complementary_suppression_applied")
    legacy_manifest_bytes = json.dumps(legacy_manifest).encode("utf-8")
    rejected_import = client.post("/api/v1/plan-audits/import-summary/", {
        "organization_id": org_id,
        "plan_id": plan_id,
        "file": SimpleUploadedFile("claims_summary.csv", csv_bytes, content_type="text/csv"),
        "manifest": SimpleUploadedFile("manifest.json", legacy_manifest_bytes, content_type="application/json"),
    })
    assert rejected_import.status_code == 400

    import_res = client.post("/api/v1/plan-audits/import-summary/", {
        "organization_id": org_id,
        "plan_id": plan_id,
        "file": SimpleUploadedFile("claims_summary.csv", csv_bytes, content_type="text/csv"),
        "manifest": SimpleUploadedFile("manifest.json", manifest_bytes, content_type="application/json"),
    })
    assert import_res.status_code == 201, import_res.content
    import_id = import_res.json()["id"]
    duplicate_import = client.post("/api/v1/plan-audits/import-summary/", {
        "organization_id": org_id, "plan_id": plan_id,
        "file": SimpleUploadedFile("claims_summary.csv", csv_bytes, content_type="text/csv"),
        "manifest": SimpleUploadedFile("manifest.json", manifest_bytes, content_type="application/json"),
    })
    assert duplicate_import.status_code == 409

    bad_rows = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8"))))
    bad_rows[1][0] = "OTHER-PLAN"
    bad_csv = io.StringIO()
    csv.writer(bad_csv, lineterminator="\n").writerows(bad_rows)
    bad_bytes = bad_csv.getvalue().encode("utf-8")
    import hashlib
    bad_manifest = dict(manifest_data, sha256=hashlib.sha256(bad_bytes).hexdigest())
    invalid_plan_import = client.post("/api/v1/plan-audits/import-summary/", {
        "organization_id": org_id, "plan_id": plan_id,
        "file": SimpleUploadedFile("claims_summary.csv", bad_bytes, content_type="text/csv"),
        "manifest": SimpleUploadedFile("manifest.json", json.dumps(bad_manifest).encode(), content_type="application/json"),
    })
    assert invalid_plan_import.status_code == 400
    assert any("plan_reference" in row["reason"]
               for row in invalid_plan_import.json()["validation_summary"]["errors"])

    # 4. Upload network roster
    roster_rows = [
        ["provider_npi", "provider_name", "network_name", "network_status", "effective_start", "effective_end", "service_location", "applicable_plan_reference"],
        ["1000000004", "Primary Cancer Center", "Choice Network", "in_network", "2026-01-01", "2026-12-31", "Columbus OH", plan_ref],
        ["1000000012", "Alternative Infusion Clinic", "Choice Network", "in_network", "2026-01-01", "2026-12-31", "Dublin OH", plan_ref],
    ]
    roster_out = io.StringIO()
    csv.writer(roster_out, lineterminator="\n").writerows(roster_rows)
    roster_bytes = roster_out.getvalue().encode("utf-8")

    roster_res = client.post(f"/api/v1/plans/{plan_id}/network-roster/", {
        "file": SimpleUploadedFile("network_roster.csv", roster_bytes, content_type="text/csv"),
    })
    assert roster_res.status_code == 201, roster_res.content
    coverage_res = client.get(
        f"/api/v1/plans/{plan_id}/network-roster/?summary_import_id={import_id}"
    )
    assert coverage_res.status_code == 200
    assert coverage_res.json()["quarter_coverage"]["status_counts"] == {"IN_NETWORK": 2}

    conflict_csv = io.StringIO()
    csv.writer(conflict_csv, lineterminator="\n").writerows([
        ["provider_npi", "provider_name", "network_name", "network_status",
         "effective_start", "effective_end", "service_location", "applicable_plan_reference"],
        ["1000000004", "Primary Cancer Center", "Choice Network", "out_of_network",
         "2026-01-01", "2026-12-31", "Columbus OH", plan_ref],
    ])
    conflicting_roster = conflict_csv.getvalue().encode("utf-8")
    conflicting_res = client.post(f"/api/v1/plans/{plan_id}/network-roster/", {
        "file": SimpleUploadedFile("conflicting_roster.csv", conflicting_roster, content_type="text/csv"),
    })
    assert conflicting_res.status_code == 400
    assert client.get(f"/api/v1/plans/{plan_id}/network-roster/").json()["entry_count"] == 2

    # 5. Create Plan Audit
    create_audit_res = client.post("/api/v1/plan-audits/create/", {
        "organization_id": org_id,
        "plan_id": plan_id,
        "summary_import_id": import_id,
        "audit_name": "2026 Q4 Oncology Infusion Audit",
        "reporting_period": "2026-01-01/2026-12-31",
    }, content_type="application/json")
    assert create_audit_res.status_code == 201, create_audit_res.content
    audit_id = create_audit_res.json()["id"]

    # 6. Run Plan Audit
    run_res = client.post(f"/api/v1/plan-audits/{audit_id}/run/")
    assert run_res.status_code == 200, run_res.content
    audit_data = run_res.json()
    assert audit_data["status"] == "needs_review"

    # 7. Check Audit Overview, Results, and Quality
    overview_res = client.get(f"/api/v1/plan-audits/{audit_id}/overview/")
    assert overview_res.status_code == 200
    metrics = overview_res.json()["metrics"]
    assert metrics["totals_scope"] == "visible_approved_aggregate_rows_only"
    assert metrics["potential_allowed_savings"] is None

    results_res = client.get(f"/api/v1/plan-audits/{audit_id}/results/")
    assert results_res.status_code == 200
    assert results_res.json()["count"] == 2
    result = results_res.json()["results"][0]
    assert result["total_units"] is not None
    assert result["total_allowed_amount"] is not None
    assert result["total_paid_amount"] is not None
    assert result["source_references"]["claims_summary_import_id"] == import_id
    assert "asp_source_file" in result["source_references"]
    assert result["source_references"]["unit_review_status"] == "unresolved"
    assert Decimal(result["plan_allowed_price_per_unit"]) == Decimal("150")
    assert Decimal(result["plan_paid_price_per_unit"]) == Decimal("120")
    assert client.get(f"/api/v1/plan-audits/{audit_id}/savings/").json() == []

    # 8. Review workflow: Reviewer submits comment and approves
    # Non-reviewer gets 403
    viewer_group, _ = Group.objects.get_or_create(name="Viewer")
    unauth_user = get_user_model().objects.create_user(username="unauth-viewer", password="test-password")
    unauth_user.groups.add(viewer_group)
    unauth_client = DjangoClient()
    unauth_client.force_login(unauth_user)

    unauth_approve = unauth_client.post(f"/api/v1/plan-audits/{audit_id}/approve/")
    assert unauth_approve.status_code == 403

    # Reviewer submits comment
    comment_res = rev_client.post(f"/api/v1/plan-audits/{audit_id}/review/", {
        "comments": "Reviewed aggregate rows and ASP benchmarks; verified in-network clinical status.",
    }, content_type="application/json")
    assert comment_res.status_code == 201
    second_comment = rev_client.post(f"/api/v1/plan-audits/{audit_id}/review/", {
        "comments": "Confirmed that source aggregates remain distinct by HCPCS, site, and quarter.",
    }, content_type="application/json")
    assert second_comment.status_code == 201
    assert second_comment.json()["id"] != comment_res.json()["id"]

    # Reviewer approves audit
    approve_res = rev_client.post(f"/api/v1/plan-audits/{audit_id}/approve/", {
        "comments": "Approved after clinical verification.",
    }, content_type="application/json")
    assert approve_res.status_code == 200
    assert approve_res.json()["review_status"] == "approved"
    review_history = rev_client.get(f"/api/v1/plan-audits/{audit_id}/review/")
    assert review_history.status_code == 200
    assert len(review_history.json()) == 3
    assert {item["review_status"] for item in review_history.json()} == {"submitted", "approved"}

    # 9. Generate Report and Download PDF & CSV
    gen_res = client.post(f"/api/v1/plan-audits/{audit_id}/generate-report/")
    assert gen_res.status_code == 200

    pdf_res = client.get(f"/api/v1/plan-audits/{audit_id}/report/")
    assert pdf_res.status_code == 200
    assert pdf_res["Content-Type"] == "application/pdf"
    assert len(pdf_res.content) > 100

    csv_res = client.get(f"/api/v1/plan-audits/{audit_id}/export-csv/")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res["Content-Type"]
    assert "plan_allowed_price_per_unit" in csv_res.content.decode("utf-8")
    assert "source_references" in csv_res.content.decode("utf-8")

    # 10. Historical Spending Filtering & Quality Breakdown
    hs_all = client.get(f"/api/v1/plan-audits/{audit_id}/historical-spending/")
    assert hs_all.status_code == 200
    assert hs_all.json()["available"] is True
    assert len(hs_all.json()["by_quarter"]) == 1
    assert len(hs_all.json()["by_site"]) == 2

    # Filter by specific site_npi
    hs_site = client.get(f"/api/v1/plan-audits/{audit_id}/historical-spending/?site_npi=1000000004")
    assert hs_site.status_code == 200
    assert len(hs_site.json()["by_site"]) == 1
    assert hs_site.json()["by_site"][0]["site_npi"] == "1000000004"

    # Filter by service_setting
    hs_setting = client.get(f"/api/v1/plan-audits/{audit_id}/historical-spending/?service_setting=outpatient_hospital")
    assert hs_setting.status_code == 200
    assert len(hs_setting.json()["by_setting"]) == 1

    # 11. Client/Viewer Access Control & Subsequent Rejection Gate
    client_group, _ = Group.objects.get_or_create(name="CLIENT")
    client_user = get_user_model().objects.create_user(username="e2e-client-user", password="test-password")
    client_user.groups.add(client_group)
    # Grant client user access to org_id
    client.post(f"/api/v1/organizations/{org_id}/users/", {"user_id": client_user.pk}, content_type="application/json")

    client_test_client = DjangoClient()
    client_test_client.force_login(client_user)

    # Currently approved -> Client sees audit
    client_audits_res = client_test_client.get("/api/v1/plan-audits/")
    assert client_audits_res.status_code == 200
    assert any(a["id"] == audit_id for a in client_audits_res.json())

    client_report_res = client_test_client.get(f"/api/v1/plan-audits/{audit_id}/report/")
    assert client_report_res.status_code == 200

    # Reviewer subsequent rejection -> gate closes for Client
    reject_res = rev_client.post(f"/api/v1/plan-audits/{audit_id}/reject/", {
        "comments": "Requires recalculation of clinical dose following physician inquiry.",
    }, content_type="application/json")
    assert reject_res.status_code == 200
    assert reject_res.json()["review_status"] == "rejected"

    # Client user can no longer view or download rejected audit
    client_audits_after = client_test_client.get("/api/v1/plan-audits/")
    assert not any(a["id"] == audit_id for a in client_audits_after.json())

    client_report_after = client_test_client.get(f"/api/v1/plan-audits/{audit_id}/report/")
    assert client_report_after.status_code == 404

    # 12. Cross-organization isolation
    org2_res = client.post("/api/v1/organizations/", {
        "organization_name": "Isolated Other Employer", "organization_type": "employer",
    }, content_type="application/json")
    org2_id = org2_res.json()["id"]

    other_user = get_user_model().objects.create_user(username="other-org-user", password="test-password")
    other_user.groups.add(client_group)
    client.post(f"/api/v1/organizations/{org2_id}/users/", {"user_id": other_user.pk}, content_type="application/json")

    other_client = DjangoClient()
    other_client.force_login(other_user)
    # Attempting to access Org 1's audit from Org 2 user returns 404
    assert other_client.get(f"/api/v1/plan-audits/{audit_id}/").status_code == 404


def test_evaluation_package_and_broker_evaluations_workflow(client):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.test import Client as DjangoClient

    # 1. Evaluation package endpoint
    pkg_res = client.get("/api/v1/reports/evaluation-package/")
    assert pkg_res.status_code == 200, pkg_res.json()
    pkg_data = pkg_res.json()
    assert "product_explanation" in pkg_data
    assert "what_meshkor_does" in pkg_data["product_explanation"]
    assert "privacy_and_security_boundary" in pkg_data["product_explanation"]
    assert len(pkg_data["evaluation_questions"]) == 7
    assert len(pkg_data["pilot_onboarding_checklist"]) == 15

    # Viewer role gets 403 on evaluation package
    viewer_group, _ = Group.objects.get_or_create(name="Viewer")
    viewer_user = get_user_model().objects.create_user(username="viewer-eval-test", password="test-password")
    viewer_user.groups.add(viewer_group)
    viewer_client = DjangoClient()
    viewer_client.force_login(viewer_user)

    viewer_pkg = viewer_client.get("/api/v1/reports/evaluation-package/")
    assert viewer_pkg.status_code == 403

    # 2. Broker evaluations initial milestone check
    init_res = client.get("/api/v1/reports/evaluations/")
    assert init_res.status_code == 200
    init_data = init_res.json()
    assert init_data["milestone"]["required_count"] == 3
    assert init_data["milestone"]["verified_count"] == 0
    assert init_data["milestone"]["remaining_count"] == 3
    assert init_data["milestone"]["prerequisite_met"] is False

    # 3. Viewer cannot record evaluations
    viewer_post = viewer_client.post("/api/v1/reports/evaluations/", {
        "organization_name": "Unauthorized Attempt",
    }, content_type="application/json")
    assert viewer_post.status_code == 403

    # 4. Record a contact-only interaction (does not qualify as verified evaluation)
    contact_res = client.post("/api/v1/reports/evaluations/", {
        "organization_name": "Apex Benefits",
        "contact_reference": "John Smith",
        "status": "contacted",
        "feedback": "",
    }, content_type="application/json")
    assert contact_res.status_code == 201
    assert contact_res.json()["is_verified"] is False

    # Milestone should still show verified_count == 0
    res_m1 = client.get("/api/v1/reports/evaluations/").json()
    assert res_m1["milestone"]["verified_count"] == 0

    # 5. Record verified evaluation for Org 1 (Apex Benefits)
    e1_res = client.post("/api/v1/reports/evaluations/", {
        "organization_name": "Apex Benefits",
        "contact_reference": "Jane Doe, VP Health",
        "report_version": "2024-Q3 v1.0",
        "evaluation_date": "2024-11-01",
        "status": "report_evaluated",
        "feedback": "Hospital markups exceeding 3x ASP limit are relevant for our self-funded employer clients.",
        "pilot_interest": True,
        "evidence_reference": "Meeting notes 2024-11-01",
    }, content_type="application/json")
    assert e1_res.status_code == 201
    assert e1_res.json()["is_verified"] is True
    e1_id = e1_res.json()["id"]

    res_m2 = client.get("/api/v1/reports/evaluations/").json()
    assert res_m2["milestone"]["verified_count"] == 1
    assert res_m2["milestone"]["remaining_count"] == 2
    assert res_m2["milestone"]["prerequisite_met"] is False

    # 6. Duplicate org does not increment verified milestone count
    e1_dup = client.post("/api/v1/reports/evaluations/", {
        "organization_name": "Apex Benefits ",  # Whitespace variant
        "status": "feedback_received",
        "evaluation_date": "2024-11-02",
        "feedback": "Second meeting feedback.",
    }, content_type="application/json")
    assert e1_dup.status_code == 201
    assert e1_dup.json()["is_verified"] is True

    res_m2_dup = client.get("/api/v1/reports/evaluations/").json()
    assert res_m2_dup["milestone"]["verified_count"] == 1  # Deduplicated

    # 7. Record Org 2
    e2_res = client.post("/api/v1/reports/evaluations/", {
        "organization_name": "Buckeye TPA Services",
        "status": "feedback_received",
        "evaluation_date": "2024-11-02",
        "feedback": "Confirmed that local claims extraction boundary resolves employer privacy concerns.",
    }, content_type="application/json")
    assert e2_res.status_code == 201
    res_m3 = client.get("/api/v1/reports/evaluations/").json()
    assert res_m3["milestone"]["verified_count"] == 2
    assert res_m3["milestone"]["remaining_count"] == 1
    assert res_m3["milestone"]["prerequisite_met"] is False

    # 8. Record Org 3 -> Satisfies prerequisite
    e3_res = client.post("/api/v1/reports/evaluations/", {
        "organization_name": "Great Lakes Benefits Trust",
        "status": "pilot_interested",
        "evaluation_date": "2024-11-03",
        "feedback": "Ready to evaluate one paid pilot audit with a 1,200-member self-funded plan.",
        "pilot_interest": True,
    }, content_type="application/json")
    assert e3_res.status_code == 201
    res_m4 = client.get("/api/v1/reports/evaluations/").json()
    assert res_m4["milestone"]["verified_count"] == 3
    assert res_m4["milestone"]["remaining_count"] == 0
    assert res_m4["milestone"]["prerequisite_met"] is True

    # 9. PATCH and DELETE
    patch_res = client.patch(f"/api/v1/reports/evaluations/{e1_id}/", {
        "next_action": "Follow up on pilot NDA agreement.",
    }, content_type="application/json")
    assert patch_res.status_code == 200
    assert patch_res.json()["next_action"] == "Follow up on pilot NDA agreement."

    del_res = client.delete(f"/api/v1/reports/evaluations/{e1_id}/")
    assert del_res.status_code == 204


def test_synthea_dataset_registration_and_category(client):
    from pricing.models import ClaimsExplorerDataset, PlanOrganization

    org = PlanOrganization.objects.create(
        organization_name="Synthea Demo Org", organization_type="tpa",
    )
    # Registration via POST
    reg_res = client.post("/api/v1/claims/datasets/", {
        "organization_id": org.pk,
        "dataset_identifier": "synthea_sample_data",
    }, content_type="application/json")
    assert reg_res.status_code == 201
    assert reg_res.json()["registered"] is True
    assert reg_res.json()["category"] == "synthetic_demo"

    # Verify category persisted in DB
    record = ClaimsExplorerDataset.objects.get(organization=org, dataset_identifier="synthea_sample_data")
    assert record.category == "synthetic_demo"

    # Verify catalog lists category
    catalog = client.get("/api/v1/claims/datasets/", {"organization_id": org.pk}).json()
    synthea_entry = next(d for d in catalog["datasets"] if d["dataset_identifier"] == "synthea_sample_data")
    assert synthea_entry["category"] == "synthetic_demo"
    assert synthea_entry["synthetic"] is True


def test_synthea_summary_and_quality_endpoint(client):
    from pricing.models import ClaimsExplorerDataset, PlanOrganization

    org = PlanOrganization.objects.create(
        organization_name="Synthea Analytics Org", organization_type="employer",
    )
    # 403 before registration
    unreg = client.get(f"/api/v1/claims/synthea/summary/?organization_id={org.pk}")
    assert unreg.status_code == 403

    # Register
    ClaimsExplorerDataset.objects.create(
        organization=org, dataset_identifier="synthea_sample_data", category="synthetic_demo",
    )

    # 200 after registration
    res = client.get(f"/api/v1/claims/synthea/summary/?organization_id={org.pk}")
    assert res.status_code == 200, res.content
    data = res.json()
    assert data["dataset_identifier"] == "synthea_sample_data"
    assert data["category"] == "synthetic_demo"
    assert data["total_tables"] == 18
    assert data["total_rows"] > 100000
    assert "summary_metrics" in data
    assert data["summary_metrics"]["total_encounters"] == 5059
    assert data["summary_metrics"]["unique_patients"] == 111
    assert "data_boundaries" in data
    assert data["data_boundaries"]["is_synthetic"] is True
    assert "hcpcs_jcodes" in data["data_boundaries"]
    assert "quality_report" in data
    assert data["quality_report"]["overall_status"] == "PASS"


def test_synthea_subtable_queries_and_privacy(client):
    from pricing.models import ClaimsExplorerDataset, PlanOrganization

    org = PlanOrganization.objects.create(
        organization_name="Synthea Subtables Org", organization_type="broker",
    )
    ClaimsExplorerDataset.objects.create(
        organization=org, dataset_identifier="synthea_sample_data", category="synthetic_demo",
    )

    # 1. Encounters query
    enc_res = client.get("/api/v1/claims/", {
        "organization_id": org.pk,
        "dataset": "synthea_sample_data",
        "subtable": "encounters",
        "page_size": 10,
    })
    assert enc_res.status_code == 200
    enc_data = enc_res.json()
    assert enc_data["dataset"] == "synthea_sample_data"
    assert enc_data["subtable"] == "encounters"
    assert enc_data["total"] == 5059
    assert len(enc_data["rows"]) == 10
    assert "TOTAL_CLAIM_COST" in enc_data["columns"]
    sample_enc_id = enc_data["rows"][0]["Id"]

    # 2. Patients privacy check: direct identifiers must not be exposed
    pat_res = client.get("/api/v1/claims/", {
        "organization_id": org.pk,
        "dataset": "synthea_sample_data",
        "subtable": "patients",
        "page_size": 10,
    })
    assert pat_res.status_code == 200
    pat_cols = pat_res.json()["columns"]
    for forbidden in ["SSN", "DRIVERS", "PASSPORT", "FIRST", "LAST", "MAIDEN", "ADDRESS"]:
        assert forbidden not in pat_cols

    # 3. Text search
    search_res = client.get("/api/v1/claims/", {
        "organization_id": org.pk,
        "dataset": "synthea_sample_data",
        "subtable": "encounters",
        "search": "examination",
        "page_size": 10,
    })
    assert search_res.status_code == 200
    assert search_res.json()["total"] > 0

    # 4. Detail lookup for encounter
    detail_res = client.get(f"/api/v1/claims/{sample_enc_id}/", {
        "organization_id": org.pk,
        "dataset": "synthea_sample_data",
    })
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["record_type"] == "encounter"
    assert detail["encounter"]["Id"] == sample_enc_id
    assert "procedures" in detail
    assert "medications" in detail

