import asyncio
import json

import pytest
from fastapi import status

from exceptions.base import BaseAppException
from exceptions.handlers import (
    EXCEPTION_STATUS_MAPPING,
    app_exception_handler,
    resolve_exception_status_code,
)


class AttributeStatusError(BaseAppException):
    status_code = 418
    error_code = "ATTRIBUTE_STATUS"
    message = "Attribute status wins."


class LegacyMappedError(BaseAppException):
    error_code = "LEGACY_MAPPED"
    message = "Legacy mapped."


class UnmappedError(BaseAppException):
    error_code = "UNMAPPED"
    message = "Unmapped."


def test_exception_status_attribute_has_priority(monkeypatch):
    monkeypatch.setitem(
        EXCEPTION_STATUS_MAPPING,
        AttributeStatusError,
        status.HTTP_400_BAD_REQUEST,
    )

    assert resolve_exception_status_code(AttributeStatusError()) == 418


def test_exception_status_falls_back_to_legacy_mapping(monkeypatch):
    monkeypatch.setitem(
        EXCEPTION_STATUS_MAPPING,
        LegacyMappedError,
        status.HTTP_409_CONFLICT,
    )

    assert resolve_exception_status_code(LegacyMappedError()) == 409


def test_exception_status_falls_back_to_500_for_unmapped_exception():
    assert (
        resolve_exception_status_code(UnmappedError())
        == status.HTTP_500_INTERNAL_SERVER_ERROR
    )


def test_app_exception_handler_uses_status_attribute():
    response = asyncio.run(app_exception_handler(None, AttributeStatusError()))
    body = json.loads(response.body)

    assert response.status_code == 418
    assert body == {
        "error_code": "ATTRIBUTE_STATUS",
        "message": "Attribute status wins.",
        "details": {},
    }
