import { useEffect, useState } from "react";
import { downloadFile, fmt, useApi } from "../api.js";
import { Loading, Table } from "../components.jsx";

export default function ClientPlanAudits() {
  const state = useApi("plan-audits");
  const approved = (state.data || []).filter((audit) => audit.can_view_results && audit.publication_status === "published");
  const [selectedId, setSelectedId] = useState("");
  useEffect(() => {
    if (!approved.some((audit) => String(audit.id) === selectedId)) setSelectedId(approved[0] ? String(approved[0].id) : "");
  }, [approved.map((audit) => audit.id).join(","), selectedId]);
  const selected = approved.find((audit) => String(audit.id) === selectedId);
  const overview = useApi(selected ? `plan-audits/${selected.id}/overview` : null, null, [selected?.id]);
  const metrics = overview.data?.metrics || {};
  return <div className="page-stack">
    <section className="page-intro"><div><p className="eyebrow">Client workspace</p><h2>Approved plan reports</h2>
      <p className="muted">Published reports for organizations assigned to your account.</p></div></section>
    <Loading state={state}>{() => approved.length ? <>
      <section><h3>Published audits</h3><Table rows={approved} columns={[
        { key: "audit_name", label: "Audit" }, { key: "organization_name", label: "Organization" },
        { key: "plan_name", label: "Plan" }, { key: "reporting_period", label: "Reporting period" },
        { key: "publication_status", label: "Status", render: () => "Published" },
        { key: "id", label: "View", render: (row) => <button type="button" onClick={() => setSelectedId(String(row.id))} aria-pressed={String(row.id) === selectedId}>View summary</button> },
      ]} /></section>
      {selected && <section className="dashboard-panel"><h3>{selected.audit_name} · {selected.reporting_period}</h3>
        <Loading state={overview}>{() => <div className="plan-audit-metrics">
          <article><span>Historical allowed spend</span><strong>{fmt.usd(metrics.visible_allowed_amount)}</strong></article>
          <article><span>Historical paid spend</span><strong>{fmt.usd(metrics.visible_paid_amount)}</strong></article>
          <article><span>Drugs analyzed</span><strong>{fmt.n(metrics.drugs_analyzed)}</strong></article>
          <article><span>Sites in claims</span><strong>{fmt.n(metrics.sites_with_claims)}</strong></article>
          <article><span>Verified savings opportunities</span><strong>{metrics.potential_allowed_savings == null ? "—" : fmt.usd(metrics.potential_allowed_savings)}</strong></article>
        </div>}</Loading>
        <p className="muted">Published totals use approved, unsuppressed aggregate data. Savings are shown only after clinical and data validation.</p>
        <div className="role-dashboard-links">
          <button type="button" onClick={() => downloadFile(`plan-audits/${selected.id}/report/`, `plan-audit-${selected.id}.pdf`)}>Download approved PDF</button>
          <button type="button" onClick={() => downloadFile(`plan-audits/${selected.id}/export-csv/`, `plan-audit-${selected.id}.csv`)}>Download approved CSV</button>
        </div>
      </section>}
    </> : <p className="note">No approved plan reports are currently assigned to your account.</p>}</Loading>
  </div>;
}
