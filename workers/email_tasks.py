# workers/email_tasks.py

import dramatiq
from users.utils import send_email_message_utility


# @trace_action(name="Task::Send_Email")
@dramatiq.actor
def send_email(sender_email, password, receiver_email, subject, body):
    try:
        send_email_message_utility(
            sender_email=sender_email,
            password=password,
            receiver_email=receiver_email,
            subject=subject,
            body=body,
        )
    except Exception as e:
        raise e
