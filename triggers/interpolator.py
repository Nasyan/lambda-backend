# triggers/interpolator.py

import re
from typing import Any, Dict


class ContextInterpolator:
    """Парсит строки/структуры JSON и подставляет значения из контекста записи Mongo."""

    @classmethod
    def interpolate(cls, data_structure: Any, context: Dict[str, Any]) -> Any:
        if isinstance(data_structure, str):
            return cls._replace_string(data_structure, context)
        elif isinstance(data_structure, dict):
            return {k: cls.interpolate(v, context) for k, v in data_structure.items()}
        elif isinstance(data_structure, list):
            return [cls.interpolate(item, context) for item in data_structure]
        return data_structure

    @classmethod
    def _replace_string(cls, text: str, context: Dict[str, Any]) -> Any:
        pattern = r"\{\{\s*([\w\.]+)\s*\}\}"
        cleaned_text = text.strip()

        # Проверяем, состоит ли ВСЯ строка ровно из одного тега, например: "{{ data.price }}"
        # Если да — возвращаем оригинальный тип данных (int, float, list, dict), а не строку.
        match = re.match(r"^\{\{\s*([\w\.]+)\s*\}\}$", cleaned_text)
        if match:
            path = match.group(1)
            return cls._get_value_by_path(path, context)

        # Если это комбинированный текст, например: "Привет, {{ data.name }}!"
        matches = re.findall(pattern, text)
        if not matches:
            return text

        # Заменяем плейсхолдеры. Для строк приводим к str(), а None превращаем в пустую строку.
        for match_iter in re.finditer(pattern, text):
            raw_placeholder = match_iter.group(0)  # Например: "{{ data.field }}"
            path = match_iter.group(1)  # Тот же путь: "data.field"
            val = cls._get_value_by_path(path, context)

            replacement = str(val) if val is not None else ""
            text = text.replace(raw_placeholder, replacement)

        return text

    @classmethod
    def _get_value_by_path(cls, path: str, obj: Dict[str, Any]) -> Any:
        parts = path.split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                data = obj.get("data") if isinstance(obj, dict) else None
                if isinstance(data, dict) and parts[0] != "data":
                    return cls._get_value_by_path(path, data)
                return None
        return current
