from flask import Blueprint, flash, redirect, url_for

workflow_blueprint = Blueprint("workflow_view", __name__)


@workflow_blueprint.route("/workflow", methods=["GET", "POST"])
def workflow():
    flash("Workflow is now presented as Pipelines. The legacy workflow board has been retired.")
    return redirect(url_for("pipelines.pipelines"))
