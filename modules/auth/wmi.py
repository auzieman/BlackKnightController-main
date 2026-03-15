import wmi

def run_template(host, username, password, template_output):
    c = wmi.WMI(host, user=username, password=password)
    with open(template_output, 'w') as outfile:
        outfile.write(rendered_content)

