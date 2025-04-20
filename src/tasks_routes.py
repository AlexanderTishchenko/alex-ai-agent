from flask import Blueprint, request, jsonify, session
from src.scheduler import list_tasks, create_task, update_task, delete_task
import os
from flask_login import login_required as _login_required
import logging
logger = logging.getLogger(__name__)

# Feature flag for Google OAuth
ENABLE_GOOGLE_OAUTH = os.getenv("ENABLE_GOOGLE_OAUTH", "true").lower() in ("1", "true", "yes")
if ENABLE_GOOGLE_OAUTH:
    login_required = _login_required
else:
    def login_required(fn):
        return fn

tasks_bp = Blueprint("tasks_bp", __name__)

@tasks_bp.route("/tasks", methods=["GET"])
@login_required
def get_tasks():
    session_id = session["session_id"]
    return jsonify([t.__dict__ for t in list_tasks(session_id)])

@tasks_bp.route("/tasks", methods=["POST"])
@login_required
def post_task():
    session_id = session["session_id"]
    data = request.get_json()
    tz = data.get("timezone", "UTC")
    logger.info(f"POST /tasks called with session_id={session_id}")
    logger.debug(f"Request data: {data}")
    logger.debug(f"Timezone parameter: {tz}")
    t = create_task(session_id, data, tz)
    logger.info(f"Task created: id={t.id}, cron={t.cron}, next_run={t.next_run.isoformat()}, tz={tz}")
    return jsonify(t.__dict__), 201

@tasks_bp.route("/tasks/<task_id>", methods=["PUT"])
@login_required
def put_task(task_id):
    session_id = session["session_id"]
    patch = request.get_json()
    tz = patch.get("timezone", "UTC")
    t = update_task(task_id, patch, tz, session_id)
    return jsonify(t.__dict__)

@tasks_bp.route("/tasks/<task_id>", methods=["DELETE"])
@login_required
def delete_task_route(task_id):
    delete_task(task_id)
    return '', 204
