from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ai_recruitment",
    broker=settings.celery_broker_url,
    include=["app.workers.ai_tasks"],
)

# task_routes queue name shares Settings.celery_sensitive_queue_name with worker -Q.
# Changing CELERY_SENSITIVE_QUEUE_NAME requires restarting API and all Celery workers.
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.workers.ai_tasks.process_sensitive_ai_task": {
            "queue": settings.celery_sensitive_queue_name,
        },
    },
)
