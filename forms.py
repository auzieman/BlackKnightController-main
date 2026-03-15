from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired


class AddNodesForm(FlaskForm):
    group = SelectField("Group", validators=[DataRequired()], choices=[])
    nodes = TextAreaField("Nodes", validators=[DataRequired()])


class ScanSubnetForm(FlaskForm):
    group = SelectField("Group", validators=[DataRequired()], choices=[])
    subnet = StringField("Subnet", validators=[DataRequired()])
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password")
    install_key = BooleanField("Install BKC SSH key after login")
