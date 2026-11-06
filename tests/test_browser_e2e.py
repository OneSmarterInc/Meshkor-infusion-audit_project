"""Playwright browser checks against Django's disposable pytest database."""
import csv
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import os
import secrets
import shutil
import socket
import subprocess
import time
from argparse import Namespace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

import pytest
import yaml
from cryptography.fernet import Fernet

pytest.importorskip(
    "playwright.sync_api",
    reason="Install the optional Playwright package to run browser end-to-end checks.",
)

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


@pytest.fixture
def browser_app(live_server, settings, built, tmp_path):
    """Start Vite with an explicit proxy to Django's isolated live test server."""
    settings.DEBUG = False
    settings.SECURE_SSL_REDIRECT = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "127.0.0.1", "localhost"]
    settings.TOTP_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")

    backend = urlsplit(live_server.url)
    backend_url = urlunsplit((backend.scheme, f"127.0.0.1:{backend.port}", "", "", ""))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        vite_port = listener.getsockname()[1]
    vite_url = f"http://127.0.0.1:{vite_port}"
    settings.CSRF_TRUSTED_ORIGINS = [*settings.CSRF_TRUSTED_ORIGINS, vite_url]
    node = shutil.which("node")
    assert node, "Node.js is required to start the existing Vite development server."
    vite_script = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    assert vite_script.is_file(), "The repository's installed Vite executable is missing."

    vite_env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
                if key in os.environ}
    vite_env.update({"VITE_PROXY_API_TARGET": backend_url, "NO_COLOR": "1"})
    log_path = tmp_path / "vite.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [node, str(vite_script), "--host", "127.0.0.1", "--port", str(vite_port), "--strictPort"],
            cwd=FRONTEND, env=vite_env, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            for _ in range(80):
                if process.poll() is not None:
                    pytest.fail("Vite exited during startup; see isolated pytest temporary log.")
                try:
                    with urlopen(vite_url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.25)
            else:
                pytest.fail("Vite did not become ready; see isolated pytest temporary log.")
            yield vite_url, backend_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _new_enabled_user(username, password, *, superuser=False):
    from django.contrib.auth import get_user_model
    from pricing.models import AdminOTPDevice
    from pricing.otp import encrypt_secret, new_secret

    User = get_user_model()
    user = (User.objects.create_superuser(username=username, email=f"{username}@example.invalid",
                                          password=password) if superuser else
            User.objects.create_user(username=username, email=f"{username}@example.invalid",
                                     password=password))
    secret = new_secret()
    AdminOTPDevice.objects.create(user=user, secret=encrypt_secret(secret), enabled=True)
    return user, secret


def _enable_existing_user(username):
    from django.contrib.auth import get_user_model
    from pricing.models import AdminOTPDevice
    from pricing.otp import encrypt_secret, new_secret

    user = get_user_model().objects.get(username=username)
    secret = new_secret()
    AdminOTPDevice.objects.create(user=user, secret=encrypt_secret(secret), enabled=True)
    return user, secret


def _create_account(page, username, role, db_call):
    password = secrets.token_urlsafe(20) + "Q7!"
    page.get_by_label("Username").fill(username)
    page.get_by_label("First name").fill("Synthetic")
    page.get_by_label("Last name").fill("Browser")
    page.get_by_label("Email").fill(f"{username}@example.invalid")
    page.get_by_label("Role").select_option(role)
    page.get_by_label("Initial password").fill(password)
    page.get_by_role("button", name="Create account").click()
    page.get_by_role("status").filter(has_text=username).wait_for()
    user, secret = db_call(_enable_existing_user, username)
    return user, password, secret


def _sign_in(page, origin, username, password, secret, *, exercise_bad_otp=False):
    from pricing.otp import code_at

    page.goto(origin)
    page.locator('input[autocomplete="username"]').fill(username)
    page.locator('input[autocomplete="current-password"]').fill(password)
    page.get_by_role("button", name="Sign in").click()
    code_input = page.get_by_label("Authenticator code")
    code_input.wait_for()
    if exercise_bad_otp:
        correct, _ = code_at(secret)
        incorrect = f"{(int(correct) + 1) % 1_000_000:06d}"
        code_input.fill(incorrect)
        page.get_by_role("button", name="Verify code").click()
        page.get_by_role("alert").wait_for()
    code, _ = code_at(secret)
    code_input.fill(code)
    page.get_by_role("button", name="Verify code").click()
    page.get_by_role("heading", name="Overview").wait_for()


