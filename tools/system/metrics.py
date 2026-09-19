"""
System metrics tool — CPU, memory, disk stats via psutil.
Use this for any task asking about system resource usage.
"""

import psutil
from tools.base import Tool


def get_system_metrics() -> str:
    """Return a summary of current CPU, memory, and disk usage."""
    cpu_pct = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return (
        f"CPU: {cpu_pct}% | "
        f"Memory: {mem.percent}% used ({mem.used // 1024**3}GB / {mem.total // 1024**3}GB) | "
        f"Disk: {disk.percent}% used ({disk.used // 1024**3}GB / {disk.total // 1024**3}GB)"
    )


def get_top_memory_processes(n: int = 10) -> str:
    """Return the top N processes by memory usage percentage."""
    procs = sorted(
        psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]),
        key=lambda p: p.info.get("memory_percent") or 0,
        reverse=True,
    )[:n]
    lines = [f"{'PID':<8} {'MEM%':<7} {'CPU%':<7} NAME"]
    lines.append("-" * 45)
    for p in procs:
        pid = p.info.get("pid", "?")
        mp = p.info.get("memory_percent") or 0.0
        cp = p.info.get("cpu_percent") or 0.0
        name = (p.info.get("name") or "?")[:30]
        lines.append(f"{pid:<8} {mp:<7.1f} {cp:<7.1f} {name}")
    return "\n".join(lines)


get_system_metrics_tool = Tool(
    name="get_system_metrics",
    description=(
        "Get current system resource usage: CPU %, memory % and GB used/total, "
        "disk % and GB used/total. Call this first when investigating performance."
    ),
    args_schema={
        "type": "object",
        "properties": {},
    },
    func=get_system_metrics,
)

get_top_memory_processes_tool = Tool(
    name="get_top_memory_processes",
    description=(
        "List the top N processes consuming the most memory, sorted by "
        "memory %. Use when asked to identify top memory consumers. "
        "Default n=10; pass a number to limit results."
    ),
    args_schema={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of top processes to return (default 10).",
            }
        },
    },
    func=get_top_memory_processes,
)
