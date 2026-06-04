# mongo/tools/exceptions.py

class TemplateNotFoundError(Exception):
    """Вызывается, если шаблон не найден или принадлежит другому инстансу."""

    pass


class SchemaMutationError(Exception):
    """Вызывается при попытке нелегально изменить типы данных в схеме."""

    pass


class SchemaValidationError(Exception):
    """Вызывается, если отправленные данные (body) не соответствуют схеме."""

    pass


class MongoRepositoryError(Exception):
    """Базовое исключение для нашего слоя работы с MongoDB."""

    pass


class RecordNotFoundError(MongoRepositoryError):
    """Вызывается, если конкретная строка данных не найдена."""

    pass


class RecordValidationError(MongoRepositoryError):
    """Вызывается, если передаваемые данные не соответствуют схеме шаблона."""

    pass
