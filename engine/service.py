# engine/service.py

from typing import Any, Dict, Callable, Awaitable
from .ast import parse_ast
from .evaluator import FormulaEvaluator

from engine.extractor import RelationExtractor
from engine.exceptions.evaluator import FormulaValidationError, FormulaEvaluationError


class FormulaService:
    """Сервис для интеграции движка формул с бизнес-логикой CRM."""

    @staticmethod
    async def process_record_formulas(
        template_schema: Dict[str, Any],
        record_data: Dict[str, Any],
        record_resolver: Callable[[str], Awaitable[Dict[str, Any]]] = None,
        aggregation_resolver: Callable[
            [str, str, Any, str, str], Awaitable[Any]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Обработка формул с защитой от N+1 и корректной распаковкой relation_list.
        """
        updated_data = record_data.copy()

        inner_context = (
            updated_data.get("data", updated_data)
            if isinstance(updated_data, dict)
            else updated_data
        )

        parsed_formulas = {}
        all_required_relations = []

        for column_name, meta in template_schema.items():
            if meta.get("type") == "formula":
                raw_ast = meta.get("ast")
                if not raw_ast:
                    continue

                try:
                    ast_tree = parse_ast(raw_ast)
                    parsed_formulas[column_name] = ast_tree

                    relations = RelationExtractor.extract(ast_tree)
                    all_required_relations.extend(relations)
                except FormulaValidationError:
                    if "data" in updated_data and isinstance(
                        updated_data["data"], dict
                    ):
                        updated_data["data"][column_name] = None
                    else:
                        updated_data[column_name] = None

        all_required_relations = list(set(all_required_relations))

        if (
            all_required_relations
            and record_resolver
            and hasattr(record_resolver, "prefetch")
        ):
            ids_to_fetch = set()
            for col in all_required_relations:
                target_val = inner_context.get(col)
                if not target_val:
                    continue

                # 🔥 Бронебойная распаковка: списки, объекты денормализации и строки
                if isinstance(target_val, list):
                    for item in target_val:
                        if isinstance(item, dict) and "_id" in item:
                            ids_to_fetch.add(str(item["_id"]))
                        elif isinstance(item, str):
                            ids_to_fetch.add(item)
                elif isinstance(target_val, dict) and "_id" in target_val:
                    ids_to_fetch.add(str(target_val["_id"]))
                elif isinstance(target_val, str):
                    ids_to_fetch.add(target_val)

            if ids_to_fetch:
                await record_resolver.prefetch(list(ids_to_fetch))

        for column_name, ast_tree in parsed_formulas.items():
            try:
                result = await FormulaEvaluator.evaluate(
                    node=ast_tree,
                    context=updated_data,
                    record_resolver=record_resolver,
                    aggregation_resolver=aggregation_resolver,
                )

                if "data" in updated_data and isinstance(updated_data["data"], dict):
                    updated_data["data"][column_name] = result
                else:
                    updated_data[column_name] = result

            except FormulaEvaluationError:
                if "data" in updated_data and isinstance(updated_data["data"], dict):
                    updated_data["data"][column_name] = None
                else:
                    updated_data[column_name] = None

        return updated_data
