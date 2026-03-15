import json
import os
from render_template import render_template

# Load configuration from JSON configuration file
with open('deployment/config.json', 'r') as config_file:
    config = json.load(config_file)

# Generate the templates
for group in config['groups']:
    for template in group['templates']:
        # Get the template variables
        vars = {}
        vars.update(config['deployment_vars'])
        vars.update(group['group_vars'])
        vars.update(template['vars'])
        
        # Render the template
        input_file = f'deployment/templates/{template["template"]}'
        output_file = template['output']
        rendered_content = render_template(input_file, vars)
        
        # Write the rendered content to the output file
        if not os.path.exists(os.path.dirname(output_file)):
            os.makedirs(os.path.dirname(output_file))
        with open(output_file, 'w') as outfile:
            outfile.write(rendered_content)

