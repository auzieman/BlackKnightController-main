#!ipxe
set base-url http://${dictionary.physical_pxe_http_host}/netboot/debian/trixie/amd64
set preseed-url ${dictionary.physical_preseed_url}
initrd --name initrd.gz ${base-url}/initrd.gz
kernel ${base-url}/linux initrd=initrd.gz auto=true priority=critical debconf/priority=critical preseed/url=${preseed-url} url=${preseed-url} hostname=${dictionary.physical_install_hostname} domain=${dictionary.physical_install_domain} interface=auto netcfg/choose_interface=auto netcfg/dhcp_timeout=60 ---
boot
