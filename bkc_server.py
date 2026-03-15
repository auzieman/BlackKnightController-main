from app import app
from routes.add_nodes import add_nodes_blueprint
from routes.admin import admin_blueprint
from routes.builder import builder_blueprint
from routes.deploy_host import deploy_host_blueprint
from routes.group import groups
from routes.integrations import integrations_blueprint
from routes.index import index_blueprint
from routes.proxmox_ops import proxmox_ops_blueprint
from routes.templates import templates
from routes.workflow_view import workflow_blueprint


app.register_blueprint(index_blueprint)
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