def _api(page, path, *, method="GET", body=None):
    return page.evaluate("""async ({path, method, body}) => {
      const csrf = document.cookie.split(';').map(x => x.trim())
        .find(x => x.startsWith('csrftoken='))?.slice(10) || '';
      const headers = {'Content-Type': 'application/json'};
      if (!['GET', 'HEAD'].includes(method)) headers['X-CSRFToken'] = csrf;
      const response = await fetch(path, {method, headers, credentials: 'same-origin',
        body: body == null ? undefined : JSON.stringify(body)});
      let payload = null;
      try { payload = await response.json(); } catch {}
      return {status: response.status, payload};
    }""", {"path": path, "method": method, "body": body})


def _write_extract(tmp_path, plan_reference, hcpcs, site_specs, file_stem):
    from client_extractor.src.cli import extract_claims

    source = tmp_path / f"{file_stem}-raw-synthetic.csv"
    with source.open("w", newline="", encoding="utf-8") as stream:
        fields = ["claim_id", "service_date", "hcpcs_code", "site_npi", "units",
                  "allowed_amount", "paid_amount", "member_id", "claim_status", "plan_id",
                  "service_setting"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        serial = 0
        for site_npi, allowed, paid in site_specs:
            for member_index in range(11):
                serial += 1
                writer.writerow({"claim_id": f"{file_stem}-{serial}", "service_date": "2026-10-15",
                    "hcpcs_code": hcpcs, "site_npi": site_npi, "units": "1",
                    "allowed_amount": allowed, "paid_amount": paid,
                    "member_id": f"{file_stem}-member-{serial}", "claim_status": "paid",
                    "plan_id": "RG01-SYNTHETIC-PLAN", "service_setting": "outpatient_hospital"})
    config = tmp_path / f"{file_stem}-synthetic-config.yaml"
    config.write_text(yaml.safe_dump({"plan_id": "RG01-SYNTHETIC-PLAN", "plan_reference": plan_reference,
        "hcpcs_billing_units": {hcpcs: {"qty": 1, "unit": "MG", "verification_status": "validated",
            "source_reference": "Synthetic browser fixture only", "source_sha256": "a" * 64}}}),
        encoding="utf-8")
    output = tmp_path / f"{file_stem}-extract-output"
    assert extract_claims(Namespace(claims=source, config=config, output=output)) == 0
    return output / "claims_summary.csv", output / "manifest.json"


def _upload_summary(page, summary, manifest):
    page.get_by_label("Claims summary CSV").set_input_files(str(summary))
    page.get_by_label("Matching manifest JSON").set_input_files(str(manifest))
    page.get_by_role("button", name="Preview validation").click()
    page.get_by_role("status").filter(has_text="Privacy declarations and aggregate CSV SHA-256 verified locally").wait_for()
    page.get_by_role("button", name="Import approved aggregates").click()
    page.get_by_text("Approved aggregate summary imported.").wait_for()


def test_rg01_browser_workflow_and_authorization(browser_app, built, tmp_path, settings, request, monkeypatch):
    """Exercise MFA, tenant scoping, audit lifecycle, downloads, revocation and responsive UI."""
    import hashlib
    from decimal import Decimal
    from django.contrib.auth import get_user_model
    from pricing.models import (ClaimsSummaryImport, Plan, PlanAudit, PlanOrganization,
                                PlanOrganizationAccess)
    from pipeline import build_compare, select_drugs
    from pricing import plan_audit_engine

    origin, backend_url = browser_app
    # The fixture must never point at the configured operational DB.
    operational_db = str(ROOT / "data" / "operational" / "meshkor.sqlite3")
    assert settings.DATABASES["default"]["NAME"] != operational_db
    assert settings.DATABASES["default"]["NAME"] != str(settings.OPERATIONAL_DB_PATH)
    print("Using isolated test database:", settings.DATABASES["default"]["NAME"])

    monkeypatch.setattr(
        plan_audit_engine,
        "_hospital_rows",
        lambda codes, quarters: (
            {
                ("J9271", "1000000004", "2026Q4", "outpatient_hospital"): {
                    "price": Decimal("180"),
                    "source_file": "synthetic-hospital.csv",
                    "source_sha256": "b" * 64,
                },
                ("J9271", "1000000012", "2026Q4", "outpatient_hospital"): {
                    "price": Decimal("120"),
                    "source_file": "synthetic-hospital.csv",
                    "source_sha256": "b" * 64,
                },
            } if "J9271" in codes else {},
            0,
        ),
    )

    run_tag = secrets.token_hex(4)
    admin_name = f"rg01-admin-{run_tag}"
    admin_password = secrets.token_urlsafe(24) + "F3!"
    from django.db import connections
    admin_user, admin_secret = _new_enabled_user(admin_name, admin_password, superuser=True)
    connections.close_all()
    db_executor = ThreadPoolExecutor(max_workers=1)

    def db_call(function, *args, **kwargs):
        def _invoke():
            try:
                return function(*args, **kwargs)
            finally:
                connections.close_all()

        for attempt in range(8):
            try:
                return db_executor.submit(_invoke).result()
            except Exception as exc:
                from django.db import OperationalError
                if not isinstance(exc, OperationalError) or "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(0.1 * (attempt + 1))

    sync_playwright = pytest.importorskip(
        "playwright.sync_api",
        reason="Install the optional Playwright package to run browser end-to-end checks.",
    ).sync_playwright
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=not request.config.getoption("--headed"),
        slow_mo=request.config.getoption("--slowmo"),
    )
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    context.set_default_timeout(12_000)
    context.set_default_navigation_timeout(15_000)
    pages = []
    api_responses = []
    console_errors = []
    page_errors = []

    def new_page(browser_context):
        page = browser_context.new_page()
        pages.append(page)
        page.on("response", lambda response: api_responses.append((
            response.request.method, urlsplit(response.url).path, response.status
        )) if "/api/" in response.url else None)
        page.on("console", lambda message: console_errors.append(message.text)
                if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        return page

    page = new_page(context)
    reviewer_context = analyst_context = reviewer_b_context = client_context = None
    try:
        page.goto(origin + "/user-management")
        assert _api(page, "/api/v1/user-accounts/")["status"] == 401
        page.locator('input[autocomplete="username"]').wait_for()
        _sign_in(page, origin, admin_name, admin_password, admin_secret, exercise_bad_otp=True)

        # MFA cookie session must also open the staff-only Django Admin.
        admin_response = page.goto(backend_url + "/admin/")
        assert admin_response.status == 200
        assert page.locator("#header").inner_text().startswith("Django administration")
        page.goto(origin + "/user-management")
        page.get_by_role("main").get_by_role("heading", name="User management").wait_for()

        analyst_name = f"rg01-analyst-{run_tag}"
        reviewer_a_name = f"rg01-reviewer-a-{run_tag}"
        reviewer_b_name = f"rg01-reviewer-b-{run_tag}"
        client_name = f"rg01-client-{run_tag}"
        analyst_user, analyst_password, analyst_secret = _create_account(page, analyst_name, "ANALYST", db_call)
        reviewer_a_user, reviewer_a_password, reviewer_a_secret = _create_account(
            page, reviewer_a_name, "REVIEWER", db_call)
        reviewer_b_user, reviewer_b_password, reviewer_b_secret = _create_account(
            page, reviewer_b_name, "REVIEWER", db_call)
        client_user, client_password, client_secret = _create_account(page, client_name, "CLIENT", db_call)
        assert "ANALYST" in db_call(lambda: list(analyst_user.groups.values_list("name", flat=True)))
        assert "REVIEWER" in db_call(lambda: list(reviewer_a_user.groups.values_list("name", flat=True)))
        assert "CLIENT" in db_call(lambda: list(client_user.groups.values_list("name", flat=True)))

        page.goto(origin + "/plan-specific-audits")
        page.get_by_role("tab", name="Plan Setup").click()
        page.get_by_label("Organization name").fill(f"RG01 Synthetic Employer {run_tag}")
        page.get_by_label("Organization type").select_option("employer")
        page.get_by_role("button", name="Create organization").click()
        page.get_by_text("Organization created.").wait_for()
        organization = db_call(PlanOrganization.objects.get,
                               organization_name=f"RG01 Synthetic Employer {run_tag}")
        plan_reference = f"RG01-{run_tag.upper()}"
        page.get_by_label("Plan name").fill(f"Synthetic browser plan {run_tag}")
        page.get_by_label("Unique plan reference").fill(plan_reference)
        page.get_by_label("Payer reference (optional)").fill("RG01 synthetic payer")
        page.get_by_role("button", name="Create plan").click()
        page.get_by_text("Plan created.").wait_for()
        plan = db_call(Plan.objects.get, organization_id=organization.pk, plan_reference=plan_reference)

        page.get_by_label("Account").wait_for()
        for username in (analyst_name, reviewer_a_name, reviewer_b_name, client_name):
            account_select = page.get_by_label("Account")
            option = account_select.locator("option").filter(has_text=username).first
            option_value = option.get_attribute("value")
            account_select.select_option(option_value)
            page.get_by_role("button", name="Grant access").click()
            page.get_by_text("Organization access granted.").wait_for()
        assert db_call(lambda: PlanOrganizationAccess.objects.filter(
            organization_id=organization.pk).count()) == 4

        # A second synthetic tenant provides a direct cross-organization audit target.
        other_org = db_call(PlanOrganization.objects.create, organization_name=f"RG01 Unassigned {run_tag}",
                            organization_type="employer")
        other_plan = db_call(Plan.objects.create, organization_id=other_org.pk,
                             plan_name="Unassigned synthetic plan", plan_reference=f"RG01-OTHER-{run_tag}")

        reviewer_context = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        reviewer_page = new_page(reviewer_context)
        _sign_in(reviewer_page, origin, reviewer_a_name, reviewer_a_password, reviewer_a_secret)
        reviewer_orgs = _api(reviewer_page, "/api/v1/organizations/")
        assert reviewer_orgs["status"] == 200
        assert [row["id"] for row in reviewer_orgs["payload"]] == [organization.pk]
        assert _api(reviewer_page, "/api/v1/application-settings/")["status"] == 403
        assert _api(reviewer_page, "/api/v1/organizations/", method="POST",
                    body={"organization_name": "forbidden", "organization_type": "employer"})["status"] == 403

        # Clinical reviewer records a CMS-bound synthetic decision through the UI.
        reviewer_page.goto(origin + "/drug-unit-validations")
        reviewer_page.get_by_role("heading", name="Unit validation", exact=True).wait_for()
        drug_card = reviewer_page.locator("article.review-card").filter(has_text="J9271")
        drug_card.wait_for()
        drug_card.get_by_label("Review status for J9271").select_option("validated")
        drug_card.get_by_label("Clinical reviewer for J9271").fill("Synthetic Clinical Reviewer")
        drug_card.get_by_label("Review note for J9271").fill(
            "Synthetic fixture evidence matches the selected CMS dosage and unit."
        )
        drug_card.get_by_role("button", name="Record clinical sign-off").click()
        reviewer_page.get_by_text("Review recorded.", exact=False).wait_for()
        assert _api(reviewer_page, "/api/v1/drug-selection-scope/")["status"] == 200

        # Apply the saved reviewer evidence using the local pipeline against only
        # the synthetic data_root produced by tests/conftest.py.
        assert select_drugs.main() == 0
        assert build_compare.main() == 0

        analyst_context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        analyst_page = new_page(analyst_context)
        _sign_in(analyst_page, origin, analyst_name, analyst_password, analyst_secret)
        analyst_page.goto(origin + "/plan-specific-audits")
        analyst_page.get_by_role("heading", name="Plan-specific infusion drug audits").wait_for()
        analyst_orgs = _api(analyst_page, "/api/v1/organizations/")
        assert analyst_orgs["status"] == 200
        assert [row["id"] for row in analyst_orgs["payload"]] == [organization.pk]

        main_summary, main_manifest = _write_extract(tmp_path, plan_reference, "J9271", [
            ("1000000004", "140", "100"), ("1000000012", "10", "8"),
        ], "main")
        analyst_page.get_by_role("tab", name="Plan Setup").click()
        _upload_summary(analyst_page, main_summary, main_manifest)

        roster_path = tmp_path / "synthetic-network-roster.csv"
        with roster_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["provider_npi", "provider_name", "network_name", "network_status",
                             "effective_start", "effective_end", "service_location",
                             "applicable_plan_reference"])
            writer.writerow(["1000000004", "Synthetic Site A", "Synthetic Choice", "in_network",
                             "2026-01-01", "2026-12-31", "Synthetic Location A", plan_reference])
            writer.writerow(["1000000012", "Synthetic Site B", "Synthetic Choice", "in_network",
                             "2026-01-01", "2026-12-31", "Synthetic Location B", plan_reference])
        analyst_page.get_by_label("Network roster CSV").set_input_files(str(roster_path))
        analyst_page.get_by_role("button", name="Validate and import roster").click()
        analyst_page.get_by_text("Network roster imported.").wait_for()

        # Build the main, fully reviewed audit as Admin, giving its approval
        # decision an independent contributor boundary to test in the browser.
        page.goto(origin + "/plan-specific-audits")
        page.get_by_role("heading", name="Plan-specific infusion drug audits").wait_for()
        page.get_by_role("tab", name="Plan Setup").click()
        page.get_by_label("Audit name").fill("RG01 Eligible Synthetic Audit")
        page.get_by_label("Source period").fill("2026-10-15/2026-10-15")
        page.get_by_role("button", name="Create audit").click()
        page.get_by_text("Plan audit created.").wait_for()
        main_audit = db_call(PlanAudit.objects.get, audit_name="RG01 Eligible Synthetic Audit")
        page.get_by_role("button", name="Run audit").click()
        page.get_by_text("Audit run finished.", exact=False).wait_for()
        page.get_by_role("tab", name="Review & Reports").click()
        page.get_by_role("button", name="Generate and download PDF").wait_for()
        with page.expect_download() as generated_pdf:
            page.get_by_role("button", name="Generate and download PDF").click()
        assert generated_pdf.value.suggested_filename.endswith(".pdf")
        main_audit = db_call(PlanAudit.objects.get, pk=main_audit.pk)
        current = _api(page, f"/api/v1/plan-audits/{main_audit.pk}/")
        assert current["status"] == 200
        binding = {"version": current["payload"]["current_version"],
                   "result_fingerprint": current["payload"]["result_fingerprint"],
                   "comments": "Synthetic browser approval attempt."}
        self_approval = _api(page, f"/api/v1/plan-audits/{main_audit.pk}/approve/",
                             method="POST", body=binding)
        assert self_approval["status"] == 403

        # Analyst runs a separate unresolved J1745 audit; its markup stays
        # explicitly Not comparable and cannot affect the eligible J9271 run.
        unresolved_summary, unresolved_manifest = _write_extract(tmp_path, plan_reference, "J1745", [
            ("1000000004", "90", "70"),
        ], "unresolved")
        analyst_page.get_by_role("tab", name="Plan Setup").click()
        _upload_summary(analyst_page, unresolved_summary, unresolved_manifest)
        analyst_page.get_by_label("Audit name").fill("RG01 Unresolved Synthetic Audit")
        analyst_page.get_by_label("Source period").fill("2026-10-15/2026-10-15")
        analyst_page.get_by_role("button", name="Create audit").click()
        analyst_page.get_by_text("Plan audit created.").wait_for()
        unresolved_audit = db_call(PlanAudit.objects.get, audit_name="RG01 Unresolved Synthetic Audit")
        analyst_page.get_by_role("button", name="Run audit").click()
        analyst_page.get_by_text("Audit run finished.", exact=False).wait_for()
        analyst_page.get_by_role("tab", name="Drug Cost Analysis").click()
        analyst_page.get_by_text("Not comparable", exact=True).first.wait_for()
        assert _api(analyst_page, f"/api/v1/plan-audits/{unresolved_audit.pk}/approve/",
                    method="POST", body={})["status"] in {403, 400}

        # Unassigned reviewer cannot read another tenant's audit, even by ID.
        other_import = db_call(ClaimsSummaryImport.objects.create,
            organization_id=other_org.pk, plan_id=other_plan.pk, source_file_name="synthetic.csv",
            file_hash=hashlib.sha256(f"{run_tag}-other".encode()).hexdigest(), schema_version="1.0",
            import_status="accepted", source_period="2026-10-15/2026-10-15")
        foreign_audit = db_call(PlanAudit.objects.create,
            organization_id=other_org.pk, plan_id=other_plan.pk, summary_import_id=other_import.pk,
            audit_name="RG01 Foreign Synthetic Draft", reporting_period="2026",
            created_by=admin_user)
        reviewer_page.goto(origin + "/plan-specific-audits")
        assert _api(reviewer_page, f"/api/v1/plan-audits/{foreign_audit.pk}/")["status"] == 404

        reviewer_page.get_by_label("Audit").select_option(str(main_audit.pk))
        reviewer_page.get_by_role("tab", name="Review & Reports").click()
        reviewer_page.get_by_role("heading", name="Review history").wait_for()
        reviewer_page.get_by_role("button", name="Approve audit").wait_for()
        reviewer_page.get_by_role("button", name="Approve audit").click()
        reviewer_page.get_by_text("Audit approved.").wait_for()
        assert main_audit.pk

        # Client can read only the assigned, published audit and downloads both
        # stored artifacts through actual browser requests.
        client_context = browser.new_context(viewport={"width": 390, "height": 844}, accept_downloads=True)
        client_page = new_page(client_context)
        _sign_in(client_page, origin, client_name, client_password, client_secret)
        client_page.goto(origin + "/plan-specific-audits")
        client_page.get_by_role("heading", name="Plan-specific infusion drug audits").wait_for()
        client_page.get_by_label("Audit").select_option(str(main_audit.pk))
        assert _api(client_page, f"/api/v1/plan-audits/{main_audit.pk}/results/")["status"] == 200
        assert _api(client_page, f"/api/v1/plan-audits/{foreign_audit.pk}/results/")["status"] == 404
        with client_page.expect_download() as csv_download:
            client_page.get_by_role("button", name="Download results CSV").click()
        assert csv_download.value.suggested_filename.endswith(".csv")
        with client_page.expect_download() as pdf_download:
            client_page.get_by_role("button", name="Download approved PDF").click()
        assert pdf_download.value.suggested_filename.endswith(".pdf")

        # Reviewer B rejects with a required reason; both the client and stale
        # Reviewer A bindings are denied immediately afterward.
        reviewer_b_context = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        reviewer_b_page = new_page(reviewer_b_context)
        _sign_in(reviewer_b_page, origin, reviewer_b_name, reviewer_b_password, reviewer_b_secret)
        reviewer_b_page.goto(origin + "/plan-specific-audits")
        reviewer_b_page.get_by_label("Audit").select_option(str(main_audit.pk))
        reviewer_b_page.get_by_role("tab", name="Review & Reports").click()
        reviewer_b_page.get_by_label("Review comment or decision reason").fill(" ")
        reject_button = reviewer_b_page.get_by_role("button", name="Reject audit")
        assert reject_button.is_disabled()
        reviewer_b_page.get_by_label("Review comment or decision reason").fill(
            "Synthetic review rejected; request corrected source evidence."
        )
        reject_button.click()
        reviewer_b_page.get_by_text("Audit rejected.").wait_for()
        assert _api(reviewer_page, f"/api/v1/plan-audits/{main_audit.pk}/approve/",
                    method="POST", body=binding)["status"] == 409
        for suffix in ("overview/", "results/", "report/", "export-csv/", "savings/"):
            assert _api(client_page, f"/api/v1/plan-audits/{main_audit.pk}/{suffix}")["status"] == 404
        client_page.reload()
        client_page.get_by_text("Rejected — publication revoked").wait_for()
        assert client_page.get_by_role("button", name="Download results CSV").is_disabled()

        # Revoke Reviewer A organization membership while that browser session
        # remains live; server-side authorization must deny its next request.
        page.goto(origin + "/plan-specific-audits")
        page.get_by_role("tab", name="Plan Setup").click()
        access_row = page.locator("li").filter(has_text=reviewer_a_name)
        access_row.get_by_role("button", name="Revoke").click()
        page.get_by_text("Organization access revoked.").wait_for()
        assert _api(reviewer_page, f"/api/v1/plan-audits/{main_audit.pk}/")["status"] == 404

        # Basic mobile width and native required-field validation.
        client_page.set_viewport_size({"width": 390, "height": 844})
        assert client_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(origin + "/user-management")
        required = page.locator('form input[name="username"]')
        assert required.evaluate("element => !element.checkValidity()")
        assert not page_errors, page_errors
        assert not console_errors, console_errors
        assert not [(method, path, status) for method, path, status in api_responses if status >= 500]

    except Exception:
        # Capture only screenshots after removing transient password/OTP input.
        for index, current_page in enumerate(pages):
            try:
                current_page.locator('input[type="password"], input[autocomplete="one-time-code"]').evaluate_all(
                    "elements => elements.forEach(element => element.value = '')")
                current_page.screenshot(path=tmp_path / f"rg01-failure-{index}.png", full_page=True)
            except Exception:
                pass
        raise
    finally:
        for browser_context in (reviewer_b_context, client_context, analyst_context, reviewer_context, context):
            if browser_context:
                browser_context.close()
        browser.close()
        playwright.stop()
        db_call(lambda: __import__("django.db").db.connections.close_all())
        db_executor.shutdown(wait=True)
