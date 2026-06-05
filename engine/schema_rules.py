# engine/schema_rules.py

"""Чистые (in-memory) проверки no-code схемы.

NoCodeSchemaValidator — первая половина бывшего SchemaIntegrityValidator
(task3, ГЗ-1 Блок A): работает строго в оперативной памяти, без обращений
к Postgres или MongoDB. Инфраструктурные проверки каскадных связей живут
в core/services/template_integrity.py (TemplateIntegrityService).
"""

from typing import Dict, Any, Set, List

from mongo.tools.validators import validate_schema_definition

from engine.exceptions.integrity import (
    CircularDependencyError,
    SchemaValidationError,
)
from logs.decorators import trace_action


class NoCodeSchemaValidator:
    """Валидатор структуры no-code схемы: циклы формул, поля AST, маски витрины.

    Ни один метод класса не имеет права обращаться к базе данных —
    это контракт чистого валидатора (View -> Service -> Validator).
    """

    @classmethod
    def validate_definition(cls, schema: Dict[str, Any]) -> None:
        """Проверяет синтаксическую корректность определения колонок шаблона.

        Делегирует в канонический validate_schema_definition (стратегии типов,
        зарезервированные имена, ui_widget, embedded-триггеры) — проверка
        выполняется полностью в памяти.
        """
        validate_schema_definition(schema)

    @classmethod
    def extract_used_fields(cls, ast_node: Dict[str, Any]) -> Set[str]:
        if not ast_node or not isinstance(ast_node, dict):
            return set()

        node_type = ast_node.get("type")
        used_fields = set()

        if node_type == "field":
            used_fields.add(ast_node.get("value"))
        elif node_type == "relation_field":
            used_fields.add(ast_node.get("relation_column"))
        elif node_type == "aggregation":
            if isinstance(ast_node.get("filter_value"), dict):
                used_fields.update(
                    cls.extract_used_fields(ast_node.get("filter_value"))
                )
        elif node_type in ("binary_op", "logical_op", "string_op", "date_op"):
            used_fields.update(cls.extract_used_fields(ast_node.get("left", {})))
            used_fields.update(cls.extract_used_fields(ast_node.get("right", {})))
        elif node_type == "condition":
            used_fields.update(cls.extract_used_fields(ast_node.get("condition", {})))
            used_fields.update(cls.extract_used_fields(ast_node.get("true_expr", {})))
            used_fields.update(cls.extract_used_fields(ast_node.get("false_expr", {})))
        elif node_type == "array_reduce":
            used_fields.add(ast_node.get("array_field"))
            used_fields.update(
                cls.extract_used_fields(ast_node.get("item_expression", {}))
            )
            used_fields.update(
                cls.extract_used_fields(ast_node.get("filter_expression", {}))
            )
        elif node_type == "object":
            for child in ast_node.get("fields", {}).values():
                used_fields.update(cls.extract_used_fields(child))
        elif node_type == "query":
            for item in ast_node.get("filters", []):
                if isinstance(item.get("value"), dict):
                    used_fields.update(cls.extract_used_fields(item.get("value")))

        return used_fields

    @classmethod
    @trace_action(name="Integrity::Check_Circular_Deps")
    def check_circular_dependencies(cls, schema: Dict[str, Any]) -> None:
        graph: Dict[str, Set[str]] = {}
        for column_name, meta in schema.items():
            if meta.get("type") == "formula":
                ast = meta.get("ast", {})
                raw_used = cls.extract_used_fields(ast)
                graph[column_name] = {f.split(".")[0] for f in raw_used if f}
            else:
                graph[column_name] = set()

        visited = set()
        rec_stack = set()

        def dfs(node: str) -> bool:
            if node in rec_stack:
                return True
            if node in visited:
                return False

            visited.add(node)
            rec_stack.add(node)

            for neighbor in graph.get(node, []):
                if dfs(neighbor):
                    return True

            rec_stack.remove(node)
            return False

        for node in graph:
            if dfs(node):
                raise CircularDependencyError(
                    f"Обнаружена циклическая зависимость: поле '{node}' зациклено в цепочке вычислений формул."
                )

    @classmethod
    def validate_storefront_policy(
        cls, schema: Dict[str, Any], policy_data: Dict[str, Any]
    ) -> None:
        valid_columns = set(schema.keys())

        def _is_valid_col(col: str) -> bool:
            base_col = col.split(".")[0]
            return base_col in valid_columns

        read_mask: List[str] = policy_data.get("read_mask", [])
        write_mask: List[str] = policy_data.get("write_mask", [])
        read_filters: Dict[str, Any] = policy_data.get("read_filters", {})

        invalid_read = [col for col in read_mask if not _is_valid_col(col)]
        if invalid_read:
            raise SchemaValidationError(
                reason="Несуществующие поля в маске чтения витрины",
                invalid_fields=invalid_read,
                target_context="read_mask",
            )

        invalid_write = [col for col in write_mask if not _is_valid_col(col)]
        if invalid_write:
            raise SchemaValidationError(
                reason="Несуществующие поля в маске записи витрины",
                invalid_fields=invalid_write,
                target_context="write_mask",
            )

        invalid_filters = [col for col in read_filters.keys() if not _is_valid_col(col)]
        if invalid_filters:
            raise SchemaValidationError(
                reason="Несуществующие поля в серверных фильтрах витрины",
                invalid_fields=invalid_filters,
                target_context="read_filters",
            )
