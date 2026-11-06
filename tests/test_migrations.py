"""Regression checks for legacy audit publication migration semantics."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class LegacyAuditApprovalMigrationTests(TransactionTestCase):
    reset_sequences = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_approved_legacy_audit_is_linked_but_not_published(self):
        target = [("pricing", "0010_claimsexplorerdataset_category_and_more")]
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        old_apps = executor.loader.project_state(target).apps

        User = old_apps.get_model("auth", "User")
        Organization = old_apps.get_model("pricing", "PlanOrganization")
        Plan = old_apps.get_model("pricing", "Plan")
        SummaryImport = old_apps.get_model("pricing", "ClaimsSummaryImport")
        PlanAudit = old_apps.get_model("pricing", "PlanAudit")
        AuditReview = old_apps.get_model("pricing", "AuditReview")

        user = User.objects.create(username="legacy-reviewer", password="unused")
        organization = Organization.objects.create(
            organization_name="Legacy migration test", organization_type="employer",
        )
        plan = Plan.objects.create(
            organization=organization, plan_name="Legacy plan", plan_reference="LEGACY-1",
        )
        source = SummaryImport.objects.create(
            organization=organization, plan=plan, source_file_name="legacy.csv",
            file_hash="a" * 64, schema_version="1.0", imported_by_id=user.pk,
            import_status="accepted", row_count=0, validation_summary={}, source_period="2026Q4",
        )
        old_audit = PlanAudit.objects.create(
            organization=organization, plan=plan, summary_import=source,
            audit_name="Previously approved audit", reporting_period="2026Q4",
            status="completed", created_by_id=user.pk, completed_at=timezone.now(),
            report_generated_at=timezone.now(), summary={"visible_allowed_amount": "123.45"},
        )
        AuditReview.objects.create(
            plan_audit=old_audit, reviewer_id=user.pk,
            review_status="approved", comments="Legacy approval",
        )

        latest = [("pricing", "0011_plan_audit_run_versions")]
        MigrationExecutor(connection).migrate(latest)
        new_apps = MigrationExecutor(connection).loader.project_state(latest).apps
        NewAudit = new_apps.get_model("pricing", "PlanAudit")
        NewRun = new_apps.get_model("pricing", "PlanAuditRun")
        NewReview = new_apps.get_model("pricing", "AuditReview")

        migrated_audit = NewAudit.objects.get(pk=old_audit.pk)
        migrated_run = NewRun.objects.get(plan_audit_id=old_audit.pk, version=1)
        migrated_review = NewReview.objects.get(plan_audit_id=old_audit.pk)
        assert migrated_audit.current_version == 1
        assert migrated_audit.published_version is None
        assert migrated_run.calculation_status == "legacy_unverified"
        assert migrated_run.publication_status == "unverified"
        assert migrated_run.review_status == "unverified"
        assert migrated_run.approved_by_id is None
        assert migrated_review.review_status == "approved"  # retained only as legacy history
        assert migrated_review.audit_run_id == migrated_run.pk
        assert migrated_run.source_sha256 == "a" * 64

