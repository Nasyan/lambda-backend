# analytics/builder.py

from typing import Dict, Any, List, Optional
from engine.ast import parse_ast
from analytics.schemas import ChartConfigPayload

# Импортируем новые доменные исключения
from analytics.exceptions import (
    UnsupportedASTNodeError,
    UnsupportedOperatorError,
    InvalidAggregationConfigError,
)
from logs.decorators import trace_action


class MongoPipelineBuilder:
    """
    Продвинутый компилятор AST-деревьев и конфигураций графиков в нативные пайплайны агрегации MongoDB.
    Обеспечивает строгую изоляцию тенантов (Multi-tenancy), поддержку кросс-табличных вычислений
    и прозрачное динамическое разрешение путей каскадных деревьев (cascading_tree).
    """

    def __init__(
        self,
        instance_uuid: str,
        template_uuid: str,
        schema_definition: Optional[Dict[str, Any]] = None,
    ):
        self.instance_uuid = str(instance_uuid)
        self.template_uuid = str(template_uuid)
        self.schema = schema_definition or {}
        self.lookup_stages: List[Dict[str, Any]] = []
        self._lookup_counter = 0

    def _generate_alias(self, prefix: str) -> str:
        self._lookup_counter += 1
        return f"__compiled_{prefix}_{self._lookup_counter}"

    def _resolve_field_path(self, field_path: str) -> str:
        """
        Преобразует абстрактное имя поля или путь в валидный путь MongoDB Aggregation.
        Автоматически достраивает пути до нужных этажей для типа поля cascading_tree.
        """
        if not field_path:
            return "$data"

        # Сценарий 1: Фронтенд передал готовый dot-path, например "attributes.Материал"
        if "." in field_path:
            parts = field_path.split(".", 1)
            main_field = parts[0]
            if (
                main_field in self.schema
                and self.schema[main_field].get("type") == "cascading_tree"
            ):
                return f"$data.{field_path}"
            return f"$data.{field_path}"

        # Сценарий 2: Передано чистое имя поля (например, "attributes")
        if field_path in self.schema:
            field_meta = self.schema[field_path]
            # Если это каскадное дерево без указания этажа через точку, берём верхний дефолтный этаг
            if field_meta.get("type") == "cascading_tree":
                tree_config = field_meta.get("tree_config", {})
                first_floor = tree_config.get("floor_name")
                if first_floor:
                    return f"$data.{field_path}.{first_floor}"

        return f"$data.{field_path}"

    @trace_action(name="Analytics::Compile_Chart")
    def compile_chart(
        self, config: ChartConfigPayload, ast_filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        pipeline = []

        # 1. Базовый изолированный $match по тенанту и шаблону
        base_match = {
            "instance_uuid": self.instance_uuid,
            "template_uuid": self.template_uuid,
        }
        pipeline.append({"$match": base_match})

        # 2. Компиляция глобального AST-фильтра
        if ast_filter:
            parsed_node = parse_ast(ast_filter)
            compiled_filter = self._compile_node(parsed_node)

            if self.lookup_stages:
                pipeline.extend(self.lookup_stages)
                self.lookup_stages = []

            pipeline.append({"$match": {"$expr": compiled_filter}})

        # =================================================================
        # 2.5 ПОДДЕРЖКА МАССИВОВ (RelationListField)
        # =================================================================
        unwind_field = getattr(config, "unwind_field", None)
        if unwind_field:
            pipeline.append(
                {
                    "$unwind": {
                        "path": self._resolve_field_path(unwind_field),
                        "preserveNullAndEmptyArrays": False,
                    }
                }
            )

        # 3. Настройка группировки (Axis X) с использованием динамического разрешения путей
        x_field = (
            "$_id"
            if config.axis_x.field == "_id"
            else self._resolve_field_path(config.axis_x.field)
        )
        group_id_expr: Any = x_field

        if config.axis_x.type == "datetime" and config.axis_x.date_bucket:
            bucket_formats = {
                "day": "%Y-%m-%d",
                "week": "%Y-%U",
                "month": "%Y-%m",
                "year": "%Y",
            }
            fmt = bucket_formats.get(config.axis_x.date_bucket, "%Y-%m-%d")
            group_id_expr = {
                "$dateToString": {
                    "format": fmt,
                    "date": (
                        {"$dateFromString": {"dateString": x_field}}
                        if config.axis_x.type == "categorical"
                        else x_field
                    ),
                    "onError": x_field,
                    "onNull": "N/A",
                }
            }

        # 4. Настройка метрики (Axis Y)
        y_agg = (
            config.axis_y.aggregation.lower()
            if hasattr(config.axis_y.aggregation, "value")
            else str(config.axis_y.aggregation).lower()
        )

        y_field = (
            "$_id"
            if config.axis_y.field in ["_id", "id"]
            else self._resolve_field_path(config.axis_y.field)
        )

        SUPPORTED_CHART_AGGS = {"count", "sum", "avg", "min", "max"}
        if y_agg not in SUPPORTED_CHART_AGGS:
            raise InvalidAggregationConfigError(
                message=f"Функция агрегации графика '{y_agg}' не поддерживается на оси Y.",
                details={
                    "requested_aggregation": y_agg,
                    "supported": list(SUPPORTED_CHART_AGGS),
                },
            )

        if y_agg == "count":
            accumulator = {"$sum": 1}
        else:
            accumulator = {f"${y_agg}": y_field}

        # 5. Сборка стадии $group
        group_stage = {"$group": {"_id": group_id_expr, "value": accumulator}}
        pipeline.append(group_stage)

        # 6. Финальное форматирование для фронтенда и сортировка по оси X
        pipeline.append(
            {"$project": {"_id": 0, "label": {"$ifNull": ["$_id", "None"]}, "value": 1}}
        )
        pipeline.append({"$sort": {"label": 1}})

        return pipeline

    def _compile_node(self, node: Any) -> Any:
        """
        Рекурсивный обход узлов AST (Реализация паттерна Интерпретатор под MongoDB Expressions).
        """
        if not hasattr(node, "type"):
            raise UnsupportedASTNodeError(
                node_type="UNKNOWN", details={"node_object": str(node)}
            )

        node_type = node.type

        if node_type == "literal":
            return node.value

        elif node_type == "field":
            # 🔥 Динамический резолвинг вместо жесткой строки f"$data.{node.value}"
            return self._resolve_field_path(node.value)

        elif node_type == "input":
            return "$$ROOT"

        elif node_type == "relation_field":
            # Для связей тоже адаптируем резолвинг корневой части пути, если нужно
            resolved_relation = self._resolve_field_path(node.relation_column)
            return f"{resolved_relation}.{node.target_field}"

        elif node_type == "binary_op":
            op_mapping = {
                "add": "$add",
                "subtract": "$subtract",
                "multiply": "$multiply",
                "divide": "$divide",
                "gt": "$gt",
                "lt": "$lt",
                "eq": "$eq",
            }
            mongo_op = op_mapping.get(node.operator)
            if not mongo_op:
                raise UnsupportedOperatorError(
                    operator_name=node.operator, node_type=node_type
                )

            return {
                mongo_op: [
                    self._compile_node(node.left),
                    self._compile_node(node.right),
                ]
            }

        elif node_type == "logical_op":
            if node.operator == "not":
                return {"$not": [self._compile_node(node.left)]}

            op_mapping = {"and": "$and", "or": "$or"}
            mongo_op = op_mapping.get(node.operator)
            if not mongo_op:
                raise UnsupportedOperatorError(
                    operator_name=node.operator, node_type=node_type
                )

            return {
                mongo_op: [
                    self._compile_node(node.left),
                    self._compile_node(node.right),
                ]
            }

        elif node_type == "condition":
            return {
                "$cond": [
                    self._compile_node(node.condition),
                    self._compile_node(node.true_expr),
                    self._compile_node(node.false_expr),
                ]
            }

        elif node_type == "date_op":
            if node.operator == "now":
                return "$$NOW"
            elif node.operator == "add_days":
                return {
                    "$add": [
                        self._compile_node(node.left),
                        {
                            "$multiply": [
                                self._compile_node(node.right),
                                24,
                                60,
                                60,
                                1000,
                            ]
                        },
                    ]
                }
            elif node.operator == "sub_days":
                return {
                    "$subtract": [
                        self._compile_node(node.left),
                        {
                            "$multiply": [
                                self._compile_node(node.right),
                                24,
                                60,
                                60,
                                1000,
                            ]
                        },
                    ]
                }
            elif node.operator == "diff_days":
                return {
                    "$dateDiff": {
                        "startDate": self._compile_node(node.left),
                        "endDate": self._compile_node(node.right),
                        "unit": "day",
                    }
                }
            else:
                raise UnsupportedOperatorError(
                    operator_name=node.operator, node_type=node_type
                )

        elif node_type == "string_op":
            if node.operator == "lower":
                return {"$toLower": self._compile_node(node.left)}
            elif node.operator == "upper":
                return {"$toUpper": self._compile_node(node.left)}
            elif node.operator == "concat":
                return {
                    "$concat": [
                        {"$toString": self._compile_node(node.left)},
                        {"$toString": self._compile_node(node.right)},
                    ]
                }
            elif node.operator == "regex_match":
                return {
                    "$regexMatch": {
                        "input": self._compile_node(node.left),
                        "regex": self._compile_node(node.right),
                        "options": "i",
                    }
                }
            elif node.operator == "regex_extract":
                return {
                    "$regexFind": {
                        "input": self._compile_node(node.left),
                        "regex": self._compile_node(node.right),
                    }
                }
            else:
                raise UnsupportedOperatorError(
                    operator_name=node.operator, node_type=node_type
                )

        elif node_type == "aggregation":
            SUPPORTED_INNER_AGGS = {"count", "sum", "avg", "min", "max"}
            if node.agg_function not in SUPPORTED_INNER_AGGS:
                raise UnsupportedOperatorError(
                    operator_name=node.agg_function, node_type=node_type
                )

            alias = self._generate_alias(f"cross_{node.agg_function}")
            compiled_val = self._compile_node(node.filter_value)

            # 🔥 Безопасно вычисляем пути фильтрации и целевого поля внутри $lookup поддерева
            resolved_filter_field = self._resolve_field_path(node.filter_field).replace(
                "$", ""
            )
            resolved_agg_field = self._resolve_field_path(node.agg_field).replace(
                "$", ""
            )

            match_conditions = [
                {"$eq": ["$instance_uuid", self.instance_uuid]},
                {"$eq": ["$template_uuid", node.target_template_uuid]},
                {"$eq": [f"${resolved_filter_field}", "$$outer_val"]},
            ]

            inner_pipeline: List[Dict[str, Any]] = [
                {"$match": {"$expr": {"$and": match_conditions}}}
            ]

            if node.agg_function != "count":
                inner_pipeline.append(
                    {
                        "$group": {
                            "_id": None,
                            "agg_result": {
                                f"${node.agg_function}": f"${resolved_agg_field}"
                            },
                        }
                    }
                )

            let_val = (
                compiled_val
                if isinstance(compiled_val, str) and compiled_val.startswith("$")
                else compiled_val
            )

            self.lookup_stages.append(
                {
                    "$lookup": {
                        "from": "records",
                        "let": {"outer_val": let_val},
                        "pipeline": inner_pipeline,
                        "as": alias,
                    }
                }
            )

            if node.agg_function == "count":
                return {"$size": f"${alias}"}
            else:
                return {"$ifNull": [{"$arrayElemAt": [f"${alias}.agg_result", 0]}, 0]}

        elif node_type == "array_reduce":
            SUPPORTED_ARRAY_AGGS = {"count", "sum", "avg", "min", "max"}
            if node.agg_function not in SUPPORTED_ARRAY_AGGS:
                raise UnsupportedOperatorError(
                    operator_name=node.agg_function, node_type=node_type
                )

            array_path = self._resolve_field_path(node.array_field)
            current_array = array_path

            if node.filter_expression:
                compiled_filter = self._compile_node(node.filter_expression)
                compiled_filter = self._rewrite_paths_for_scope(
                    compiled_filter, "data.", "$$this."
                )

                current_array = {
                    "$filter": {
                        "input": array_path,
                        "as": "this",
                        "cond": compiled_filter,
                    }
                }

            if node.agg_function == "count":
                return {"$size": current_array}

            compiled_item = self._compile_node(node.item_expression)
            compiled_item = self._rewrite_paths_for_scope(
                compiled_item, "data.", "$$this."
            )

            if node.agg_function == "sum":
                return {
                    "$reduce": {
                        "input": current_array,
                        "initialValue": 0,
                        "in": {"$add": ["$$value", compiled_item]},
                    }
                }
            elif node.agg_function == "avg":
                sum_expr = {
                    "$reduce": {
                        "input": current_array,
                        "initialValue": 0,
                        "in": {"$add": ["$$value", compiled_item]},
                    }
                }
                size_expr = {"$size": current_array}
                return {
                    "$cond": [
                        {"$eq": [size_expr, 0]},
                        0,
                        {"$divide": [sum_expr, size_expr]},
                    ]
                }
            elif node.agg_function in ["min", "max"]:
                op = "$min" if node.agg_function == "min" else "$max"
                mapped_array = {
                    "$map": {"input": current_array, "as": "this", "in": compiled_item}
                }
                return {op: mapped_array}

        raise UnsupportedASTNodeError(node_type=node_type)

    def _rewrite_paths_for_scope(
        self, expr: Any, old_prefix: str, new_prefix: str
    ) -> Any:
        """
        Вспомогательный метод транслятора для подмены контекстных путей переменных в поддеревьях MongoDB
        """
        if isinstance(expr, str):
            if expr.startswith(f"${old_prefix}"):
                return expr.replace(f"${old_prefix}", new_prefix)
            elif expr.startswith(f"$$this.{old_prefix}"):
                return expr.replace(old_prefix, "")
            return expr
        elif isinstance(expr, dict):
            return {
                k: self._rewrite_paths_for_scope(v, old_prefix, new_prefix)
                for k, v in expr.items()
            }
        elif isinstance(expr, list):
            return [
                self._rewrite_paths_for_scope(item, old_prefix, new_prefix)
                for item in expr
            ]
        return expr
