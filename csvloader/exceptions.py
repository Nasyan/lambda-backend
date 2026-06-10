from typing import Any, Dict, List


class CSVImportValidationError(Exception):
    """CSV не прошёл построчную валидацию перед импортом.

    errors: [{"row": <1-based номер строки данных>, "field": str|None, "detail": str}]
    """

    def __init__(self, errors: List[Dict[str, Any]]):
        self.errors = errors
        super().__init__(f"CSV import validation failed: {len(errors)} error(s)")
