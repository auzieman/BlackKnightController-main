from pathlib import Path

from flask import Flask

BASE_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "html_templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["SECRET_KEY"] = "dev-placeholder-overwritten-by-init-ce-app"
app.config["TEMPLATES_AUTO_RELOAD"] = True
