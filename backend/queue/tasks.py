from celery.signals import worker_process_init
from backend.queue.app import app

@worker_process_init.connect
def reconnect_database(**kwargs):
    from backend.db.database import engine
    engine.dispose(close=False)
    from backend.services.cpu_limits import configure_cpu_limits
    configure_cpu_limits()

@app.task(name='aimarker.execute')
def execute_job(job_id):
    from backend.queue.runtime import execute
    execute(job_id)
