import paramiko

def run_template(host, username, password, key_file, template_output):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if key_file:
        client.load_system_host_keys()
        client.connect(host, username=username, key_filename=key_file)
    else:
        client.connect(host, username=username, password=password)
    stdin, stdout, stderr = client.exec_command(f'echo "{rendered_content}" > {template_output}')
    for line in stdout:
        print(line.strip('\n'))
    for line in stderr:
        print(line.strip('\n'))
    client.close()

