import socket


def probe_tcp_service(host: str, port: int, timeout: float = 1.0) -> dict:
    result = {
        "host": host,
        "port": port,
        "open": False,
        "banner": "",
    }
    try:
        with socket.create_connection((host, port), timeout=timeout) as conn:
            result["open"] = True
            conn.settimeout(timeout)
            try:
                banner = conn.recv(256).decode("utf-8", errors="replace").strip()
                result["banner"] = banner
            except OSError:
                result["banner"] = ""
    except OSError:
        return result
    return result


def ssh_fingerprint(host: str, timeout: float = 1.0) -> dict:
    result = probe_tcp_service(host, 22, timeout=timeout)
    banner = result.get("banner", "")
    result["service"] = "ssh" if result["open"] else "closed"
    result["likely_linux"] = "openssh" in banner.lower()
    return result
