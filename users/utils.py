# users/utils.py

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from users.models import Users, UserRole
from config import SENDER_EMAIL, ADMIN_PASSWORD


def send_email_message_utility(sender_email, password, receiver_email, subject, body):
    try:
        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = receiver_email
        message["Subject"] = subject
        message.attach(MIMEText(body, "plain"))
    except Exception as e:
        raise e

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender_email, password)
            server.send_message(message)
    except Exception as e:
        raise e


async def init_admin(session: AsyncSession) -> None:
    # 1. Ищем пользователя с ролью ADMIN
    query = select(Users).where(Users.role == UserRole.ADMIN)
    result = await session.execute(query)
    admin_user = result.scalar_one_or_none()

    # 2. Если админ не найден, создаем его
    if admin_user is None:
        new_admin = Users()
        new_admin.email = SENDER_EMAIL
        new_admin.password = ADMIN_PASSWORD  # Сеттер сам захеширует пароль через bcrypt
        new_admin.role = UserRole.ADMIN
        new_admin.active = True
        new_admin.name = "Super Admin"

        session.add(new_admin)
        await session.commit()
    else:
        pass
