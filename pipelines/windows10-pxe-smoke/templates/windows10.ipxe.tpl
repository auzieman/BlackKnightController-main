#!ipxe
set base-url http://${dictionary.pxe_http_host}/pxe/windows/10-22h2
set winpe-url http://${dictionary.pxe_http_host}/netboot/windows/10-22h2
kernel ${base-url}/wimboot
initrd ${base-url}/Autounattend.xml Autounattend.xml
initrd ${base-url}/bkc-firstboot.ps1 bkc-firstboot.ps1
initrd ${base-url}/winpeshl.ini winpeshl.ini
initrd ${base-url}/startnet.cmd startnet.cmd
initrd ${base-url}/bkc-winpe-setup.cmd bkc-winpe-setup.cmd
initrd ${winpe-url}/boot.wim boot.wim
imgstat
boot
