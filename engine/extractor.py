# engine/extractor.py

from typing import List, Set

from engine.ast import (
    ASTNode,
    RelationFieldNode,
    BinaryOpNode,
    LogicalOpNode,
    ConditionNode,
    DateOpNode,
    StringOpNode,
    AggregationNode,
    ArrayReduceNode,
)


class RelationExtractor:
    """Сервис для статического анализа AST и извлечения зависимостей (связей)."""

    @classmethod
    def extract(cls, node: ASTNode) -> List[str]:
        """Публичный метод для извлечения всех relation-полей из дерева."""
        out_set = set()
        cls.traverse(node, out_set)
        return list(out_set)

    @classmethod
    def traverse(cls, node: ASTNode, out_set: Set[str]) -> None:
        """Внутренний рекурсивный обход с использованием единого set() для производительности."""
        if not node:
            return

        handler_name = f"_rel_{node.type}"
        handler = getattr(cls, handler_name, None)
        if handler:
            handler(node, out_set)

    @classmethod
    def _rel_relation_field(cls, node: RelationFieldNode, out_set: Set[str]):
        out_set.add(node.relation_column)

    @classmethod
    def _rel_binary_op(cls, node: BinaryOpNode, out_set: Set[str]):
        cls.traverse(node.left, out_set)
        cls.traverse(node.right, out_set)

    @classmethod
    def _rel_logical_op(cls, node: LogicalOpNode, out_set: Set[str]):
        cls.traverse(node.left, out_set)
        if node.right:
            cls.traverse(node.right, out_set)

    @classmethod
    def _rel_condition(cls, node: ConditionNode, out_set: Set[str]):
        cls.traverse(node.condition, out_set)
        cls.traverse(node.true_expr, out_set)
        cls.traverse(node.false_expr, out_set)

    @classmethod
    def _rel_date_op(cls, node: DateOpNode, out_set: Set[str]):
        if node.left:
            cls.traverse(node.left, out_set)
        if node.right:
            cls.traverse(node.right, out_set)

    @classmethod
    def _rel_string_op(cls, node: StringOpNode, out_set: Set[str]):
        if node.left:
            cls.traverse(node.left, out_set)
        if node.right:
            cls.traverse(node.right, out_set)

    @classmethod
    def _rel_aggregation(cls, node: AggregationNode, out_set: Set[str]):
        cls.traverse(node.filter_value, out_set)

    @classmethod
    def _rel_array_reduce(cls, node: ArrayReduceNode, out_set: Set[str]):
        if node.item_expression:
            cls.traverse(node.item_expression, out_set)
        if node.filter_expression:
            cls.traverse(node.filter_expression, out_set)
