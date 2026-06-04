# engine/tests/test_evaluator.py

import pytest
from engine.ast import parse_ast
from engine.evaluator import FormulaEvaluator
from engine.exceptions.evaluator import FormulaValidationError


@pytest.mark.asyncio
async def test_complex_math_order_of_operations():
    """
    Тест 1: Глубокая вложенность.
    Формула: ((price + tax) * discount_multiplier) / 2
    Если price=100, tax=20, discount=0.5 -> (120 * 0.5) / 2 = 30.0
    """
    raw_ast = {
        "type": "binary_op",
        "operator": "divide",
        "left": {
            "type": "binary_op",
            "operator": "multiply",
            "left": {
                "type": "binary_op",
                "operator": "add",
                "left": {"type": "field", "value": "price"},
                "right": {"type": "field", "value": "tax"},
            },
            "right": {"type": "field", "value": "discount_multiplier"},
        },
        "right": {"type": "literal", "value": 2},
    }

    ast_tree = parse_ast(raw_ast)
    context = {"price": 100, "tax": 20, "discount_multiplier": 0.5}

    result = await FormulaEvaluator.evaluate(ast_tree, context)
    assert result == 30.0


@pytest.mark.asyncio
async def test_safe_division_by_zero():
    """
    Тест 2: Защита от деления на ноль.
    Формула: total / count. При count = 0 ожидаем None, а не падение приложения.
    """
    raw_ast = {
        "type": "binary_op",
        "operator": "divide",
        "left": {"type": "field", "value": "total"},
        "right": {"type": "field", "value": "count"},
    }

    ast_tree = parse_ast(raw_ast)
    context = {"total": 1000, "count": 0}

    result = await FormulaEvaluator.evaluate(ast_tree, context)
    assert result is None  # Движок должен мягко вернуть None


@pytest.mark.asyncio
async def test_missing_field_fallback_to_zero():
    """
    Тест 3: Отсутствующее поле.
    Пользователь добавил формулу "price - discount", но в старых записях нет поля "discount".
    Движок должен подставить 0.
    """
    raw_ast = {
        "type": "binary_op",
        "operator": "subtract",
        "left": {"type": "field", "value": "price"},
        "right": {"type": "field", "value": "discount"},
    }

    ast_tree = parse_ast(raw_ast)
    context = {"price": 5000}

    result = await FormulaEvaluator.evaluate(ast_tree, context)
    assert result == 5000  # 5000 - 0 = 5000


# @pytest.mark.asyncio
# async def test_type_mismatch_raises_evaluation_error():
#     raw_ast = {
#         "type": "binary_op",
#         "operator": "subtract",
#         "left": {"type": "field", "value": "status"},
#         "right": {"type": "literal", "value": 10},
#     }

#     ast_tree = parse_ast(raw_ast)
#     context = {"status": "draft"}

#     # Проверяем и тип ошибки, и текст сообщения одним блоком
#     with pytest.raises(FormulaTypeMismatchError) as exc_info:
#         await FormulaEvaluator.evaluate(ast_tree, context)

#     # Проверяем, что текст ошибки содержит нужную фразу
#     assert "requires numeric operands" in str(exc_info.value)


@pytest.mark.asyncio
async def test_logical_operators_for_triggers():
    """
    Тест 5: Логические операторы (Подготовка к Кейсу 3 - Автоматизации).
    Формула: days_passed > 3
    """
    raw_ast = {
        "type": "binary_op",
        "operator": "gt",
        "left": {"type": "field", "value": "days_passed"},
        "right": {"type": "literal", "value": 3},
    }

    ast_tree = parse_ast(raw_ast)

    # Сценарий А: Условие не выполнено
    result_false = await FormulaEvaluator.evaluate(ast_tree, {"days_passed": 2})
    assert result_false is False

    # Сценарий Б: Условие выполнено
    result_true = await FormulaEvaluator.evaluate(ast_tree, {"days_passed": 5})
    assert result_true is True


def test_invalid_ast_structure():
    raw_ast = {
        "type": "binary_op",
        "operator": "UNKNOWN_OPERATOR",
        "left": {"type": "field", "value": "price"},
        "right": {"type": "literal", "value": 1},
    }

    # Pydantic при ошибке литерала "operator" выдаст сообщение,
    # содержащее 'operator' и 'input value is not a valid enumeration member'
    with pytest.raises(FormulaValidationError):
        parse_ast(raw_ast)


# === Регрессия cost=0: array_reduce(sum) поверх relation_field ===
# До фикта этот сценарий был полностью непокрыт тестами и молча схлопывался в 0.


@pytest.mark.asyncio
async def test_array_reduce_relation_field_sums_related_costs():
    """array_reduce(sum) поверх relation_field должен суммировать cost из связанных
    записей (150 + 350 = 500), а не возвращать 0."""
    raw_ast = {
        "type": "array_reduce",
        "array_field": "items",
        "agg_function": "sum",
        "item_expression": {
            "type": "relation_field",
            "relation_column": "target_uuid",
            "target_field": "cost",
        },
    }
    ast_tree = parse_ast(raw_ast)
    context = {"items": [{"target_uuid": "rec1"}, {"target_uuid": "rec2"}]}
    related = {
        "rec1": {"_id": "rec1", "data": {"cost": 150}},
        "rec2": {"_id": "rec2", "data": {"cost": 350}},
    }

    async def resolver(target_val, lookup_field="_id"):
        return related.get(str(target_val), {})

    result = await FormulaEvaluator.evaluate(
        ast_tree, context, record_resolver=resolver
    )
    assert result == 500


@pytest.mark.asyncio
async def test_relation_field_missing_target_raises_not_zero():
    """Связь ЗАДАНА, но запись не найдена — раньше молча возвращался 0 (корень
    cost=0). Теперь это громкая FormulaRelationTargetMissingError."""
    from engine.exceptions.evaluator import FormulaRelationTargetMissingError

    raw_ast = {
        "type": "relation_field",
        "relation_column": "target_uuid",
        "target_field": "cost",
    }
    ast_tree = parse_ast(raw_ast)
    context = {"target_uuid": "missing_rec"}

    async def resolver(target_val, lookup_field="_id"):
        return {}  # запись не найдена

    with pytest.raises(FormulaRelationTargetMissingError):
        await FormulaEvaluator.evaluate(ast_tree, context, record_resolver=resolver)


@pytest.mark.asyncio
async def test_relation_field_unset_relation_is_neutral_zero():
    """Связь НЕ задана (relation_column отсутствует в контексте) — это «намеренно
    пусто», корректно вернуть 0, а не ошибку."""
    raw_ast = {
        "type": "relation_field",
        "relation_column": "target_uuid",
        "target_field": "cost",
    }
    ast_tree = parse_ast(raw_ast)
    context = {}  # target_uuid отсутствует -> связь не задана

    async def resolver(target_val, lookup_field="_id"):
        return {}

    result = await FormulaEvaluator.evaluate(
        ast_tree, context, record_resolver=resolver
    )
    assert result == 0
