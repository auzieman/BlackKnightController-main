# AUZiX validated HDD build and PVE deployment

This stage cannot run until the fresh validation container has a passing
receipt. It uses that exact validation root as the HDD payload and invokes the
existing AUZiX HDD builder; it does not redesign init, GRUB, X11, or E.

VMID135 is the protected graphical reference. VMID142 is the default disposable
deployment target. Changing the target to 135 is rejected unless the explicit
protected-target override is enabled.

The pipeline records the image hash, PVE configuration, power-cycle result, and
network/SSH reachability. Desktop and launcher evidence remains a follow-on
product gate after the machine is reachable.
