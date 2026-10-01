from celery import Celery
from backend.config import get_settings

settings = get_settings()
app = Celery('aimarker', broker=settings.redis_url, include=['backend.queue.tasks'])
app.conf.update(
    task_serializer='json', accept_content=['json'], result_serializer='json',
    task_ignore_result=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, worker_concurrency=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={'visibility_timeout': 7200},
    visibility_timeout=7200, task_soft_time_limit=6600, task_time_limit=6900,
    worker_cancel_long_running_tasks_on_connection_loss=True,
)
