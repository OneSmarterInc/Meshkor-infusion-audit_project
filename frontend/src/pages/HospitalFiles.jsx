import { useApi, fmt } from "../api.js";
import { Loading, Table } from "../components.jsx";

export default function HospitalFiles() {
  const catalog = useApi("reference-data-sets/hospitals");
  return <>
    <p className="lede">Hospitals listed in the CMS reference data.</p>
    <Loading state={catalog}>{(hospitals) => <section><p>{fmt.n(hospitals.total)} hospital records.</p><Table rows={hospitals.rows || []} columns={[
      { key: "ccn", label: "CCN" }, { key: "hospital_name", label: "Hospital" },
      { key: "city", label: "City" }, { key: "county", label: "County" },
      { key: "hospital_type", label: "CMS type" }, { key: "npi", label: "NPI", render: (r) => r.npi || "Pending NPPES/enrollment match" },
    ]} empty="No hospital catalog rows." /></section>}</Loading>
  </>;
}
