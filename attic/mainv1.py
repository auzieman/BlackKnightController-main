#!/usr/bin/env python3

from fabric import *
from jinja2 import *
import json
import os
from flask import Flask, request, render_template, redirect, url_for
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True


class AddNodesForm(FlaskForm):
    group = StringField('Group', validators=[DataRequired()])
    nodes = StringField('Nodes', validators=[DataRequired()])

@app.route('/', methods=['GET'])
def index():
    rules = json.load(open('rules.json', 'r'))
    groups = rules['groups'].keys()
    return render_template('index.html.j2', groups=groups)

@app.route('/group/<group>/hosts', methods=['GET'])
def hosts(group):
    rules = json.load(open('rules.json', 'r'))
    hosts = rules['groups'][group]['nodes'].keys()
    return render_template('hosts.html', group=group, hosts=hosts)

@app.route('/group/<group>/host/<host>/edit', methods=['GET', 'POST'])
def edit_host(group, host):
    rules = json.load(open('rules.json', 'r'))
    if request.method == 'POST':
        rules['groups'][group]['nodes'][host]['user'] = request.form['user']
        rules['groups'][group]['nodes'][host]['port'] = request.form['port']
        rules['groups'][group]['nodes'][host]['private_key'] = request.form['private_key']
        json.dump(rules, open('rules.json', 'w'))
        return redirect(url_for('hosts', group=group))
    else:
        user = rules['groups'][group]['nodes'][host]['user']
        port = rules['groups'][group]['nodes'][host]['port']
        private_key = rules['groups'][group]['nodes'][host]['private_key']
        return render_template('edit_host.html', group=group, host=host, user=user, port=port, private_key=private_key)

@app.route('/group/<group>/edit', methods=['GET', 'POST'])
def edit_group(group):
    rules = json.load(open('rules.json', 'r'))
    if request.method == 'POST':
        rules['groups'][group]['locals']['env'] = request.form['env']
        rules['groups'][group]['locals']['datacenter'] = request.form['datacenter']
        rules['groups'][group]['locals']['release'] = request.form['release']
        json.dump(rules, open('rules.json', 'w'))
        return redirect(url_for('hosts', group=group))
    else:
        env = rules['groups'][group]['locals']['env']
        datacenter = rules['groups'][group]['locals']['datacenter']
        release = rules['groups'][group]['locals']['release']
        return render_template('edit_group.html', group=group, env=env, datacenter=datacenter, release=release)

@app.route('/templates', methods=['GET'])
def templates():
    templates = os.listdir('templates')
    return render_template('templates.html', templates=templates)

@app.route('/template/<template>/edit', methods=['GET', 'POST'])
def edit_template(template):
    if request.method == 'POST':
        with open(os.path.join('templates', template), 'w') as f:
            f.write(request.form['content'])
        return redirect(url_for('templates'))
    else:
        with open(os.path.join('templates', template), 'r') as f:
            content = f.read()
        return render_template('edit_template.html', template=template, content=content)

@app.route('/deploy', methods=['POST'])
def deploy():
    group = request.form['group']
    host = request.form['host']
    user = request.form['user']

    rules = json.load(open('rules.json'))

@app.route('/add_nodes', methods=['GET', 'POST'])
def add_nodes():
    form = AddNodesForm()

    if form.validate_on_submit():
        group = form.group.data
        nodes = form.nodes.data.split(',')

        # Add nodes to the group here

        flash('Nodes added successfully.')
        return redirect(url_for('index'))

    return render_template('add_nodes.html', form=form)

app.run(host='0.0.0.0', port=5000)