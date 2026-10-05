"""Produce source-only test inputs without compiling or importing proposed code."""

import base64

from ai_quant_os.candidate_test_contracts import CandidateTestFile, CandidateTestRequest
from ai_quant_os.validation_package_contracts import ValidationPackage


def candidate_test_request(package: ValidationPackage) -> CandidateTestRequest:
    package = ValidationPackage.model_validate_json(package.model_dump_json())
    files = [
        CandidateTestFile(
            path=row.path,
            raw_base64=base64.b64encode(row.text.encode()).decode(),
            sha256=row.sha256,
        )
        for row in package.candidate.files
    ]
    files.extend(CandidateTestFile(**row.model_dump()) for row in package.sdk_files)
    return CandidateTestRequest(
        package_ref=package.package_ref,
        candidate_ref=package.candidate.candidate_ref,
        files=tuple(sorted(files, key=lambda row: row.path)),
    )
