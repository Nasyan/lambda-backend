# engine/ast.py

from typing import Literal, Union, Annotated, Optional, Dict, List
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator
from engine.exceptions.evaluator import FormulaValidationError


class LiteralNode(BaseModel):
    type: Literal["literal"] = "literal"
    value: Union[int, float, str, bool, None]


class FieldNode(BaseModel):
    type: Literal["field"] = "field"
    value: str


class InputNode(BaseModel):
    type: Literal["input"] = "input"


class RelationFieldNode(BaseModel):
    type: Literal["relation_field"] = "relation_field"
    relation_column: str
    target_field: str
    # 🔥 Новый параметр: по умолчанию ищем по системному _id, но фронтенд может переопределить на 'data.qr_code'
    lookup_field: Optional[str] = "_id"


class BinaryOpNode(BaseModel):
    type: Literal["binary_op"] = "binary_op"
    operator: Literal["add", "subtract", "multiply", "divide", "gt", "lt", "eq"]
    left: "ASTNode"
    right: "ASTNode"


class LogicalOpNode(BaseModel):
    """Логические операторы (AND / OR / NOT)."""

    type: Literal["logical_op"] = "logical_op"
    operator: Literal["and", "or", "not"]
    left: "ASTNode"
    right: Optional["ASTNode"] = None

    @model_validator(mode="after")
    def check_not_operator(self) -> "LogicalOpNode":
        if self.operator != "not" and self.right is None:
            raise ValueError(f"Operator '{self.operator}' requires 'right' operand")
        return self


class ConditionNode(BaseModel):
    """Ветвление IF-THEN-ELSE (Ternary)."""

    type: Literal["condition"] = "condition"
    condition: "ASTNode"
    true_expr: "ASTNode"
    false_expr: "ASTNode"


class DateOpNode(BaseModel):
    """Работа с датами (SLA, дедлайны)."""

    type: Literal["date_op"] = "date_op"
    operator: Literal["now", "add_days", "sub_days", "diff_days"]
    left: Optional["ASTNode"] = None
    right: Optional["ASTNode"] = None


class StringOpNode(BaseModel):
    """Строковые манипуляции и регулярные выражения."""

    type: Literal["string_op"] = "string_op"
    operator: Literal["lower", "upper", "regex_match", "regex_extract", "concat"]
    left: "ASTNode"
    right: Optional["ASTNode"] = None


class AggregationNode(BaseModel):
    @model_validator(mode="after")
    def check_agg_field(self) -> "AggregationNode":
        if self.agg_function != "count" and not self.agg_field:
            raise ValueError(f"Function '{self.agg_function}' requires an agg_field")
        return self

    type: Literal["aggregation"] = "aggregation"
    target_template_uuid: str
    filter_field: str
    filter_value: "ASTNode"
    agg_function: Literal["sum", "count", "avg", "min", "max"]
    agg_field: Optional[str] = None


class ArrayReduceNode(BaseModel):
    @model_validator(mode="after")
    def check_item_expr(self) -> "ArrayReduceNode":
        if self.agg_function != "count" and not self.item_expression:
            raise ValueError(
                f"Function '{self.agg_function}' requires an item_expression"
            )
        return self

    type: Literal["array_reduce"] = "array_reduce"
    array_field: str
    agg_function: Literal["sum", "count", "avg", "min", "max"]
    item_expression: Optional["ASTNode"] = None
    filter_expression: Optional["ASTNode"] = None


class ObjectNode(BaseModel):
    type: Literal["object"] = "object"
    fields: Dict[str, "ASTNode"]


class QueryFilter(BaseModel):
    field: str
    operator: Literal["eq", "ne", "gt", "lt", "gte", "lte", "contains"] = "eq"
    value: "ASTNode"


class QueryNode(BaseModel):
    type: Literal["query"] = "query"
    target_template_uuid: str
    filters: List[QueryFilter] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)
    return_fields: Optional[List[str]] = None


# Объединяем все узлы
ASTNode = Annotated[
    Union[
        LiteralNode,
        FieldNode,
        InputNode,
        RelationFieldNode,
        BinaryOpNode,
        LogicalOpNode,
        ConditionNode,
        DateOpNode,
        StringOpNode,
        AggregationNode,
        ArrayReduceNode,
        ObjectNode,
        QueryNode,
    ],
    Field(discriminator="type"),
]

# Разрешаем рекурсивные ссылки
BinaryOpNode.model_rebuild()
LogicalOpNode.model_rebuild()
ConditionNode.model_rebuild()
DateOpNode.model_rebuild()
StringOpNode.model_rebuild()
AggregationNode.model_rebuild()
ArrayReduceNode.model_rebuild()
ObjectNode.model_rebuild()
QueryFilter.model_rebuild()
QueryNode.model_rebuild()

ASTAdapter = TypeAdapter(ASTNode)


def parse_ast(raw_json: dict) -> ASTNode:
    try:
        return ASTAdapter.validate_python(raw_json)
    except ValidationError as e:
        raise FormulaValidationError(f"Invalid formula structure: {e}")
