from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import AuditEvent, ProcessingJob, ReportRecord, SourceFile


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class ProtectedRootAdmin(UserAdmin):
    actions = tuple(action for action in UserAdmin.actions if action != "delete_selected")

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_superuser:
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.is_superuser:
            fields.extend(field for field in (
                "username", "is_active", "is_staff", "is_superuser", "groups",
                "user_permissions",
            ) if field not in fields)
        return fields


@admin.register(SourceFile)
class SourceFileAdmin(admin.ModelAdmin):
    list_display = ("source_key", "relative_path", "status", "reporting_period", "sha256", "created_at")
    list_filter = ("source_key", "status")
    search_fields = ("relative_path", "sha256", "source_url")
    readonly_fields = ("created_at",)


@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = ("job_type", "status", "progress", "requested_by", "created_at")
    list_filter = ("job_type", "status")
    readonly_fields = ("created_at", "started_at", "finished_at")


@admin.register(ReportRecord)
class ReportRecordAdmin(admin.ModelAdmin):
    list_display = ("name", "report_type", "status", "generated_at", "approved_by", "approved_at")
    list_filter = ("report_type", "status")
    readonly_fields = ("generated_at",)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("action", "actor", "entity_type", "entity_id", "created_at")
    list_filter = ("action", "entity_type")
    search_fields = ("entity_id", "actor__username")
    readonly_fields = ("actor", "action", "entity_type", "entity_id", "details", "created_at")

from .models import (
    AuditReview, ClaimsExplorerDataset, ClaimsSummaryImport, ClaimsSummaryRecord, NetworkRosterEntry,
    Plan, PlanAudit, PlanAuditResult, PlanOrganization, SavingsOpportunity,
)

for model in (
    PlanOrganization, Plan, ClaimsSummaryImport, ClaimsSummaryRecord,
    NetworkRosterEntry,
):
    admin.site.register(model)


@admin.register(PlanAudit)
class PlanAuditAdmin(admin.ModelAdmin):
    readonly_fields = ("created_by",)


class AuditRunRowAdmin(admin.ModelAdmin):
    """Expose run snapshots for inspection without allowing history edits."""

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(PlanAuditResult, AuditRunRowAdmin)
admin.site.register(SavingsOpportunity, AuditRunRowAdmin)

from .models import PlanOrganizationAccess

admin.site.register(PlanOrganizationAccess)


@admin.register(ClaimsExplorerDataset)
class ClaimsExplorerDatasetAdmin(admin.ModelAdmin):
    list_display = ("organization", "dataset_identifier", "registered_by", "registered_at")
    list_filter = ("dataset_identifier",)
    readonly_fields = ("registered_by", "registered_at")

    def save_model(self, request, obj, form, change):
        if not change and not obj.registered_by_id:
            obj.registered_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(AuditReview)
class AuditReviewAdmin(admin.ModelAdmin):
    list_display = ("plan_audit", "reviewer", "review_status", "reviewed_at")
    list_filter = ("review_status",)
    readonly_fields = ("plan_audit", "reviewer", "review_status", "comments", "reviewed_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import BrokerEvaluation


@admin.register(BrokerEvaluation)
class BrokerEvaluationAdmin(admin.ModelAdmin):
    list_display = ("organization_name", "status", "evaluation_date", "pilot_interest", "recorded_by", "created_at")
    list_filter = ("status", "pilot_interest")
    search_fields = ("organization_name", "contact_reference", "feedback")
    readonly_fields = ("created_at", "updated_at")

