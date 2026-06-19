from string import Template

from services.rules_store import BASE_DIR

FRESH_BUILD_TEMPLATE_PATH = BASE_DIR / "file_templates" / "fedora-server-minimal.ks.j2"


def default_fedora_iso_plan(release: str = "43", arch: str = "x86_64") -> dict:
    return {
        "release": release,
        "arch": arch,
        "dvd_iso": (
            f"https://download.fedoraproject.org/pub/fedora/linux/releases/{release}/"
            f"Server/{arch}/iso/Fedora-Server-dvd-{arch}-{release}-1.5.iso"
        ),
        "netinst_iso": (
            f"https://download.fedoraproject.org/pub/fedora/linux/releases/{release}/"
            f"Server/{arch}/iso/Fedora-Server-netinst-{arch}-{release}-1.5.iso"
        ),
    }


def iso_download_commands(release: str = "43", arch: str = "x86_64") -> list[str]:
    plan = default_fedora_iso_plan(release=release, arch=arch)
    return [
        "cd /var/lib/vz/template/iso",
        f"curl -L -O {plan['dvd_iso']}",
        f"curl -L -O {plan['netinst_iso']}",
    ]


def render_fedora_kickstart(
    hostname: str,
    release: str = "43",
    arch: str = "x86_64",
    username: str = "deployer",
    password: str = "changeme",
    network_mode: str = "dhcp",
    ip_address: str = "",
    gateway: str = "",
    dns_servers: str = "",
    nameserver_host: str = "ns1.morgans.lan",
) -> str:
    template = Template(FRESH_BUILD_TEMPLATE_PATH.read_text(encoding="utf-8"))
    if network_mode == "static" and ip_address and gateway:
        network_line = (
            f"network --bootproto=static --ip={ip_address} --gateway={gateway} "
            f"--nameserver={dns_servers or nameserver_host} --hostname={hostname}"
        )
    else:
        network_line = f"network --bootproto=dhcp --hostname={hostname}"
    return template.safe_substitute(
        hostname=hostname,
        release=release,
        arch=arch,
        username=username,
        password=password,
        network_line=network_line,
        nameserver_host=nameserver_host,
        install_mirror=f"https://mirrors.fedoraproject.org/metalink?repo=fedora-{release}&arch={arch}",
    )


def fresh_build_plan(
    hostname: str,
    release: str = "43",
    arch: str = "x86_64",
    username: str = "deployer",
    password: str = "changeme",
    network_mode: str = "dhcp",
    ip_address: str = "",
    gateway: str = "",
    dns_servers: str = "",
    nameserver_host: str = "ns1.morgans.lan",
) -> dict:
    iso_plan = default_fedora_iso_plan(release=release, arch=arch)
    kickstart = render_fedora_kickstart(
        hostname=hostname,
        release=release,
        arch=arch,
        username=username,
        password=password,
        network_mode=network_mode,
        ip_address=ip_address,
        gateway=gateway,
        dns_servers=dns_servers,
        nameserver_host=nameserver_host,
    )
    return {
        "hostname": hostname,
        "release": release,
        "arch": arch,
        "iso_plan": iso_plan,
        "download_commands": iso_download_commands(release=release, arch=arch),
        "kickstart_filename": f"{hostname}.ks",
        "kickstart_url": f"http://{nameserver_host}/ks/{hostname}.ks",
        "kickstart_content": kickstart,
        "boot_args": f"inst.ks=http://{nameserver_host}/ks/{hostname}.ks inst.waitfornet=30",
    }
