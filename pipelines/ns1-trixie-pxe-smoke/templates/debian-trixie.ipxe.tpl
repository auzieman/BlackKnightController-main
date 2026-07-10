#!ipxe
set base-url http://${dictionary.pxe_http_host}/netboot/debian/trixie/amd64
initrd --name initrd.gz ${base-url}/initrd.gz
kernel ${base-url}/linux initrd=initrd.gz auto=true priority=critical url=http://${dictionary.pxe_http_host}/pxe/preseed/trixie-smoke-vm132.cfg hostname=${dictionary.target_install_hostname} domain=${dictionary.target_install_domain} interface=auto netcfg/choose_interface=auto ---
boot
