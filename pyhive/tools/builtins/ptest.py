import json
import re
import subprocess


def list_active_connections(include_process: bool = False) -> list[dict]:
    cmd = ["netstat", "-ano"] if include_process else ["netstat", "-an"]
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)

    lines = result.stdout.splitlines()

    data = []

    for line in lines:
        line = line.strip()

        if not line or line.startswith(("Active", "Proto")):
            continue

        parts = re.split(r"\s+", line)

        if len(parts) < 3:
            continue

        entry = {
            "protocol": parts[0],
            "local_address": parts[1],
            "foreign_address": parts[2],
            "state": parts[3] if len(parts) > 3 else None,
        }

        if include_process:
            entry["pid"] = parts[4] if len(parts) > 4 else None

        data.append(entry)

    return data


def test_net_connection(ip: str, port: int) -> list[dict]:
    ps_cmd = (
        f"Test-NetConnection -ComputerName '{ip}' -Port {port} "
        "| Select ComputerName,RemoteAddress,RemotePort,TcpTestSucceeded "
        "| ConvertTo-Json"
    )

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True
    )

    if not result.stdout.strip():
        return []

    data = json.loads(result.stdout)

    return [data] if isinstance(data, dict) else data
