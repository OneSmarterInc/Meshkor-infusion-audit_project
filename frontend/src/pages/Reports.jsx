import { useState } from "react";
import { postJSON, patchJSON, deleteJSON, useApi, downloadFile } from "../api.js";
import { Loading, Table } from "../components.jsx";

const STATUS_OPTIONS = [
  { value: "not_contacted", label: "Not contacted" },
  { value: "contacted", label: "Contacted" },
  { value: "meeting_scheduled", label: "Meeting scheduled" },
  { value: "report_evaluated", label: "Report evaluated" },
  { value: "feedback_received", label: "Feedback received" },
  { value: "pilot_interested", label: "Pilot interested" },
  { value: "declined", label: "Declined" },
];

export default function Reports({ user }) {
  const [version, setVersion] = useState(0);
  const [evalVersion, setEvalVersion] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [evalMessage, setEvalMessage] = useState("");
  const [evalError, setEvalError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [downloadingXlsx, setDownloadingXlsx] = useState(false);
  const [downloadingCsv, setDownloadingCsv] = useState(false);
  const [activeTab, setActiveTab] = useState("evaluations");
  const [copiedQuestions, setCopiedQuestions] = useState(false);
  const [editingId, setEditingId] = useState(null);

  const initialForm = {
    organization_name: "",
    contact_reference: "",
    report_version: "2024-Q3 v1.0",
    evaluation_date: new Date().toISOString().slice(0, 10),
    status: "report_evaluated",
    feedback: "",
    pilot_interest: false,
    next_action: "",
    evidence_reference: "",
  };
  const [form, setForm] = useState(initialForm);
  const [saving, setSaving] = useState(false);

  const isAdmin = user?.roles?.some((role) => ["SUPER_ADMIN", "Admin"].includes(role));
  const canRelease = user?.roles?.some((role) => ["SUPER_ADMIN", "REVIEWER", "Admin"].includes(role));
  const canReviewReports = user?.roles?.some((role) => ["SUPER_ADMIN", "ANALYST", "REVIEWER", "Admin"].includes(role));

  const reports = useApi("reports", null, [version]);
  const evaluationsState = useApi(canReviewReports ? "reports/evaluations" : null, null, [evalVersion]);
  const packageState = useApi(canReviewReports ? "reports/evaluation-package" : null, null, []);

  const currentReportReleased = reports.data?.some((report) => report.status === "released");
  const canDownload = isAdmin || currentReportReleased;

  async function release() {
    setError(""); setMessage("");
    try {
      const result = await postJSON("reports/approval/sign-off");
      setMessage(result.signed ? "The current report is released for Viewer access." : "The report was not released.");
      setVersion((v) => v + 1);
    } catch (e) { setError(e.message); }
  }

  async function handleDownload() {
    setError("");
    setDownloading(true);
    try {
      await downloadFile("reports/market-analysis/pdf/", "ohio_infusion_drug_market_report.pdf");
    } catch (e) {
      setError(e.message);
    } finally {
      setDownloading(false);
    }
  }

  async function handleCsvDownload() {
    setError("");
    setDownloadingCsv(true);
    try {
      await downloadFile("hospital-drug-price-comparisons/exports/csv/", "infusion_audit_report_appendix.csv");
    } catch (e) { setError(e.message); }
    finally { setDownloadingCsv(false); }
  }

  async function handleXlsxDownload() {
    setError("");
    setDownloadingXlsx(true);
    try {
      await downloadFile("hospital-drug-price-comparisons/exports/excel/", "infusion_audit_comparisons.xlsx");
    } catch (e) {
      setError(e.message);
    } finally {
      setDownloadingXlsx(false);
    }
  }

  async function handleSaveEvaluation(e) {
    e.preventDefault();
    setEvalError("");
    setEvalMessage("");
    setSaving(true);
    try {
      if (editingId) {
        await patchJSON(`reports/evaluations/${editingId}/`, form);
        setEvalMessage(`Evaluation for ${form.organization_name} updated successfully.`);
        setEditingId(null);
      } else {
        await postJSON("reports/evaluations/", form);
        setEvalMessage(`Evaluation for ${form.organization_name} recorded.`);
      }
      setForm(initialForm);
      setEvalVersion((v) => v + 1);
    } catch (err) {
      setEvalError(err.message);
    } finally {
      setSaving(false);
    }
  }

  function handleStartEdit(item) {
    setEditingId(item.id);
    setForm({
      organization_name: item.organization_name || "",
      contact_reference: item.contact_reference || "",
      report_version: item.report_version || "2024-Q3 v1.0",
      evaluation_date: item.evaluation_date || "",
      status: item.status || "report_evaluated",
      feedback: item.feedback || "",
      pilot_interest: Boolean(item.pilot_interest),
      next_action: item.next_action || "",
      evidence_reference: item.evidence_reference || "",
    });
    setEvalMessage("");
    setEvalError("");
  }

  function handleCancelEdit() {
    setEditingId(null);
    setForm(initialForm);
    setEvalError("");
  }

  async function handleDeleteEvaluation(item) {
    if (!window.confirm(`Delete evaluation record for "${item.organization_name}"?`)) return;
    setEvalError("");
    setEvalMessage("");
    try {
      await deleteJSON(`reports/evaluations/${item.id}/`);
      setEvalMessage(`Evaluation for ${item.organization_name} deleted.`);
      if (editingId === item.id) {
        setEditingId(null);
        setForm(initialForm);
      }
      setEvalVersion((v) => v + 1);
    } catch (err) {
      setEvalError(err.message);
    }
  }

  function handleCopyQuestions(questions) {
    if (!questions || !questions.length) return;
    const text = questions
      .map((q, idx) => `${idx + 1}. [${q.category}] ${q.question}\n   ${q.description}`)
      .join("\n\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopiedQuestions(true);
      window.setTimeout(() => setCopiedQuestions(false), 2500);
    });
  }

  const milestone = evaluationsState.data?.milestone || {
    required_count: 3,
    verified_count: 0,
    remaining_count: 3,
    prerequisite_met: false,
    distinct_organizations_verified: [],
  };

  const evaluations = evaluationsState.data?.evaluations || [];
  const evalPackage = packageState.data || {};

  return (
    <>
      <p className="lede">
        {isAdmin
          ? "Manage reports, releases, and broker evaluations."
          : canReviewReports
          ? "Review reports, evaluations, and release status."
          : "Download released reports and comparison data."}
      </p>

      <section>
        <h2>Current market report</h2>
        <p className="muted">
          Posted hospital prices are not plan claims or guaranteed savings.
        </p>
        <div className="filters">
          <button type="button" className="button" onClick={handleDownload} disabled={downloading || !canDownload}>
            {downloading ? "Generating PDF…" : canDownload ? "Download PDF Report" : "PDF available after release"}
          </button>
          <button type="button" className="button secondary" onClick={handleCsvDownload} disabled={downloadingCsv || !canDownload}>
            {downloadingCsv ? "Preparing appendix…" : canDownload ? "Download CSV Appendix" : "CSV available after release"}
          </button>
          {isAdmin && (
            <button type="button" className="button secondary" onClick={handleXlsxDownload} disabled={downloadingXlsx}>
              {downloadingXlsx ? "Generating workbook…" : "Download Admin Excel Export"}
            </button>
          )}
          {canRelease && (
            <button type="button" onClick={release} disabled={!reports.data?.some((report) => report.status === "generated")}>
              Release current report
            </button>
          )}
        </div>
        {message && <p role="status" className="success-message">{message}</p>}
        {error && <p role="alert" className="note error">{error}</p>}
      </section>

      <Loading state={reports}>
        {(rows) => (
          <section>
            <h2>Report history</h2>
            <Table
              rows={rows}
              columns={[
                { key: "name", label: "Report" },
                { key: "status", label: "Status" },
                { key: "generated_at", label: "Generated", render: (r) => new Date(r.generated_at).toLocaleString() },
                ...(isAdmin ? [{ key: "built_by", label: "Built by", render: (r) => r.built_by || "—" }] : []),
                { key: "approved_by", label: "Released by", render: (r) => r.approved_by || "—" },
              ]}
            />
          </section>
        )}
      </Loading>

      {canReviewReports && (
        <section style={{ marginTop: "32px", borderTop: "2px solid var(--line)", paddingTop: "24px" }}>
          <div className="panel-header">
            <div>
              <h2 className="panel-title" style={{ fontSize: "20px" }}>Broker and TPA evaluations</h2>
              <p className="panel-subtitle">Three verified evaluations unlock paid pilots.</p>
            </div>
            <div>
              {milestone.prerequisite_met ? (
                <span className="pill s-ok" style={{ fontSize: "13px", padding: "4px 12px" }}>
                  Pilot prerequisite met
                </span>
              ) : (
                <span className="pill s-review_range" style={{ fontSize: "13px", padding: "4px 12px" }}>
                  Pilot prerequisite pending · {milestone.remaining_count} remaining
                </span>
              )}
            </div>
          </div>

          <div className="kpi-grid" style={{ margin: "16px 0" }}>
            <div className={`kpi-card ${milestone.prerequisite_met ? "highlight" : "warning"}`}>
              <div className="stat-label">Verified Evaluations</div>
              <div className="stat-value" style={{ fontSize: "28px" }}>
                {milestone.verified_count} <span style={{ fontSize: "15px", color: "var(--muted)", fontWeight: "normal" }}>/ {milestone.required_count}</span>
              </div>
              {milestone.distinct_organizations_verified.length > 0 && <div className="stat-hint">{milestone.distinct_organizations_verified.join(", ")}</div>}
            </div>

            <div className="kpi-card">
              <div className="stat-label">Interactions</div>
              <div className="stat-value" style={{ fontSize: "28px" }}>{evaluations.length}</div>
            </div>
          </div>

          {!milestone.prerequisite_met && (
            <div className="banner" style={{ margin: "12px 0 20px" }}>
              Only report evaluations with attributable feedback or evidence count toward the pilot prerequisite.
            </div>
          )}

          <div className="filters" style={{ margin: "16px 0 20px", display: "flex", gap: "8px" }}>
            <button
              type="button"
              className={`button ${activeTab === "evaluations" ? "" : "secondary"}`}
              onClick={() => setActiveTab("evaluations")}
            >
              Evaluations ({evaluations.length})
            </button>
            <button
              type="button"
              className={`button ${activeTab === "package" ? "" : "secondary"}`}
              onClick={() => setActiveTab("package")}
            >
              Product scope
            </button>
            <button
              type="button"
              className={`button ${activeTab === "questions" ? "" : "secondary"}`}
              onClick={() => setActiveTab("questions")}
            >
              Questions
            </button>
            <button
              type="button"
              className={`button ${activeTab === "checklist" ? "" : "secondary"}`}
              onClick={() => setActiveTab("checklist")}
            >
              Onboarding
            </button>
          </div>

          {activeTab === "evaluations" && (
            <div>
              {isAdmin && (
                <div className="dashboard-panel" style={{ marginBottom: "20px" }}>
                  <div className="panel-header">
                    <div>
                      <h3 className="panel-title">{editingId ? "Edit Evaluation Record" : "Record Genuine Broker/TPA Evaluation"}</h3>
                      <p className="panel-subtitle">
                        Record attributable feedback; contacts and scheduled meetings do not count.
                      </p>
                    </div>
                    {editingId && (
                      <button type="button" className="button secondary" onClick={handleCancelEdit}>
                        Cancel Edit
                      </button>
                    )}
                  </div>

                  {evalMessage && <p role="status" className="success-message">{evalMessage}</p>}
                  {evalError && <p role="alert" className="note error">{evalError}</p>}

                  <form onSubmit={handleSaveEvaluation} className="plan-audit-form">
                    <div className="grid2">
                      <label>
                        Organization Name *
                        <input
                          type="text"
                          required
                          maxLength={200}
                          placeholder="e.g. Apex Benefits Consulting, Buckeye TPA"
                          value={form.organization_name}
                          onChange={(e) => setForm({ ...form, organization_name: e.target.value })}
                        />
                      </label>
                      <label>
                        Contact Reference
                        <input
                          type="text"
                          maxLength={200}
                          placeholder="e.g. Jane Doe, VP Self-Funded Solutions"
                          value={form.contact_reference}
                          onChange={(e) => setForm({ ...form, contact_reference: e.target.value })}
                        />
                      </label>
                    </div>

                    <div className="grid2">
                      <label>
                        Report Version Evaluated
                        <input
                          type="text"
                          maxLength={120}
                          value={form.report_version}
                          onChange={(e) => setForm({ ...form, report_version: e.target.value })}
                        />
                      </label>
                      <label>
                        Evaluation Date
                        <input
                          type="date"
                          value={form.evaluation_date}
                          onChange={(e) => setForm({ ...form, evaluation_date: e.target.value })}
                        />
                      </label>
                    </div>

                    <div className="grid2">
                      <label>
                        Evaluation Status *
                        <select
                          value={form.status}
                          onChange={(e) => setForm({ ...form, status: e.target.value })}
                        >
                          {STATUS_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ))}
                        </select>
                      </label>
                      <label style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "24px" }}>
                        <input
                          type="checkbox"
                          checked={form.pilot_interest}
                          onChange={(e) => setForm({ ...form, pilot_interest: e.target.checked })}
                          style={{ width: "20px", height: "20px" }}
                        />
                        <span style={{ fontWeight: 600 }}>Expressed interest in paid Plan Audit pilot</span>
                      </label>
                    </div>

                    <label>
                      Attributable Feedback & Evaluation Notes
                      <textarea
                        rows={3}
                        placeholder="Detailed feedback regarding hospital rates, ASP comparison, clarity, relevance to employer clients..."
                        value={form.feedback}
                        onChange={(e) => setForm({ ...form, feedback: e.target.value })}
                        style={{ width: "100%", padding: "8px", border: "1px solid var(--line)", borderRadius: "6px" }}
                      />
                    </label>

                    <div className="grid2">
                      <label>
                        Evidence Reference (document, email ID, meeting notes ref)
                        <input
                          type="text"
                          placeholder="e.g. DOCS/EVAL_2024_APEX.pdf or Meeting notes 2024-11-01"
                          value={form.evidence_reference}
                          onChange={(e) => setForm({ ...form, evidence_reference: e.target.value })}
                        />
                      </label>
                      <label>
                        Next Action / Follow-up
                        <input
                          type="text"
                          maxLength={500}
                          placeholder="e.g. Schedule pilot scope presentation with self-funded employer client"
                          value={form.next_action}
                          onChange={(e) => setForm({ ...form, next_action: e.target.value })}
                        />
                      </label>
                    </div>

                    <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
                      {editingId && (
                        <button type="button" className="button secondary" onClick={handleCancelEdit}>
                          Cancel
                        </button>
                      )}
                      <button type="submit" className="button" disabled={saving}>
                        {saving ? "Saving…" : editingId ? "Update Evaluation" : "Record Evaluation"}
                      </button>
                    </div>
                  </form>
                </div>
              )}

              <div className="dashboard-panel">
                <div className="panel-header">
                  <div>
                    <h3 className="panel-title">Recorded Broker / TPA Evaluations</h3>
                  </div>
                </div>

                {evaluations.length === 0 ? (
                  <div className="note">
                    No evaluations yet. Record feedback after a broker or TPA reviews the report.
                  </div>
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Organization</th>
                          <th>Contact</th>
                          <th>Status</th>
                          <th>Milestone Count</th>
                          <th>Date</th>
                          <th>Pilot Interest</th>
                          <th>Evidence / Notes</th>
                          <th>Recorded By</th>
                          {isAdmin && <th>Actions</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {evaluations.map((item) => (
                          <tr key={item.id}>
                            <td>
                              <strong>{item.organization_name}</strong>
                              {item.report_version && (
                                <div style={{ fontSize: "11px", color: "var(--muted)" }}>{item.report_version}</div>
                              )}
                            </td>
                            <td>{item.contact_reference || "—"}</td>
                            <td>
                              <span className={`pill ${item.is_verified ? "s-ok" : "s-review_range"}`}>
                                {item.status_display}
                              </span>
                            </td>
                            <td>
                              {item.is_verified ? (
                                <span style={{ color: "var(--ok-fg)", fontWeight: 600 }}>✓ Verified</span>
                              ) : (
                                <span style={{ color: "var(--muted)" }}>Incomplete</span>
                              )}
                            </td>
                            <td>{item.evaluation_date || "—"}</td>
                            <td>{item.pilot_interest ? <span className="pill s-ok">Yes</span> : "No"}</td>
                            <td>
                              <div style={{ maxWidth: "260px", fontSize: "12px" }}>
                                {item.feedback && <div style={{ marginBottom: "4px" }}>{item.feedback}</div>}
                                {item.evidence_reference && (
                                  <div style={{ fontSize: "11px", color: "var(--muted)" }}>Ref: {item.evidence_reference}</div>
                                )}
                                {item.next_action && (
                                  <div style={{ fontSize: "11px", color: "var(--accent)" }}>Next: {item.next_action}</div>
                                )}
                                {!item.feedback && !item.evidence_reference && <span style={{ color: "var(--muted)" }}>—</span>}
                              </div>
                            </td>
                            <td>{item.recorded_by || "—"}</td>
                            {isAdmin && (
                              <td>
                                <div style={{ display: "flex", gap: "6px" }}>
                                  <button
                                    type="button"
                                    className="button secondary"
                                    style={{ minHeight: "28px", padding: "2px 8px", fontSize: "12px" }}
                                    onClick={() => handleStartEdit(item)}
                                  >
                                    Edit
                                  </button>
                                  <button
                                    type="button"
                                    className="button secondary"
                                    style={{ minHeight: "28px", padding: "2px 8px", fontSize: "12px", color: "var(--bad-fg)" }}
                                    onClick={() => handleDeleteEvaluation(item)}
                                  >
                                    Delete
                                  </button>
                                </div>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === "package" && (
            <div className="dashboard-panel">
              <h3 className="panel-title">Product scope</h3>

              {evalPackage.product_explanation ? (
                <div style={{ display: "grid", gap: "16px", marginTop: "12px" }}>
                  <div style={{ padding: "14px", border: "1px solid var(--line)", borderRadius: "8px", background: "var(--bg)" }}>
                    <h4 style={{ margin: "0 0 6px", fontSize: "14px" }}>What MeshKor Does</h4>
                    <p style={{ margin: 0, fontSize: "13px", lineHeight: "1.5" }}>{evalPackage.product_explanation.what_meshkor_does}</p>
                  </div>

                  <div className="grid2">
                    <div style={{ padding: "14px", border: "1px solid var(--line)", borderRadius: "8px", background: "var(--bg)" }}>
                      <h4 style={{ margin: "0 0 6px", fontSize: "14px" }}>Phase 1: Hospital Price Intelligence</h4>
                      <p style={{ margin: 0, fontSize: "13px", lineHeight: "1.5" }}>{evalPackage.product_explanation.what_phase1_delivers}</p>
                    </div>
                    <div style={{ padding: "14px", border: "1px solid var(--line)", borderRadius: "8px", background: "var(--bg)" }}>
                      <h4 style={{ margin: "0 0 6px", fontSize: "14px" }}>Phase 4: Plan-Specific Infusion Audit</h4>
                      <p style={{ margin: 0, fontSize: "13px", lineHeight: "1.5" }}>{evalPackage.product_explanation.what_plan_audit_adds}</p>
                    </div>
                  </div>

                  <div style={{ padding: "14px", border: "1px solid var(--line)", borderRadius: "8px", background: "var(--bg)" }}>
                    <h4 style={{ margin: "0 0 6px", fontSize: "14px" }}>Why Infusion Drugs Matter to Self-Funded Employers</h4>
                    <p style={{ margin: 0, fontSize: "13px", lineHeight: "1.5" }}>{evalPackage.product_explanation.why_it_matters_to_employers}</p>
                  </div>

                  <div className="grid2">
                    <div style={{ padding: "14px", border: "1px solid var(--line)", borderRadius: "8px", background: "var(--bg)" }}>
                      <h4 style={{ margin: "0 0 6px", fontSize: "14px" }}>Information Needed from Client</h4>
                      <p style={{ margin: 0, fontSize: "13px", lineHeight: "1.5" }}>{evalPackage.product_explanation.information_needed_from_client}</p>
                    </div>
                    <div style={{ padding: "14px", border: "1px solid var(--line)", borderRadius: "8px", background: "var(--bg)" }}>
                      <h4 style={{ margin: "0 0 6px", fontSize: "14px" }}>Privacy & Security Boundary</h4>
                      <p style={{ margin: 0, fontSize: "13px", lineHeight: "1.5" }}>{evalPackage.product_explanation.privacy_and_security_boundary}</p>
                    </div>
                  </div>

                  <div style={{ padding: "14px", border: "1px solid var(--line)", borderRadius: "8px", background: "var(--bg)" }}>
                    <h4 style={{ margin: "0 0 6px", fontSize: "14px" }}>Results & Analytical Limits</h4>
                    <p style={{ margin: 0, fontSize: "13px", lineHeight: "1.5" }}>{evalPackage.product_explanation.results_and_limits}</p>
                  </div>
                </div>
              ) : (
                <div className="note">Package data unavailable.</div>
              )}
            </div>
          )}

          {activeTab === "questions" && (
            <div className="dashboard-panel">
              <div className="panel-header">
                <div>
                  <h3 className="panel-title">Evaluation questions</h3>
                </div>
                <div>
                  <button
                    type="button"
                    className="button"
                    onClick={() => handleCopyQuestions(evalPackage.evaluation_questions)}
                  >
                    {copiedQuestions ? "Copied" : "Copy questions"}
                  </button>
                </div>
              </div>

              {evalPackage.evaluation_questions ? (
                <div style={{ display: "grid", gap: "12px", marginTop: "12px" }}>
                  {evalPackage.evaluation_questions.map((q, idx) => (
                    <div
                      key={q.id}
                      style={{
                        padding: "14px",
                        border: "1px solid var(--line)",
                        borderRadius: "8px",
                        background: "var(--panel)",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                        <span style={{ fontWeight: 700, fontSize: "15px" }}>
                          {idx + 1}. {q.question}
                        </span>
                        <span className="pill s-ok" style={{ fontSize: "11px" }}>{q.category}</span>
                      </div>
                      <p style={{ margin: 0, color: "var(--muted)", fontSize: "13px", lineHeight: "1.4" }}>
                        {q.description}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="note">Evaluation questions unavailable.</div>
              )}
            </div>
          )}

          {activeTab === "checklist" && (
            <div className="dashboard-panel">
              <div className="panel-header">
                <div>
                  <h3 className="panel-title">Pilot onboarding</h3>
                </div>
              </div>

              {evalPackage.pilot_onboarding_checklist ? (
                <div className="table-wrap" style={{ marginTop: "12px" }}>
                  <table>
                    <thead>
                      <tr>
                        <th style={{ width: "60px" }}>Step</th>
                        <th>Milestone / Activity</th>
                        <th>Details</th>
                      </tr>
                    </thead>
                    <tbody>
                      {evalPackage.pilot_onboarding_checklist.map((step) => (
                        <tr key={step.step}>
                          <td>
                            <strong>#{step.step}</strong>
                          </td>
                          <td>
                            <strong>{step.title}</strong>
                          </td>
                          <td style={{ fontSize: "13px", color: "var(--muted)" }}>
                            {step.description}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="note">Checklist unavailable.</div>
              )}
            </div>
          )}
        </section>
      )}
    </>
  );
}
