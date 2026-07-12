d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us
d-i netcfg/choose_interface select auto
d-i netcfg/dhcp_timeout string 60
d-i netcfg/get_hostname string ${dictionary.target_install_hostname}
d-i netcfg/get_domain string ${dictionary.target_install_domain}
d-i mirror/country string manual
d-i mirror/http/hostname string deb.debian.org
d-i mirror/http/directory string /debian
d-i mirror/http/proxy string

d-i passwd/root-login boolean true
d-i passwd/root-password-crypted password ${dictionary.target_root_password_crypted}
d-i passwd/make-user boolean true
d-i passwd/user-fullname string ${dictionary.target_install_fullname}
d-i passwd/username string ${dictionary.target_install_user}
d-i passwd/user-password-crypted password ${dictionary.target_user_password_crypted}
d-i user-setup/allow-password-weak boolean true
d-i user-setup/encrypt-home boolean false

d-i clock-setup/utc boolean true
d-i time/zone string US/Pacific
d-i clock-setup/ntp boolean true

d-i partman-auto/disk string ${dictionary.target_install_disk}
d-i partman-auto/method string regular
d-i partman-lvm/device_remove_lvm boolean true
d-i partman-lvm/confirm boolean true
d-i partman-lvm/confirm_nooverwrite boolean true
d-i partman-md/device_remove_md boolean true
d-i partman-md/confirm boolean true
d-i partman-auto/choose_recipe select atomic
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

d-i apt-setup/non-free-firmware boolean true
d-i apt-setup/contrib boolean true
d-i apt-setup/non-free boolean true
tasksel tasksel/first multiselect standard, ssh-server
d-i pkgsel/include string openssh-server sudo facter curl git qemu-guest-agent ca-certificates
d-i pkgsel/upgrade select none
popularity-contest popularity-contest/participate boolean false

d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string ${dictionary.target_install_disk}

d-i preseed/late_command string \
  in-target /bin/sh -c "usermod -aG sudo ${dictionary.target_install_user}"; \
  in-target /bin/sh -c "mkdir -p /home/${dictionary.target_install_user}/.ssh"; \
  /bin/sh -c "printf '%s\n' '${dictionary.target_ssh_authorized_key}' > /target/home/${dictionary.target_install_user}/.ssh/authorized_keys"; \
  in-target /bin/chown -R ${dictionary.target_install_user}:${dictionary.target_install_user} /home/${dictionary.target_install_user}/.ssh; \
  in-target /bin/chmod 700 /home/${dictionary.target_install_user}/.ssh; \
  in-target /bin/chmod 600 /home/${dictionary.target_install_user}/.ssh/authorized_keys; \
  in-target /bin/systemctl enable ssh qemu-guest-agent

d-i finish-install/reboot_in_progress note
