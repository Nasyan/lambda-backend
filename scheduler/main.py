# scheduler/main.py

from apscheduler.schedulers.blocking import BlockingScheduler

# Нам обязательно нужно импортировать workers, чтобы инициализировался брокер!
import workers  # noqa: F401
from workers.crm_tasks import check_time_based_alerts

scheduler = BlockingScheduler()

scheduler.add_job(
    func=check_time_based_alerts.send,  # .send кидает задачу в Redis
    trigger="interval",
    hours=1,
    id="time_alerts_job",
    replace_existing=True,
)

if __name__ == "__main__":
    print("[PLANNER] Планировщик APScheduler запущен...")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[PLANNER] Планировщик остановлен.")
