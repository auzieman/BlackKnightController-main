@app.route('/run_command', methods=['GET', 'POST'])
def run_command():
    if request.method == 'POST':
        group = request.form.get('group')
        nodes = request.form.getlist('nodes')
        command = request.form.get('command')

        # Execute command on selected group/nodes
        # Group is preferred over individual nodes
        if group:
            rules = json.load(open('rules.json', 'r'))
            nodes = rules['groups'][group]['nodes'].keys()

        # Build Fabric command to run on selected nodes
        result = {}
        for node in nodes:
            try:
                output = run(command, host=node)
                result[output] = result.get(output, []) + [node]
            except Exception as e:
                result[str(e)] = result.get(str(e), []) + [node]

        # Render result template with grouped output
        return render_template('result.html', result=result)

    else:
        rules = json.load(open('rules.json', 'r'))
        groups = rules['groups'].keys()
        return render_template('run_command.html', groups=groups)
