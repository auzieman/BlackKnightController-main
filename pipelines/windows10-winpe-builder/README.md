# Windows 10 WinPE Builder

This pipeline turns VMID 113 into the Windows-native artifact builder for BKC
WinPE media.

The intent is to keep ns1 focused on PXE/DHCP/HTTP/SMB serving while the
Windows reference VM performs Windows-native work with ADK, DISM, copype, and
MakeWinPEMedia.

The first runnable version is guarded:

- validate VMID 113 is reachable over BKC SSH
- inspect whether Windows ADK and WinPE add-on tooling exists
- stage BKC WinPE build/publish scripts
- record the artifact publish contract for ns1

ADK install, WinPE build, and publishing are explicit opt-in dictionary gates.
