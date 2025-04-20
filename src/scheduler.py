import os
import uuid
import logging

# --- Logging config: file + console ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[
        logging.FileHandler("scheduler.log"),
        logging.StreamHandler()
    ]
)

from dataclasses import dataclass
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from croniter import croniter
from sqlalchemy import create_engine, Column, String, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dateutil import parser as dtparser
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'tasks.db')
DB_PATH = os.path.abspath(DB_PATH)
Base = declarative_base()

@dataclass
class Task:
    id: str
    session_id: str
    message: str
    cron: str
    next_run: datetime
    enabled: bool
    created_at: datetime
    updated_at: datetime

class TaskModel(Base):
    __tablename__ = 'tasks'
    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False)
    message = Column(String, nullable=False)
    cron = Column(String, nullable=False)
    next_run = Column(DateTime, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

engine = create_engine(f'sqlite:///{DB_PATH}', connect_args={'check_same_thread': False})
Session = sessionmaker(bind=engine)

scheduler = BackgroundScheduler()
logger = logging.getLogger("scheduler")

SENT_LOG = os.path.join(os.path.dirname(__file__), '..', 'sent.log')

# --- Util ---
def _to_task(model: TaskModel) -> Task:
    return Task(
        id=model.id,
        session_id=model.session_id,
        message=model.message,
        cron=model.cron,
        next_run=model.next_run,
        enabled=model.enabled,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )

from zoneinfo import ZoneInfo

def _schedule_job(task: Task, user_tz: str = "UTC"):
    if not task.enabled:
        logger.info(f"Not scheduling disabled task {task.id}")
        return
    job_id = f"task_{task.id}"
    # Remove existing job
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed existing scheduled job: {job_id}")
    except Exception as ex:
        logger.debug(f"No existing job to remove for {job_id}: {ex}")
    # Date or cron?
    try:
        dt = None
        tz = ZoneInfo(user_tz)
        logger.info(f"Scheduling job_id={job_id} for task_id={task.id} (cron/iso={task.cron}) in timezone={user_tz}")
        if croniter.is_valid(task.cron):
            trigger = CronTrigger.from_crontab(task.cron, timezone=tz)
            dt = croniter(task.cron, datetime.now(tz)).get_next(datetime)
            logger.info(f"CronTrigger: cron='{task.cron}', next_run_time={dt.isoformat()} (tz={tz})")
        else:
            dt = dtparser.parse(task.cron)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
                logger.debug(f"Parsed ISO as UTC, converted to tz={tz}: {dt.isoformat()}")
            else:
                dt = dt.astimezone(tz)
                logger.debug(f"Parsed ISO as tz-aware, converted to tz={tz}: {dt.isoformat()}")
            trigger = DateTrigger(run_date=dt, timezone=tz)
            logger.info(f"DateTrigger: run_date={dt.isoformat()} (tz={tz})")
        scheduler.add_job(
            lambda: _fire_task(task.id),
            trigger,
            id=job_id,
            replace_existing=True,
        )
        logger.info(f"Scheduled job {job_id} for task {task.id}: next_run={dt.isoformat()} (tz={tz})")
    except Exception as e:
        logger.error(f"Failed to schedule task {task.id}: {e}", exc_info=True)

def _fire_task(task_id: str):
    sess = Session()
    model = sess.query(TaskModel).filter_by(id=task_id).first()
    if not model or not model.enabled:
        sess.close()
        return
    task = _to_task(model)
    url = os.getenv("SELF_ROOT", "http://127.0.0.1:5001") + "/send_message"
    try:
        resp = requests.post(url, json={"message": task.message, "session_id": task.session_id}, timeout=5)
        logger.info(f"Scheduled task {task.id} fired: {resp.status_code}")
        # Log to sent.log
        with open(SENT_LOG, "a") as f:
            f.write(f"{datetime.utcnow().isoformat()}\t{task.id}\n")
        # Emit SSE event (optional, via Flask app context)
        # Recompute next_run
        if croniter.is_valid(task.cron):
            next_run = croniter(task.cron, datetime.utcnow()).get_next(datetime)
        else:
            next_run = None
        model.next_run = next_run
        model.updated_at = datetime.utcnow()
        sess.commit()
    except Exception as e:
        logger.error(f"Failed to POST scheduled task {task.id}: {e}")
    finally:
        sess.close()

def list_tasks(session_id: str) -> list[Task]:
    sess = Session()
    models = sess.query(TaskModel).filter_by(session_id=session_id).all()
    tasks = [_to_task(m) for m in models]
    sess.close()
    return tasks

def create_task(session_id: str, data: dict, tz: str = "UTC") -> Task:
    logger.debug(f"User timezone: {tz}")
    now = datetime.now(ZoneInfo(tz))
    task_id = uuid.uuid4().hex
    cron = data["cron"]
    logger.info(f"Creating task: id={task_id} session={session_id} cron={cron} tz={tz} message={data['message']}")
    logger.debug(f"Session ID: {session_id}")
    logger.debug(f"Request data: {data}")
    logger.debug(f"User timezone: {tz}")
    logger.debug(f"Initial time (UTC): {now.isoformat()}")
    logger.debug(f"Cron expression: {cron}")
    if croniter.is_valid(cron):
        next_run = croniter(cron, now).get_next(datetime)
        logger.info(f"Cron valid, computed next_run: {next_run.isoformat()}")
    else:
        next_run = dtparser.parse(cron)
        logger.info(f"Non-cron input, parsed next_run: {next_run.isoformat()}")
    model = TaskModel(
        id=task_id,
        session_id=session_id,
        message=data["message"],
        cron=cron,
        next_run=next_run,
        enabled=data.get("enabled", True),
        created_at=now,
        updated_at=now,
    )
    sess = Session()
    sess.add(model)
    sess.commit()
    logger.info(f"Task committed to DB: id={model.id}, next_run={model.next_run.isoformat()}")
    task = _to_task(model)
    _schedule_job(task, tz)
    logger.info(f"Scheduled job for task: id={task.id}, cron={task.cron}, next_run={task.next_run.isoformat()}, tz={tz}")
    sess.close()
    return task

def update_task(task_id: str, patch: dict, tz: str = "UTC", session_id: str = None) -> Task:
    sess = Session()
    model = sess.query(TaskModel).filter_by(id=task_id).first()
    if not model:
        sess.close()
        raise ValueError("Task not found")
    for k, v in patch.items():
        if hasattr(model, k):
            setattr(model, k, v)
    # Recompute next_run if cron or enabled changed
    if "cron" in patch or "enabled" in patch:
        now = datetime.utcnow()
        cron = model.cron
        if croniter.is_valid(cron):
            model.next_run = croniter(cron, now).get_next(datetime)
        else:
            model.next_run = dtparser.parse(cron)
    model.updated_at = datetime.utcnow()
    sess.commit()
    task = _to_task(model)
    _schedule_job(task, tz)
    sess.close()
    return task

def delete_task(task_id: str) -> None:
    sess = Session()
    model = sess.query(TaskModel).filter_by(id=task_id).first()
    if model:
        sess.delete(model)
        sess.commit()
        try:
            scheduler.remove_job(f"task_{task_id}")
        except Exception:
            pass
    sess.close()

def bootstrap(app):
    Base.metadata.create_all(engine)
    # Schedule all enabled tasks
    sess = Session()
    for model in sess.query(TaskModel).filter_by(enabled=True).all():
        _schedule_job(_to_task(model))
    sess.close()
    scheduler.start()
    logger.info("Scheduler started")
