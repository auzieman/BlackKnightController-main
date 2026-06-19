from app import app
from services.ce_app import csrf, init_ce_app

init_ce_app(app)

from routes.add_nodes import add_nodes_blueprint
from routes.admin import admin_blueprint
from routes.api_v1 import api_blueprint
from routes.auth import auth_blueprint
from routes.builder import builder_blueprint
from routes.deploy_host import deploy_host_blueprint
from routes.group import groups
from routes.health_public import health_public_blueprint
from routes.index import index_blueprint
from routes.integrations import integrations_blueprint
from routes.inventory_console import inventory_console_blueprint
from routes.jobs import jobs_blueprint
from routes.pipelines import pipelines_blueprint
from routes.proxmox_ops import proxmox_ops_blueprint
from routes.resource_graph import resource_graph_blueprint
from routes.settings import settings_blueprint
from routes.templates import templates
from routes.workflow_view import workflow_blueprint

csrf.exempt(api_blueprint)

app.register_blueprint(health_public_blueprint)
app.register_blueprint(jobs_blueprint)
app.register_blueprint(auth_blueprint)
app.register_blueprint(api_blueprint)
app.register_blueprint(settings_blueprint)
app.register_blueprint(index_blueprint)
app.register_blueprint(inventory_console_blueprint)
app.register_blueprint(resource_graph_blueprint)
app.register_blueprint(pipelines_blueprint)
app.register_blueprint(groups)
app.register_blueprint(templates)
app.register_blueprint(add_nodes_blueprint)
app.register_blueprint(admin_blueprint)
app.register_blueprint(builder_blueprint)
app.register_blueprint(integrations_blueprint)
app.register_blueprint(proxmox_ops_blueprint)
app.register_blueprint(deploy_host_blueprint)
app.register_blueprint(workflow_blueprint)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
