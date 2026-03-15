import paramiko

def run_template(host, username, password, key_file, template_output):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if key_file:
        client.load_system_host_keys()
        client.connect(host, username=username, key_filename=key_file)
    else:
        client.connect(host, username=username, password=password)
    sftp = client.open_sftp()
    sftp.put(template_output, rendered_content)
    sftp.close()
    client.close()

