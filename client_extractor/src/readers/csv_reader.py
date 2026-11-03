import csv

from ..validation.claims_validator import OPTIONAL_FIELDS, REQUIRED_FIELDS, normalize_header

MAX_FIELD_SIZE = 1_000_000


def read_csv_claims(path, column_mapping=None):
    csv.field_size_limit(MAX_FIELD_SIZE)
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("The claims CSV has no header row.")
        normalized_headers = [normalize_header(name) for name in reader.fieldnames]
        if any(not name for name in normalized_headers) or len(normalized_headers) != len(set(normalized_headers)):
            raise ValueError("The claims CSV has blank or duplicate column names.")
        _validate_source_headers(normalized_headers, column_mapping)
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                row["__extra_columns__"] = True
                row.pop(None, None)
            yield line_number, row


def _validate_source_headers(headers, column_mapping):
    mapping = column_mapping or {}
    source_fields = {normalize_header(mapping.get(field, field)) for field in REQUIRED_FIELDS}
    source_fields.update(
        normalize_header(mapping[field]) for field in OPTIONAL_FIELDS if field in mapping
    )
    missing = source_fields - set(headers)
    if missing:
        raise ValueError("The claims file is missing required mapped columns.")
