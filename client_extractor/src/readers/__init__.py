from .csv_reader import read_csv_claims
from .excel_reader import read_excel_claims


def read_claims(path, column_mapping=None):
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        return read_csv_claims(path, column_mapping)
    if suffix in {".xlsx", ".xlsm"}:
        return read_excel_claims(path, column_mapping)
    if suffix == ".837":
        from .edi_837_reader import read_edi_837
        return read_edi_837(path)
    raise ValueError("Supported input files are CSV, XLSX, and XLSM. 837 parsing is not enabled yet.")
