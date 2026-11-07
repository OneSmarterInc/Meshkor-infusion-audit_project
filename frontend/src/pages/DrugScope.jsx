import { useApi, fmt } from "../api.js";
import { Loading, Table } from "../components.jsx";

export default function DrugScope({ user }) {
  const isAdmin = user?.roles?.some((role) => ["SUPER_ADMIN", "Admin"].includes(role));
  const state = useApi("drug-selection-scope");
  const columns = [
    { key: "spend_rank", label: "Spend rank", num: true }, { key: "hcpcs", label: "HCPCS" },
    { key: "name", label: "Drug" }, { key: "partb_spending", label: "Part B spending", num: true, render: (r) => r.partb_spending == null ? "—" : fmt.usd(r.partb_spending) },
    { key: "asp_dosage", label: "CMS ASP dosage" },
  ];
  if (isAdmin) columns.push({ key: "unit_review", label: "Unit review", render: (r) => r.unit_review?.reviewer__username || r.reviewed_by || r.unit_review?.status || "Pending" });
  return <>
    <p className="lede">Selected drugs ranked by annual Medicare Part B spending.</p>
    <Loading state={state}>{(scope) => <>
      {scope.status === "blocked" && <div className="banner"><b>Scope not available.</b> {scope.reason}</div>}
      <section><div className="stats">
        <div className="stat"><div className="stat-label">Selected drugs</div><div className="stat-value">{fmt.n(scope.selected_count)} / {fmt.n(scope.target_count)}</div></div>
        <div className="stat"><div className="stat-label">Spend year</div><div className="stat-value">{scope.spend_year || "—"}</div></div>
        <div className="stat"><div className="stat-label">Source</div><div className="stat-value">{scope.source || "Missing"}</div><div className="stat-hint">{scope.source_file ? scope.source_file.split(/[\\/]/).pop() : "CMS spending reference"}</div></div>
      </div></section>
      <section><h2>Selected HCPCS codes</h2><Table rows={scope.drugs || []} columns={columns} empty="No CMS spend-ranked scope is available." /></section>
    </>}</Loading>
  </>;
}
