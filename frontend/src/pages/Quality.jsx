import { fmt, useApi } from "../api.js";
import { Loading, Table } from "../components.jsx";

export default function Quality() {
  const q = useApi("hospital-file-quality");
  return (
    <section>
      <p className="lede">Review parsed hospital file coverage and errors.</p>
      <Loading state={q}>
        {(rows) => (
          <Table rows={rows} columns={[
            { key: "system", label: "System" },
            { key: "hospital_name", label: "Hospital" },
            { key: "format", label: "Format" },
            { key: "cms_version", label: "CMS ver." },
            { key: "file_last_updated", label: "File date" },
            { key: "items_scanned", label: "Items scanned", num: true, render: (r) => fmt.n(r.items_scanned) },
            { key: "drug_rows", label: "Drug rows", num: true, render: (r) => fmt.n(r.drug_rows) },
            { key: "hcpcs_found", label: "Codes found" },
            { key: "error", label: "Error", render: (r) => r.error
              ? <span className="pill s-unit_unresolved">{r.error}</span> : "—" },
          ]} />
        )}
      </Loading>
    </section>
  );
}
