from pathlib import Path

from flask import Blueprint, current_app, jsonify
from services.health_checks import readiness_report

health_public_blueprint = Blueprint("health_public", __name__)


@health_public_blueprint.get("/ready")
def ready():
    ok, body = readiness_report(Path(current_app.root_path))
    return jsonify(body), (200 if ok else 503)
