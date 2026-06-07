# users/services/verification_notifier.py

"""Нотификатор кодов верификации (task3, ГЗ-1 Этап 2).

Побочное действие (отправка email) вынесено из AuthService: сервис
аутентификации оркестрирует, а формирование письма и постановка задачи
в воркер — отдельная ответственность."""

import random

from config import SENDER_EMAIL, EMAIL_PASSWORD
from workers.email_tasks import send_email


class RegistrationVerificationNotifier:
    CODE_TTL_MINUTES = 15

    @staticmethod
    def generate_code() -> str:
        return str(random.randint(100000, 999999))

    @classmethod
    def send_code(cls, email: str, name: str, code: str, repeat: bool = False) -> None:
        subject = (
            "Повторное подтверждение регистрации"
            if repeat
            else "Подтверждение регистрации"
        )
        code_word = "новый код" if repeat else "код"
        send_email.send(
            sender_email=SENDER_EMAIL,
            password=EMAIL_PASSWORD,
            receiver_email=email,
            subject=subject,
            body=(
                f"Привет, {name}! Ваш {code_word} активации аккаунта: {code}. "
                f"Код действует {cls.CODE_TTL_MINUTES} минут."
            ),
        )
