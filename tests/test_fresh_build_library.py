from services.fresh_build_library import fresh_build_plan, render_fedora_kickstart


def test_render_fedora_kickstart_is_unattended_for_netinst():
    kickstart = render_fedora_kickstart(
        hostname="fedora-template-115.lab.auzietek.com",
        release="44",
        arch="x86_64",
        username="deployer",
        password="changeme",
        network_mode="dhcp",
        nameserver_host="ns1.lab.auzietek.com",
    )

    assert "text" in kickstart
    assert "url --mirrorlist=https://mirrors.fedoraproject.org/metalink?repo=fedora-44&arch=x86_64" in kickstart
    assert "user --name=deployer --groups=wheel --password=changeme --plaintext" in kickstart
    assert "network --bootproto=dhcp --hostname=fedora-template-115.lab.auzietek.com" in kickstart
    assert "clearpart --all --initlabel" in kickstart
    assert "autopart --type=lvm" in kickstart
    assert "@core" in kickstart
    assert "@^minimal-environment" not in kickstart
    assert "firstboot --disable" in kickstart
    assert "reboot --eject" in kickstart


def test_fresh_build_plan_uses_release_specific_kickstart_url():
    plan = fresh_build_plan(
        hostname="fedora-template-115.lab.auzietek.com",
        release="44",
        arch="x86_64",
        nameserver_host="ns1.lab.auzietek.com",
    )

    assert plan["kickstart_filename"] == "fedora-template-115.lab.auzietek.com.ks"
    assert plan["kickstart_url"] == "http://ns1.lab.auzietek.com/ks/fedora-template-115.lab.auzietek.com.ks"
    assert "inst.ks=http://ns1.lab.auzietek.com/ks/fedora-template-115.lab.auzietek.com.ks" in plan["boot_args"]
    assert "repo=fedora-44&arch=x86_64" in plan["kickstart_content"]
