# health/exceptions.py

from fastapi import HTTPException, status


class AppException(HTTPException):
    default_status_code: int = 500
    default_detail: str = "Unexpected Error"

    def __init__(self, detail: str = None, status_code: int = None, **kwargs):
        final_detail = detail or self.default_detail
        final_status_code = status_code or self.default_status_code

        if kwargs and final_detail:
            final_detail = final_detail.format(**kwargs)

        super().__init__(status_code=final_status_code, detail=final_detail)


class UnexpectedHttpException(AppException):
    default_status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = "Error: {e}"


class CantConnectPostgresHttpException(AppException):
    default_status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = "Can`t connect to postgres: {e}"


class CantCheckMigrationsHttpException(AppException):
    default_status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = "Can`t check migrations: {e}"


class CantConnectRedisHttpException(AppException):
    default_status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = "Can`t connect to redis: {e}"


class CantConnectMongoHttpException(AppException):
    default_status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Mongo error {e}"


class CantConnectMinioHttpException(AppException):
    default_status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "MinIO connection failed: {e}"
