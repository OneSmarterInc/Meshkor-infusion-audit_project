import { useApi, fmt } from "../api.js";
import { isInternalColumn, Loading, Table } from "../components.jsx";

export default function AuditLogs() {
  const state = useApi("audit-logs", { limit: 100 });
  return <>
    <Loading state={state}>{(rows) => <section><h2>Recent events</h2><Table rows={rows} columns={[
      { key: "created_at", label: "Time", render: (r) => new Date(r.created_at).toLocaleString() },
      { key: "action", label: "Action" }, { key: "actor", label: "User" },
      { key: "entity_type", label: "Record type" },
      { key: "entity_id", label: "Record ID", render: (r) => r.entity_type === "api_resource" ? "—" : r.entity_id || "—" },
      { key: "details", label: "Details", render: (r) => {
        const details = Object.entries(r.details || {}).filter(([key]) => !isInternalColumn(key));
        return details.length ? details.map(([key, value]) =>
          `${key.replaceAll("_", " ")}: ${typeof value === "object" ? JSON.stringify(value) : value}`
        ).join("; ") : "—";
      } },
    ]} empty="No audit events have been recorded." /></section>}</Loading>
  </>;
}
