# BKC managed provisioning DHCP fragment.
# Included by ${dhcp_config_path}; do not serve the management LAN here.
default-lease-time ${dhcp_lease_time};
max-lease-time ${dhcp_max_lease_time};

option domain-name "${dhcp_domain_name}";
option domain-name-servers ${dhcp_dns_servers};

subnet ${dhcp_subnet} netmask ${dhcp_netmask} {
  range ${dhcp_range_start} ${dhcp_range_end};
  option subnet-mask ${dhcp_netmask};
  option broadcast-address 10.20.0.255;
  option routers ${dhcp_router};
}
