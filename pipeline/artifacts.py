"""Canonical names for generated analytical artifacts.

Keep producers, API readers, lineage checks, and backups on one filename map.
Aliases are read-only compatibility names for older local snapshots.
"""

REFERENCE_ARTIFACTS = {
    "asp": "ref_asp.parquet",
    "crosswalk": "ref_crosswalk.parquet",
    "ndc_products": "ref_ndc_products.parquet",
    "ndc_packages": "ref_ndc_packages.parquet",
    "hospitals": "ref_hospitals.parquet",
    "nadac": "ref_nadac.parquet",
    "providers": "ref_providers.parquet",
    "enrollments": "ref_enrollments.parquet",
    "drug_scope": "drug_scope.parquet",
}

LEGACY_REFERENCE_ARTIFACTS = {
    "crosswalk": ("ref_ndc_hcpcs.parquet",),
    "ndc_products": ("ref_ndc_product.parquet",),
    "ndc_packages": ("ref_ndc_package.parquet",),
}

NORMALIZED_ARTIFACTS = {
    "hospital_rates": "hpt_drug_rates.parquet",
    "hospital_quality": "hpt_file_quality.parquet",
}

MART_ARTIFACTS = {"comparison": "comparison.parquet", "run_info": "run_info.json"}
