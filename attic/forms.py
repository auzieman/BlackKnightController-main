from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField
from wtforms.validators import DataRequired

class AddNodesForm(FlaskForm):
    group = SelectField('Group', validators=[DataRequired()], choices=[])
    node = StringField('Node', validators=[DataRequired()])

class PropertiesForm(FlaskForm):
    properties = TextAreaField('Properties')
