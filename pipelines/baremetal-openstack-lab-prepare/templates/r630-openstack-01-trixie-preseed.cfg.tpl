d-i debian-installer/locale string en_US.UTF-8
d-i keyboard-configuration/xkb-keymap select us
d-i netcfg/choose_interface select auto
d-i netcfg/dhcp_timeout string 60
d-i netcfg/get_hostname string ${dictionary.physical_install_hostname}
d-i netcfg/get_domain string ${dictionary.physical_install_domain}
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

d-i partman-auto/disk string ${dictionary.physical_install_disk}
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
d-i pkgsel/include string openssh-server sudo facter curl git ca-certificates
d-i pkgsel/upgrade select none
popularity-contest popularity-contest/participate boolean false

d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string ${dictionary.physical_install_disk}

d-i preseed/late_command string \
  /bin/sh -c "printf '%s\n' '${dictionary.physical_install_hostname}' > /target/etc/hostname"; \
  /bin/sh -c "grep -q '${dictionary.physical_install_hostname}' /target/etc/hosts || printf '%s\n' '127.0.1.1 ${dictionary.physical_install_hostname}.${dictionary.physical_install_domain} ${dictionary.physical_install_hostname}' >> /target/etc/hosts"; \
  in-target /bin/sh -c "usermod -aG sudo ${dictionary.target_install_user}"; \
  in-target /bin/sh -c "mkdir -p /home/${dictionary.target_install_user}/.ssh"; \
  in-target /bin/sh -c "mkdir -p /root/.ssh"; \
  /bin/sh -c "printf '%s\n' '${dictionary.target_ssh_authorized_key}' > /target/home/${dictionary.target_install_user}/.ssh/authorized_keys"; \
  /bin/sh -c "printf '%s\n' '${dictionary.target_ssh_authorized_key}' > /target/root/.ssh/authorized_keys"; \
  in-target /bin/chown -R ${dictionary.target_install_user}:${dictionary.target_install_user} /home/${dictionary.target_install_user}/.ssh; \
  in-target /bin/chmod 700 /home/${dictionary.target_install_user}/.ssh; \
  in-target /bin/chmod 600 /home/${dictionary.target_install_user}/.ssh/authorized_keys; \
  in-target /bin/chmod 700 /root/.ssh; \
  in-target /bin/chmod 600 /root/.ssh/authorized_keys; \
  in-target /bin/sh -c "printf '%s\n' '${dictionary.target_install_user} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-bkc-${dictionary.target_install_user}"; \
  in-target /bin/sh -c "printf '%s\n' 'Defaults:${dictionary.target_install_user} !requiretty' >> /etc/sudoers.d/90-bkc-${dictionary.target_install_user}"; \
  in-target /bin/chmod 440 /etc/sudoers.d/90-bkc-${dictionary.target_install_user}; \
  in-target /bin/sh -c "mkdir -p /etc/ssh/sshd_config.d"; \
  in-target /bin/sh -c "printf '%s\n' 'PermitRootLogin yes' 'PasswordAuthentication yes' > /etc/ssh/sshd_config.d/90-bkc-lab-access.conf"; \
  in-target /bin/sh -c "mkdir -p /var/lib/bkc"; \
  in-target /bin/sh -c "printf '%s\n' '{\"node_id\":\"node:physical_machine:r630-openstack-01\",\"profile\":\"openstack-base-os\",\"installer\":\"debian-preseed\",\"hostname\":\"${dictionary.physical_install_hostname}\"}' > /var/lib/bkc/base-provisioning.json"; \
  in-target /bin/systemctl enable ssh

d-i finish-install/reboot_in_progress note
