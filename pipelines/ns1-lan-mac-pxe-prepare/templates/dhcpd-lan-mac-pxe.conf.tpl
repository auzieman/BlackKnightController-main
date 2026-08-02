# BKC managed temporary LAN-side PXE fragment.
# MAC-only bring-up path. Do not add a general range here.
not authoritative;
deny unknown-clients;

option architecture-type code 93 = unsigned integer 16;

subnet ${dictionary.lan_subnet} netmask ${dictionary.lan_netmask} {
  option subnet-mask ${dictionary.lan_netmask};
  option routers ${dictionary.lan_router};
  option domain-name-servers ${dictionary.lan_dns_servers};
  next-server ${dictionary.pxe_server_ip};

  host ${dictionary.allowed_pxe_host.name} {
    hardware ethernet ${dictionary.allowed_pxe_host.mac};
    fixed-address ${dictionary.allowed_pxe_host.fixed_address};

    if exists user-class and option user-class = "iPXE" {
      filename "${dictionary.debian_ipxe_url}";
    } elsif option architecture-type = 00:07 {
      filename "${dictionary.ipxe_uefi_filename}";
    } elsif option architecture-type = 00:09 {
      filename "${dictionary.ipxe_uefi_filename}";
    } else {
      filename "${dictionary.ipxe_bios_filename}";
    }
  }
}
