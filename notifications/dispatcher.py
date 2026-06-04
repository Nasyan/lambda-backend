# notifications/dispatcher.py
from typing import List
from uuid import UUID
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from notifications.models import NotificationHistory, NotificationInbox


class NotificationDispatcher:

    @staticmethod
    async def dispatch(
        pg_session: AsyncSession,
        instance_uuid: str,
        template_uuid: str,
        title: str,
        body: str,
        channels: List[str],
        recipients: List[str],
    ):
        # 🔥 ИСПРАВЛЕНИЕ 1: Явное приведение str к UUID для стабильной работы asyncpg
        inst_uuid = (
            UUID(instance_uuid) if isinstance(instance_uuid, str) else instance_uuid
        )
        tmpl_uuid = (
            UUID(template_uuid) if isinstance(template_uuid, str) else template_uuid
        )

        # Создаем запись в логе истории
        history = NotificationHistory(
            instance_uuid=inst_uuid,
            template_uuid=tmpl_uuid,
            compiled_title=title,
            compiled_body=body,
            target_channels=channels,
            success_count=len(recipients),
        )
        pg_session.add(history)
        await pg_session.flush()  # Получаем сформированный базой history.uuid

        # Маршрутизация по каналам
        if "crm" in channels:
            await NotificationDispatcher._dispatch_to_crm(
                pg_session, history.uuid, recipients
            )

        if "email" in channels:
            await NotificationDispatcher._dispatch_to_email(recipients, title, body)

        if "telegram" in channels:
            await NotificationDispatcher._dispatch_to_telegram(recipients, title, body)

        # 🔥 ИСПРАВЛЕНИЕ 2: Заменяем жесткий .commit() на безопасный .flush().
        # Управление транзакцией делегируется на уровень выше (в AutomationService или воркер).
        await pg_session.flush()

    @staticmethod
    async def _dispatch_to_crm(
        pg_session: AsyncSession, history_uuid: UUID, recipients: List[str]
    ):
        """Батч-создание 'колокольчиков' для сотрудников."""
        inbox_items = []
        for recipient in recipients:
            try:
                # Пытаемся безопасно распарсить UUID. Если это email/tg — просто игнорируем
                user_uuid_obj = (
                    uuid.UUID(recipient) if isinstance(recipient, str) else recipient
                )
                inbox_items.append(
                    NotificationInbox(
                        user_uuid=user_uuid_obj, history_uuid=history_uuid
                    )
                )
            except ValueError:
                continue  # Пропускаем не-UUID строки (email, telegram_id)

        if inbox_items:
            pg_session.add_all(inbox_items)

    @staticmethod
    async def _dispatch_to_email(emails: List[str], title: str, body: str):
        """Отправка задачи в Dramatiq / Celery."""
        # my_dramatiq_worker.send_email_batch.send(emails, title, body)
        pass

    @staticmethod
    async def _dispatch_to_telegram(tg_ids: List[str], title: str, body: str):
        """Отправка задачи в Dramatiq / Celery."""
        # my_dramatiq_worker.send_tg_batch.send(tg_ids, f"{title}\n\n{body}")
        pass
