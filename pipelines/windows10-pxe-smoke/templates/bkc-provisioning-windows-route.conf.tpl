# BKC managed provisioning DHCP fragment.
authoritative;
default-lease-time 600;
max-lease-time 7200;

option domain-name "${dictionary.target_install_domain}";
option domain-name-servers 192.168.1.1;

subnet 10.20.0.0 netmask 255.255.255.0 {
  range 10.20.0.100 10.20.0.120;
  option subnet-mask 255.255.255.0;
  option broadcast-address 10.20.0.255;
  option routers 10.20.0.10;
  next-server 10.20.0.10;

  if exists user-class and option user-class = "iPXE" {
    if substring(hardware, 1, 6) = ${dictionary.target_vm_mac} {
      filename "http://${dictionary.pxe_http_host}/pxe/windows10.ipxe";
    } else {
      filename "http://${dictionary.pxe_http_host}/pxe/debian-trixie.ipxe";
    }
  } else {
    filename "undionly.kpxe";
  }
}
