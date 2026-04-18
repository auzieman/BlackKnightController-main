from jinja2 import Environment, FileSystemLoader


def render_template(template_file, vars):
    env = Environment(loader=FileSystemLoader('./'))
    tpl = env.get_template(template_file)
    return tpl.render(vars)

