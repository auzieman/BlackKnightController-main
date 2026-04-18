from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required
from rq.exceptions import NoSuchJobError
from rq.job import Job
from services import bkc_db
from services.job_queue import job_queue_enabled, redis_connection

jobs_blueprint = Blueprint("jobs", __name__)


@jobs_blueprint.route("/jobs", methods=["GET"])
@login_required
def jobs_home():
    return render_template(
        "jobs_home.html.j2",
        job_queue_enabled=job_queue_enabled(),
    )


@jobs_blueprint.route("/jobs/<job_id>", methods=["GET"])
@login_required
def job_status(job_id: str):
    if not job_queue_enabled():
        abort(404)
    try:
        job = Job.fetch(job_id, connection=redis_connection())
    except NoSuchJobError:
        abort(404)
    meta = job.meta or {}
    uid = int(current_user.id)
    user_row = bkc_db.fetch_user_by_id(uid)
    if meta.get("user_id") != uid and not (user_row and user_row.get("is_superuser")):
        abort(403)
    return render_template(
        "job_status.html.j2",
        job=job,
        job_id=job_id,
        meta=meta,
    )
