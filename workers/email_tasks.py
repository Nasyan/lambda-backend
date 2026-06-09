# workers/email_tasks.py

import dramatiq
from config import EMAIL_PASSWORD, SENDER_EMAIL
from users.utils import send_email_message_utility


# @trace_action(name="Task::Send_Email")
@dramatiq.actor
def send_email(receiver_email, subject, body):
    # Секреты (SENDER_EMAIL/EMAIL_PASSWORD) берём из конфига ВНУТРИ воркера,
    # чтобы они не попадали в payload задачи Dramatiq и хранилище брокера (Redis).
    try:
        send_email_message_utility(
            sender_email=SENDER_EMAIL,
            password=EMAIL_PASSWORD,
            receiver_email=receiver_email,
            subject=subject,
            body=body,
        )
    except Exception as e:
        raise e
