import zipfile

from openpyxl import load_workbook

from ..validation.claims_validator import OPTIONAL_FIELDS, REQUIRED_FIELDS, normalize_header

MAX_WORKBOOK_MEMBERS = 10_000
MAX_WORKBOOK_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500


def read_excel_claims(path, column_mapping=None):
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_WORKBOOK_MEMBERS:
                raise ValueError("The claims workbook contains too many internal files.")
            uncompressed_size = sum(member.file_size for member in members)
            if uncompressed_size > MAX_WORKBOOK_UNCOMPRESSED_BYTES:
                raise ValueError("The claims workbook exceeds the uncompressed size limit.")
            if any(member.compress_size == 0 and member.file_size > 0
                   or member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO
                   for member in members):
                raise ValueError("The claims workbook has an unsafe compression ratio.")
    except zipfile.BadZipFile as exc:
        raise ValueError("The claims workbook is not a valid XLSX/XLSM file.") from exc
    workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows, None)
        if not headers or not any(value is not None for value in headers):
            raise ValueError("The claims workbook has no header row.")
        names = [str(value).strip() if value is not None else "" for value in headers]
        normalized_headers = [normalize_header(name) for name in names]
        if any(not name for name in normalized_headers) or len(normalized_headers) != len(set(normalized_headers)):
            raise ValueError("The claims workbook has blank or duplicate column names.")
        mapping = column_mapping or {}
        source_fields = {normalize_header(mapping.get(field, field)) for field in REQUIRED_FIELDS}
        source_fields.update(
            normalize_header(mapping[field]) for field in OPTIONAL_FIELDS if field in mapping
        )
        if source_fields - set(normalized_headers):
            raise ValueError("The claims workbook is missing required mapped columns.")
        for line_number, values in enumerate(rows, start=2):
            yield line_number, {name: values[index] for index, name in enumerate(names)}
    finally:
        workbook.close()
