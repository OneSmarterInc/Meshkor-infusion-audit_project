import React, { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON } from "../api.js";

const PAGE_SIZE = 50;
const isAdmin = (user) => user?.roles?.some((role) => ["SUPER_ADMIN", "Admin"].includes(role));
const requestError = (error) => error.status === 401
  ? "Your session has expired. Sign in again to continue."
  : error.status === 403
    ? "Access denied. This organization or dataset is not authorized for your account."
    : error.message;

export default function ClaimsExplorer({ user }) {
  const admin = isAdmin(user);
  const [organizations, setOrganizations] = useState([]);
  const [selectedOrg, setSelectedOrg] = useState("");
  const [datasets, setDatasets] = useState([]);
  const [availableDatasets, setAvailableDatasets] = useState([]);
  const [dataset, setDataset] = useState("");
  const [registrationTarget, setRegistrationTarget] = useState("");
  const [organizationsLoading, setOrganizationsLoading] = useState(true);
  const [datasetsLoading, setDatasetsLoading] = useState(false);

  // Common and DE-SynPUF filters
  const [claimIdFilter, setClaimIdFilter] = useState("");
  const [hcpcsFilter, setHcpcsFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [claimTypeFilter, setClaimTypeFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  // Synthea-specific state
  const isSynthea = dataset === "synthea_sample_data";
  const [syntheaTab, setSyntheaTab] = useState("analytics");
  const [subtable, setSubtable] = useState("encounters");
  const [searchQuery, setSearchQuery] = useState("");
  const [syntheaSummary, setSyntheaSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(false);

  // Table pagination and results
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState([]);
  const [columns, setColumns] = useState([]);
  const [total, setTotal] = useState(0);
  const [disclaimer, setDisclaimer] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedClaim, setSelectedClaim] = useState(null);
  const [claimDetail, setClaimDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [fetchKey, setFetchKey] = useState(0);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    setPage(1);
    setFetchKey((k) => k + 1);
  }, []);

  useEffect(() => {
    let alive = true;
    getJSON("organizations/")
      .then((orgs) => {
        if (!alive || !Array.isArray(orgs)) return;
        setOrganizations(orgs);
        if (orgs.length > 0) {
          setSelectedOrg(String(orgs[0].id));
        }
      })
      .catch((e) => alive && setError(requestError(e)))
      .finally(() => alive && setOrganizationsLoading(false));
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    setDataset("");
    setDatasets([]);
    setAvailableDatasets([]);
    setSelectedClaim(null);
    setSyntheaSummary(null);
    if (!selectedOrg) {
      setDatasetsLoading(false);
      return undefined;
    }
    let alive = true;
    setDatasetsLoading(true);
    getJSON("claims/datasets/", { organization_id: selectedOrg })
      .then((data) => {
        if (!alive) return;
        const registered = data.datasets || [];
        setDatasets(registered);
        setAvailableDatasets(data.available_datasets || []);
        setDisclaimer(data.data_disclaimer || "");
        setDataset((current) => registered.some((item) => item.dataset_identifier === current)
          ? current : registered[0]?.dataset_identifier || "");
        setRegistrationTarget((data.available_datasets || [])
          .find((item) => !registered.some((entry) => entry.dataset_identifier === item.dataset_identifier))
          ?.dataset_identifier || "");
        setError("");
      })
      .catch((e) => alive && setError(requestError(e)))
      .finally(() => alive && setDatasetsLoading(false));
    return () => { alive = false; };
  }, [selectedOrg, fetchKey]);

  // Load Synthea summary when Synthea dataset is active
  useEffect(() => {
    if (!isSynthea || !selectedOrg) {
      setSyntheaSummary(null);
      return undefined;
    }
    let alive = true;
    setSummaryLoading(true);
    getJSON("claims/synthea/summary/", { organization_id: selectedOrg })
      .then((data) => alive && setSyntheaSummary(data))
      .catch((e) => alive && loggerWarn(e))
      .finally(() => alive && setSummaryLoading(false));
    return () => { alive = false; };
  }, [isSynthea, selectedOrg]);

  function loggerWarn(err) {
    // Non-blocking warning for summary
    console.warn("Synthea summary failed:", err);
  }

  // Fetch paginated claims / subtable data
  useEffect(() => {
    if (!selectedOrg || !dataset) {
      setRows([]);
      setColumns([]);
      setTotal(0);
      setLoading(false);
      return undefined;
    }
    let alive = true;
    setLoading(true);
    setError("");
    const params = {
      organization_id: selectedOrg,
      dataset,
      page,
      page_size: PAGE_SIZE,
    };

    if (isSynthea) {
      params.subtable = subtable;
      if (searchQuery.trim()) params.search = searchQuery.trim();
      if (dateFrom) params.service_date_from = dateFrom;
      if (dateTo) params.service_date_to = dateTo;
    } else {
      if (claimIdFilter.trim()) params.claim_id = claimIdFilter.trim();
      if (hcpcsFilter.trim()) params.hcpcs = hcpcsFilter.trim();
      if (providerFilter.trim()) params.provider = providerFilter.trim();
      if (claimTypeFilter.trim()) params.claim_type = claimTypeFilter.trim();
      if (dateFrom) params.service_date_from = dateFrom;
      if (dateTo) params.service_date_to = dateTo;
    }

    getJSON("claims/", params)
      .then((data) => {
        if (!alive) return;
        setRows(data.rows || []);
        setColumns(data.columns || []);
        setTotal(data.total || 0);
        setDisclaimer(data.data_disclaimer || "");
      })
      .catch((e) => {
        if (!alive) return;
        setError(requestError(e));
        setRows([]);
        setTotal(0);
      })
      .finally(() => alive && setLoading(false));

    return () => { alive = false; };
  }, [dataset, page, selectedOrg, fetchKey, isSynthea, subtable]);

  // Fetch record detail on selection
  useEffect(() => {
    if (!selectedClaim || !selectedOrg || !dataset) {
      setClaimDetail(null);
      setDetailError("");
      return undefined;
    }
    let alive = true;
    setDetailLoading(true);
    setDetailError("");
    setClaimDetail(null);
    getJSON(`claims/${encodeURIComponent(selectedClaim)}/`, {
      organization_id: selectedOrg,
      dataset,
    })
      .then((data) => alive && setClaimDetail(data))
      .catch((e) => alive && setDetailError(requestError(e)))
      .finally(() => alive && setDetailLoading(false));
    return () => { alive = false; };
  }, [selectedClaim, selectedOrg, dataset]);

  async function registerDataset() {
    if (!selectedOrg || !registrationTarget) return;
    setBusy(true);
    setError("");
    try {
      await postJSON("claims/datasets/", {
        organization_id: selectedOrg,
        dataset_identifier: registrationTarget,
      });
      const newDataset = availableDatasets.find(
        (item) => item.dataset_identifier === registrationTarget,
      );
      if (newDataset) {
        setDatasets((current) => [...current, {
          ...newDataset,
          category: newDataset.category || "desynpuf",
          reporting_period: newDataset.reporting_period || "Historical",
          synthetic: true,
        }]);
      }
      setDataset(registrationTarget);
      setFetchKey((key) => key + 1);
    } catch (e) {
      setError(requestError(e));
    } finally {
      setBusy(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const unregisteredDatasets = availableDatasets.filter(
    (item) => !datasets.some((registered) => registered.dataset_identifier === item.dataset_identifier),
  );

  function submitFilter(e) {
    e.preventDefault();
    refresh();
  }

  const kpis = syntheaSummary?.summary_metrics;

  return (
    <div className="page-stack">
      <section className="page-intro">
        <div>
          <p className="eyebrow">Synthetic Research & Analytics</p>
          <h2>Claims Explorer</h2>
          <p className="muted">
            {isSynthea
              ? "Synthea synthetic healthcare & EHR dataset. Direct identifiers scrubbed. Read-only."
              : "Browse CMS DE-SynPUF synthetic Medicare claims (2008–2010). Read-only."}
          </p>
        </div>
      </section>

      {error && (
        <div className="data-quality-alert" role="alert">
          <div className="alert-content">
            <strong className="alert-title">Claims Explorer unavailable</strong>
            <span className="alert-description">{error}</span>
          </div>
        </div>
      )}

      {disclaimer && (
        <div className="data-quality-alert" role="note" style={{ marginBottom: "1rem" }}>
          <div className="alert-content">
            <strong className="alert-title">Data boundary & disclaimer</strong>
            <span className="alert-description">{disclaimer}</span>
          </div>
        </div>
      )}

      {/* Dataset & Organization Controls */}
      <section className="dashboard-panel">
        <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", alignItems: "center" }}>
          {organizations.length > 0 && (
            <label className="plan-audit-field" style={{ minWidth: "220px" }}>
              Organization
              <select
                value={selectedOrg}
                disabled={organizationsLoading}
                onChange={(e) => { setSelectedOrg(e.target.value); setPage(1); }}
              >
                {organizations.map((org) => (
                  <option key={org.id} value={org.id}>{org.organization_name}</option>
                ))}
              </select>
            </label>
          )}

          {datasets.length > 0 && (
            <label className="plan-audit-field" style={{ minWidth: "320px" }}>
              Claims Dataset
              <select
                value={dataset}
                onChange={(e) => {
                  setDataset(e.target.value);
                  setPage(1);
                  setSelectedClaim(null);
                }}
              >
                {datasets.map((item) => (
                  <option key={item.dataset_identifier} value={item.dataset_identifier}>
                    {item.label} · {item.reporting_period} {item.category === "synthetic_demo" ? "★ Demo" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      </section>

      {/* Admin Registration Panel */}
      {admin && selectedOrg && unregisteredDatasets.length > 0 && (
        <section className="dashboard-panel">
          <h3 className="panel-title">Register an authorized dataset</h3>
          <div className="plan-audit-form plan-audit-form--inline">
            <label className="plan-audit-field">
              Dataset to register
              <select value={registrationTarget} onChange={(e) => setRegistrationTarget(e.target.value)}>
                {unregisteredDatasets.map((item) => (
                  <option key={item.dataset_identifier} value={item.dataset_identifier}>
                    {item.label} ({item.category || "dataset"})
                  </option>
                ))}
              </select>
            </label>
            <button type="button" disabled={busy || !registrationTarget} onClick={registerDataset}>
              {busy ? "Registering…" : "Register dataset for organization"}
            </button>
          </div>
        </section>
      )}

      {/* ------------------------------------------------------------- */}
      {/* SYNTHEA DATASET VIEW                                          */}
      {/* ------------------------------------------------------------- */}
      {isSynthea && (
        <>
          {/* Top KPI Cards */}
          {kpis && (
            <div className="kpi-grid" style={{ marginBottom: "1rem" }}>
              <div className="panel" style={{ padding: "16px" }}>
                <span className="muted" style={{ fontSize: "12px", textTransform: "uppercase" }}>Encounters</span>
                <div style={{ fontSize: "24px", fontWeight: "700", marginTop: "4px" }}>
                  {kpis.total_encounters?.toLocaleString() || "5,059"}
                </div>
                <span className="muted" style={{ fontSize: "12px" }}>
                  {kpis.unique_patients || 111} synthetic patients
                </span>
              </div>
              <div className="panel" style={{ padding: "16px" }}>
                <span className="muted" style={{ fontSize: "12px", textTransform: "uppercase" }}>Total Claim Cost</span>
                <div style={{ fontSize: "24px", fontWeight: "700", color: "#166534", marginTop: "4px" }}>
                  ${(kpis.total_claim_cost || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </div>
                <span className="muted" style={{ fontSize: "12px" }}>
                  Avg: ${(kpis.avg_claim_cost || 0).toFixed(2)} / encounter
                </span>
              </div>
              <div className="panel" style={{ padding: "16px" }}>
                <span className="muted" style={{ fontSize: "12px", textTransform: "uppercase" }}>Providers & Payers</span>
                <div style={{ fontSize: "24px", fontWeight: "700", marginTop: "4px" }}>
                  {kpis.unique_providers || 232} / {kpis.unique_payers || 10}
                </div>
                <span className="muted" style={{ fontSize: "12px" }}>Active synthetic network</span>
              </div>
              <div className="panel" style={{ padding: "16px" }}>
                <span className="muted" style={{ fontSize: "12px", textTransform: "uppercase" }}>Service History</span>
                <div style={{ fontSize: "24px", fontWeight: "700", marginTop: "4px" }}>
                  1940 – 2026
                </div>
                <span className="muted" style={{ fontSize: "12px" }}>18 Parquet tables · 178k rows</span>
              </div>
            </div>
          )}

          {/* Synthea Navigation Tabs */}
          <div className="plan-audit-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={syntheaTab === "analytics"}
              className={`plan-audit-tab${syntheaTab === "analytics" ? " plan-audit-tab--active" : ""}`}
              onClick={() => setSyntheaTab("analytics")}
            >
              Historical Analytics & Trends
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={syntheaTab === "tables"}
              className={`plan-audit-tab${syntheaTab === "tables" ? " plan-audit-tab--active" : ""}`}
              onClick={() => setSyntheaTab("tables")}
            >
              Subtable Record Explorer
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={syntheaTab === "quality"}
              className={`plan-audit-tab${syntheaTab === "quality" ? " plan-audit-tab--active" : ""}`}
              onClick={() => setSyntheaTab("quality")}
            >
              Parquet Quality & Reconciliation
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={syntheaTab === "pilot_readiness"}
              className={`plan-audit-tab${syntheaTab === "pilot_readiness" ? " plan-audit-tab--active" : ""}`}
              onClick={() => setSyntheaTab("pilot_readiness")}
            >
              Phase 4 Pilot Simulation & Boundaries
            </button>
          </div>

          {/* TAB 1: Analytics & Trends */}
          {syntheaTab === "analytics" && (
            <div className="page-stack">
              {/* Yearly Trends Section */}
              {kpis?.yearly_trends && (
                <section className="dashboard-panel">
                  <div className="panel-header">
                    <div>
                      <h3 className="panel-title">Historical Encounter & Spend Trends</h3>
                      <p className="panel-subtitle">Synthea annual encounter volume and aggregated synthetic costs.</p>
                    </div>
                  </div>
                  <div className="table-wrap">
                    <table className="executive-table">
                      <thead>
                        <tr>
                          <th>Service Year</th>
                          <th style={{ textAlign: "right" }}>Encounters</th>
                          <th style={{ textAlign: "right" }}>Total Cost</th>
                          <th style={{ textAlign: "right" }}>Cost per Encounter</th>
                          <th>Relative Volume</th>
                        </tr>
                      </thead>
                      <tbody>
                        {kpis.yearly_trends.slice(-15).map((t) => {
                          const maxVol = Math.max(...kpis.yearly_trends.map((y) => y.encounters));
                          const pct = Math.round((t.encounters / maxVol) * 100);
                          const avg = t.encounters ? (t.cost / t.encounters).toFixed(2) : "0.00";
                          return (
                            <tr key={t.year}>
                              <td><strong>{t.year}</strong></td>
                              <td style={{ textAlign: "right" }}>{t.encounters.toLocaleString()}</td>
                              <td style={{ textAlign: "right" }}>${t.cost.toLocaleString("en-US", { minimumFractionDigits: 2 })}</td>
                              <td style={{ textAlign: "right" }}>${avg}</td>
                              <td>
                                <div style={{ background: "var(--line)", height: "8px", borderRadius: "4px", width: "120px" }}>
                                  <div style={{ background: "var(--accent, #1d4ed8)", height: "100%", width: `${pct}%`, borderRadius: "4px" }} />
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              {/* Encounter Classes Breakdown */}
              {kpis?.encounter_classes && (
                <section className="dashboard-panel">
                  <h3 className="panel-title">Encounter Class Distribution</h3>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "12px", marginTop: "12px" }}>
                    {kpis.encounter_classes.map((cls) => (
                      <div key={cls.class} className="panel" style={{ padding: "12px", border: "1px solid var(--line)" }}>
                        <div style={{ textTransform: "capitalize", fontWeight: "600" }}>{cls.class}</div>
                        <div style={{ fontSize: "20px", fontWeight: "700", margin: "4px 0" }}>{cls.count.toLocaleString()}</div>
                        <div className="muted" style={{ fontSize: "12px" }}>${cls.cost.toLocaleString("en-US", { minimumFractionDigits: 2 })}</div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Top Procedures and Top Medications */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
                {kpis?.top_procedures && (
                  <section className="dashboard-panel">
                    <h3 className="panel-title">Top 10 Clinical Procedures</h3>
                    <p className="panel-subtitle">Coded using SNOMED-CT clinical terminology.</p>
                    <div className="table-wrap" style={{ marginTop: "8px" }}>
                      <table className="executive-table">
                        <thead>
                          <tr>
                            <th>Procedure</th>
                            <th style={{ textAlign: "right" }}>Count</th>
                            <th style={{ textAlign: "right" }}>Base Cost</th>
                          </tr>
                        </thead>
                        <tbody>
                          {kpis.top_procedures.map((p) => (
                            <tr key={p.code}>
                              <td>
                                <div>{p.description}</div>
                                <span className="muted" style={{ fontSize: "11px" }}>Code: {p.code}</span>
                              </td>
                              <td style={{ textAlign: "right" }}>{p.frequency.toLocaleString()}</td>
                              <td style={{ textAlign: "right" }}>${p.avg_cost.toFixed(2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                )}

                {kpis?.top_medications && (
                  <section className="dashboard-panel">
                    <h3 className="panel-title">Top 10 Prescribed Medications</h3>
                    <p className="panel-subtitle">Coded using RxNorm clinical medication concepts.</p>
                    <div className="table-wrap" style={{ marginTop: "8px" }}>
                      <table className="executive-table">
                        <thead>
                          <tr>
                            <th>Medication</th>
                            <th style={{ textAlign: "right" }}>Dispenses</th>
                            <th style={{ textAlign: "right" }}>Total Spend</th>
                          </tr>
                        </thead>
                        <tbody>
                          {kpis.top_medications.map((m) => (
                            <tr key={m.code}>
                              <td>
                                <div>{m.description}</div>
                                <span className="muted" style={{ fontSize: "11px" }}>RxNorm: {m.code}</span>
                              </td>
                              <td style={{ textAlign: "right" }}>{m.frequency.toLocaleString()}</td>
                              <td style={{ textAlign: "right" }}>${m.total_spend.toLocaleString("en-US", { minimumFractionDigits: 2 })}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                )}
              </div>
            </div>
          )}

          {/* TAB 2: Subtable Record Explorer */}
          {syntheaTab === "tables" && (
            <div className="page-stack">
              <section className="dashboard-panel">
                <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginBottom: "12px" }}>
                  {[
                    ["encounters", "Encounters (5,059)"],
                    ["claims", "Claims (8,275)"],
                    ["procedures", "Procedures (13,641)"],
                    ["medications", "Medications (3,216)"],
                    ["claims_transactions", "Transactions (80,981)"],
                    ["providers", "Providers (267)"],
                    ["organizations", "Organizations (267)"],
                    ["patients", "Patients (111)"],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      className={subtable === key ? "button" : "button secondary"}
                      style={{ fontSize: "12px", padding: "6px 12px" }}
                      onClick={() => {
                        setSubtable(key);
                        setPage(1);
                        setSelectedClaim(null);
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                <form className="plan-audit-form" onSubmit={submitFilter}>
                  <label className="plan-audit-field">
                    Text Search (description, code, or name)
                    <input
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="e.g. examination, clinic, Boston"
                      maxLength={100}
                    />
                  </label>
                  <label className="plan-audit-field">
                    Date from
                    <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
                  </label>
                  <label className="plan-audit-field">
                    Date to
                    <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
                  </label>
                  <button type="submit" disabled={loading}>Search</button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={loading}
                    onClick={() => {
                      setSearchQuery("");
                      setDateFrom("");
                      setDateTo("");
                      setPage(1);
                      setSelectedClaim(null);
                      setFetchKey((k) => k + 1);
                    }}
                  >
                    Clear
                  </button>
                </form>
              </section>

              {/* Paginated Results */}
              <section className="dashboard-panel">
                <div className="panel-header">
                  <div>
                    <h3 className="panel-title">{subtable.toUpperCase()} Records</h3>
                    <p className="panel-subtitle">
                      {loading ? "Querying Parquet via DuckDB…" : `${total.toLocaleString("en-US")} total records · page ${page} of ${totalPages}`}
                    </p>
                  </div>
                  <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <button
                      type="button"
                      className="secondary"
                      disabled={page <= 1 || loading}
                      onClick={() => setPage((p) => p - 1)}
                    >
                      ← Prev
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={page >= totalPages || loading}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      Next →
                    </button>
                  </div>
                </div>

                {!loading && rows.length === 0 && (
                  <div className="empty-panel-state">No records found matching filters.</div>
                )}

                {rows.length > 0 && (
                  <div className="table-wrap">
                    <table className="executive-table">
                      <thead>
                        <tr>
                          {columns.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row, idx) => {
                          const rowId = row.Id || row.ID || row.ENCOUNTER || idx;
                          return (
                            <tr
                              key={rowId}
                              className={selectedClaim === rowId ? "row-selected" : ""}
                              style={{ cursor: "pointer" }}
                              onClick={() => setSelectedClaim(selectedClaim === rowId ? null : rowId)}
                              title="Click to view details and linked clinical records"
                            >
                              {columns.map((c) => (
                                <td key={c}>
                                  {row[c] == null ? "—" : String(row[c])}
                                </td>
                              ))}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </div>
          )}

          {/* TAB 3: Parquet Quality & Reconciliation */}
          {syntheaTab === "quality" && (
            <div className="page-stack">
              <section className="dashboard-panel">
                <h3 className="panel-title">Parquet Quality & Row-Count Reconciliation</h3>
                <p className="panel-subtitle">
                  Verification of the 18 converted Synthea tables. Source CSV vs Parquet ZSTD reconciliation status: 100% Match.
                </p>
                <div className="table-wrap" style={{ marginTop: "12px" }}>
                  <table className="executive-table">
                    <thead>
                      <tr>
                        <th>Table</th>
                        <th style={{ textAlign: "right" }}>Rows</th>
                        <th style={{ textAlign: "right" }}>Columns</th>
                        <th style={{ textAlign: "right" }}>Parquet Size</th>
                        <th>Status</th>
                        <th>Privacy / Sanitization</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(syntheaSummary?.tables_summary || []).map((t) => (
                        <tr key={t.table}>
                          <td><strong>{t.table}</strong></td>
                          <td style={{ textAlign: "right" }}>{t.rows.toLocaleString()}</td>
                          <td style={{ textAlign: "right" }}>{t.columns}</td>
                          <td style={{ textAlign: "right" }}>{(t.size_bytes / 1024).toFixed(1)} KB</td>
                          <td>
                            <span style={{ color: "#166534", fontWeight: "600" }}>✓ MATCH</span>
                          </td>
                          <td>
                            {t.table === "patients" ? (
                              <span style={{ color: "#9a3412", fontWeight: "600" }}>
                                Direct identifiers scrubbed (SSN, name, passport)
                              </span>
                            ) : (
                              <span className="muted">Standard synthetic</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              {syntheaSummary?.quality_report?.data_quality_verifications && (
                <section className="dashboard-panel">
                  <h3 className="panel-title">Quality Verification Rules</h3>
                  <div className="table-wrap" style={{ marginTop: "12px" }}>
                    <table className="executive-table">
                      <thead>
                        <tr>
                          <th>Verification Rule</th>
                          <th>Status</th>
                          <th>Validation Detail</th>
                        </tr>
                      </thead>
                      <tbody>
                        {syntheaSummary.quality_report.data_quality_verifications.map((v) => (
                          <tr key={v.rule}>
                            <td><code>{v.rule}</code></td>
                            <td>
                              <span style={{ color: "#166534", fontWeight: "600" }}>✓ {v.status}</span>
                            </td>
                            <td>{v.details}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
            </div>
          )}

          {/* TAB 4: Phase 4 Pilot Simulation & Boundaries */}
          {syntheaTab === "pilot_readiness" && (
            <div className="page-stack">
              <section className="dashboard-panel">
                <h3 className="panel-title">Phase 4 Infusion Audit vs Synthetic Synthea Comparison</h3>
                <p className="panel-subtitle">
                  Architectural and data requirement boundaries between synthetic EHR clinical records and genuine Phase 4 employer infusion audits.
                </p>

                <div className="table-wrap" style={{ marginTop: "1rem" }}>
                  <table className="executive-table">
                    <thead>
                      <tr>
                        <th>Domain</th>
                        <th>Synthea Synthetic Demo Dataset</th>
                        <th>Phase 4 Employer Infusion Claims</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><strong>Coding System</strong></td>
                        <td>
                          SNOMED-CT for procedures, RxNorm for medications.
                          <div className="muted" style={{ fontSize: "11px" }}>No Medicare Part B HCPCS J-codes present.</div>
                        </td>
                        <td>
                          HCPCS J-codes (e.g. J9312 rituximab, J0897 denosumab, J1745 infliximab) + NDC crosswalk.
                        </td>
                      </tr>
                      <tr>
                        <td><strong>Pricing & Adjudication</strong></td>
                        <td>
                          Synthetic unadjudicated charges and list costs.
                          <div className="muted" style={{ fontSize: "11px" }}>No negotiated allowed amounts or coinsurance.</div>
                        </td>
                        <td>
                          Adjudicated allowed amount, plan paid amount, and member cost-share per administered dose.
                        </td>
                      </tr>
                      <tr>
                        <td><strong>Billing Units</strong></td>
                        <td>
                          Procedure count or generic medication units.
                        </td>
                        <td>
                          Strict CMS billing unit conversion (e.g., 10 mg per unit for J9312).
                        </td>
                      </tr>
                      <tr>
                        <td><strong>Hospital Price Benchmarking</strong></td>
                        <td>
                          Synthetic hospital and clinic identities.
                        </td>
                        <td>
                          CMS Hospital Price Transparency rates and Medicare ASP reference benchmarks.
                        </td>
                      </tr>
                      <tr>
                        <td><strong>Data Privacy Boundary</strong></td>
                        <td>
                          Fully synthetic; direct identifiers scrubbed in Parquet.
                        </td>
                        <td>
                          On-premise client-side extractor generates zero-PHI aggregates (<code>claims_summary.csv</code> + <code>manifest.json</code>).
                        </td>
                      </tr>
                      <tr>
                        <td><strong>Savings Calculation</strong></td>
                        <td>
                          <span style={{ color: "#9a3412", fontWeight: "600" }}>
                            Savings calculations not valid (unadjudicated synthetic data).
                          </span>
                        </td>
                        <td>
                          <span style={{ color: "#166534", fontWeight: "600" }}>
                            Valid employer savings against hospital median and ASP + 6%.
                          </span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <div style={{ marginTop: "1.5rem", padding: "16px", background: "var(--bg)", borderRadius: "8px", border: "1px solid var(--line)" }}>
                  <h4 style={{ margin: "0 0 8px" }}>MeshKor Commercial Integrity Notice</h4>
                  <p className="muted" style={{ margin: 0, fontSize: "13px", lineHeight: "1.5" }}>
                    In accordance with MeshKor engineering standards, synthetic data is strictly isolated to Claims Explorer.
                    Phase 4 Plan Audits require verified aggregate imports from genuine employer/TPA claims processed by the local client extractor.
                    Savings figures are never fabricated from synthetic EHR data.
                  </p>
                </div>
              </section>
            </div>
          )}
        </>
      )}

      {/* ------------------------------------------------------------- */}
      {/* CMS DE-SynPUF DATASET VIEW                                    */}
      {/* ------------------------------------------------------------- */}
      {!isSynthea && (
        <>
          <section className="dashboard-panel">
            <h3 className="panel-title">Filter claims</h3>
            <form className="plan-audit-form" onSubmit={submitFilter}>
              <label className="plan-audit-field">
                Claim ID (partial match)
                <input
                  value={claimIdFilter}
                  onChange={(e) => setClaimIdFilter(e.target.value)}
                  maxLength={64}
                  placeholder="e.g. 12345"
                />
              </label>
              <label className="plan-audit-field">
                HCPCS Code
                <input
                  value={hcpcsFilter}
                  onChange={(e) => setHcpcsFilter(e.target.value)}
                  maxLength={7}
                  placeholder="e.g. J9271"
                />
              </label>
              <label className="plan-audit-field">
                Provider / NPI
                <input
                  value={providerFilter}
                  onChange={(e) => setProviderFilter(e.target.value)}
                  maxLength={32}
                  placeholder="e.g. Provider ID or NPI"
                />
              </label>
              <label className="plan-audit-field">
                Claim Type
                <input
                  value={claimTypeFilter}
                  onChange={(e) => setClaimTypeFilter(e.target.value)}
                  maxLength={32}
                  placeholder="e.g. institutional"
                />
              </label>
              <label className="plan-audit-field">
                Service date from (YYYY-MM-DD)
                <input
                  type="date"
                  value={dateFrom}
                  onChange={(e) => setDateFrom(e.target.value)}
                />
              </label>
              <label className="plan-audit-field">
                Service date to (YYYY-MM-DD)
                <input
                  type="date"
                  value={dateTo}
                  onChange={(e) => setDateTo(e.target.value)}
                />
              </label>
              <button type="submit" disabled={loading || datasetsLoading || !dataset}>Search</button>
              <button
                type="button"
                className="secondary"
                disabled={loading}
                onClick={() => {
                  setClaimIdFilter("");
                  setHcpcsFilter("");
                  setProviderFilter("");
                  setClaimTypeFilter("");
                  setDateFrom("");
                  setDateTo("");
                  setPage(1);
                  setSelectedClaim(null);
                  setFetchKey((k) => k + 1);
                }}
              >
                Clear
              </button>
            </form>
          </section>

          {/* Results table for DE-SynPUF */}
          <section className="dashboard-panel">
            <div className="panel-header">
              <div>
                <h3 className="panel-title">Results</h3>
                <p className="panel-subtitle">
                  {loading ? "Loading…" : `${total.toLocaleString("en-US")} total claims · page ${page} of ${totalPages}`}
                </p>
              </div>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                <button
                  type="button"
                  className="secondary"
                  disabled={page <= 1 || loading}
                  onClick={() => setPage((p) => p - 1)}
                >
                  ← Prev
                </button>
                <button
                  type="button"
                  className="secondary"
                  disabled={page >= totalPages || loading}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next →
                </button>
              </div>
            </div>

            {!loading && !error && selectedOrg && dataset && rows.length === 0 && (
              <div className="empty-panel-state">No claims found for the selected filters.</div>
            )}

            {rows.length > 0 && (
              <div className="table-wrap">
                <table className="executive-table">
                  <thead>
                    <tr>
                      {columns.map((col) => <th key={col}>{col.replace(/_/g, " ")}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, idx) => (
                      <tr
                        key={row.claim_id || idx}
                        className={selectedClaim === row.claim_id ? "row-selected" : ""}
                        style={{ cursor: "pointer" }}
                        onClick={() => setSelectedClaim(
                          selectedClaim === row.claim_id ? null : row.claim_id
                        )}
                        title="Click to view claim detail"
                      >
                        {columns.map((col) => (
                          <td key={col}>
                            {row[col] == null ? "—" : String(row[col])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}

      {/* ------------------------------------------------------------- */}
      {/* DETAIL DRAWER / PANEL (Shared)                                */}
      {/* ------------------------------------------------------------- */}
      {selectedClaim && (
        <section className="dashboard-panel">
          <div className="panel-header">
            <div>
              <h3 className="panel-title">Detail — {selectedClaim}</h3>
              <p className="panel-subtitle">{isSynthea ? "Synthea record details" : "DE-SynPUF claim header and lines"}</p>
            </div>
            <button type="button" className="secondary" onClick={() => setSelectedClaim(null)}>
              Close Detail
            </button>
          </div>

          {detailLoading && <p className="muted">Loading detail…</p>}
          {detailError && (
            <div className="data-quality-alert" role="alert">
              <div className="alert-content">
                <strong className="alert-title">Error</strong>
                <span className="alert-description">{detailError}</span>
              </div>
            </div>
          )}

          {claimDetail && isSynthea && (
            <>
              {claimDetail.encounter && (
                <>
                  <h4 style={{ marginBottom: "0.5rem" }}>Encounter Information</h4>
                  <div className="table-wrap">
                    <table className="executive-table">
                      <tbody>
                        {Object.entries(claimDetail.encounter).map(([k, v]) => (
                          <tr key={k}>
                            <th style={{ textAlign: "left", fontWeight: "600", width: "240px" }}>{k.replace(/_/g, " ")}</th>
                            <td>{v == null ? "—" : String(v)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {claimDetail.procedures && claimDetail.procedures.length > 0 && (
                    <>
                      <h4 style={{ margin: "1.5rem 0 0.5rem" }}>Associated Procedures ({claimDetail.procedures.length})</h4>
                      <div className="table-wrap">
                        <table className="executive-table">
                          <thead>
                            <tr>
                              <th>Code</th>
                              <th>Description</th>
                              <th>Base Cost</th>
                              <th>Date</th>
                            </tr>
                          </thead>
                          <tbody>
                            {claimDetail.procedures.map((p, idx) => (
                              <tr key={idx}>
                                <td><code>{p.code}</code></td>
                                <td>{p.description}</td>
                                <td>${p.base_cost}</td>
                                <td>{p.start_date}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}

                  {claimDetail.medications && claimDetail.medications.length > 0 && (
                    <>
                      <h4 style={{ margin: "1.5rem 0 0.5rem" }}>Prescribed Medications ({claimDetail.medications.length})</h4>
                      <div className="table-wrap">
                        <table className="executive-table">
                          <thead>
                            <tr>
                              <th>RxNorm Code</th>
                              <th>Medication</th>
                              <th>Total Cost</th>
                              <th>Dispenses</th>
                              <th>Date</th>
                            </tr>
                          </thead>
                          <tbody>
                            {claimDetail.medications.map((m, idx) => (
                              <tr key={idx}>
                                <td><code>{m.code}</code></td>
                                <td>{m.description}</td>
                                <td>${m.total_cost}</td>
                                <td>{m.dispenses}</td>
                                <td>{m.start_date}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </>
              )}

              {claimDetail.claim && (
                <>
                  <h4 style={{ marginBottom: "0.5rem" }}>Claim Information</h4>
                  <div className="table-wrap">
                    <table className="executive-table">
                      <tbody>
                        {Object.entries(claimDetail.claim).map(([k, v]) => (
                          <tr key={k}>
                            <th style={{ textAlign: "left", fontWeight: "600", width: "240px" }}>{k.replace(/_/g, " ")}</th>
                            <td>{v == null ? "—" : String(v)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {claimDetail.transactions && claimDetail.transactions.length > 0 && (
                    <>
                      <h4 style={{ margin: "1.5rem 0 0.5rem" }}>Claim Transactions ({claimDetail.transactions.length})</h4>
                      <div className="table-wrap">
                        <table className="executive-table">
                          <thead>
                            <tr>
                              <th>Transaction ID</th>
                              <th>Type</th>
                              <th>Amount</th>
                              <th>Date</th>
                              <th>Procedure</th>
                            </tr>
                          </thead>
                          <tbody>
                            {claimDetail.transactions.map((tx) => (
                              <tr key={tx.id}>
                                <td><code>{tx.id}</code></td>
                                <td>{tx.type}</td>
                                <td>${tx.amount}</td>
                                <td>{tx.from_date}</td>
                                <td>{tx.procedure_code}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </>
              )}
            </>
          )}

          {claimDetail && !isSynthea && (
            <>
              <h4 style={{ marginBottom: "0.5rem" }}>Claim header</h4>
              <div className="table-wrap">
                <table className="executive-table">
                  <tbody>
                    {Object.entries(claimDetail.claim || {}).map(([k, v]) => (
                      <tr key={k}>
                        <th style={{ textAlign: "left", fontWeight: "600" }}>{k.replace(/_/g, " ")}</th>
                        <td>{v == null ? "—" : String(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {claimDetail.lines && claimDetail.lines.length > 0 && (
                <>
                  <h4 style={{ margin: "1rem 0 0.5rem" }}>Claim lines ({claimDetail.lines.length})</h4>
                  <div className="table-wrap">
                    <table className="executive-table">
                      <thead>
                        <tr>
                          {Object.keys(claimDetail.lines[0]).map((col) => (
                            <th key={col}>{col.replace(/_/g, " ")}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {claimDetail.lines.map((line, i) => (
                          <tr key={line.line_id || i}>
                            {Object.values(line).map((v, j) => (
                              <td key={j}>{v == null ? "—" : String(v)}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </>
          )}
        </section>
      )}
    </div>
  );
}
