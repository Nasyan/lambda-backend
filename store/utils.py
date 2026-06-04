# store/utils.py

from fastapi import Request
from typing import Dict, Any, List, Optional


def parse_query_filters(
    request: Request, exclude_keys: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Извлекает query-параметры из запроса и фильтрует их,
    исключая служебные ключи пагинации и сортировки.
    """
    if exclude_keys is None:
        exclude_keys = ["limit", "offset", "sort_by", "sort_descending"]

    query_params = dict(request.query_params)
    return {k: v for k, v in query_params.items() if k not in exclude_keys}
