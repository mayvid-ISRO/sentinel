# tools/ssh_tool.py
import paramiko
import logging
import traceback
from typing import Optional
from tools.base import Tool
from tools.safety import is_safe

from .ssh_manager import SSHManager

log = logging.getLogger(__name__)

def _connect_once(host: str, username: str, password: Optional[str]) -> paramiko.SSHClient:
    """
    Establish a new SSH connection (or reuse an existing one).
    Raises the original paramiko exception on failure.
    """
    manager = SSHManager()
    existing = manager.get(host)
    if existing:
        # Quick sanity‑check – is the underlying transport still alive?
        if existing.get_transport() and existing.get_transport().is_active():
            log.debug("Re‑using existing SSH session for %s", host)
            return existing
        else:
            log.debug("Stale session for %s – closing", host)
            manager.close(host)

    log.info("Opening new SSH connection to %s", host)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=username,
        password=password,
        timeout=10,
    )
    manager.set(host, client)
    return client


def ssh_connect(host: str,username: str,password: Optional[str] = None,command: Optional[str] = None,keep_alive: bool = False,) -> str:
    """
    Connect to a remote host via SSH (optionally run a command).

    Parameters
    ----------
    host: str
        IP or hostname.
    username: str
    password: str | None
        If omitted, paramiko will try key‑based auth (the default ssh‑agent keys).
    command: str | None
        If supplied, the command is executed and its stdout (or stderr) is returned.
    keep_alive: bool
        If True the connection stays cached in SSHManager; otherwise it is closed
        after the call (default: False – safer for short‑lived agents).

    Returns
    -------
    str – command output or a short status message.
    """
    try:
        client = _connect_once(host, username, password)

        if command:
            if not is_safe(command):
                return "Error: Command is blocked for safety reasons."
            log.info("Executing remote command on %s: %s", host, command)
            stdin, stdout, stderr = client.exec_command(command)
            # Wait for the command to finish – you can add a timeout if you like
            exit_status = stdout.channel.recv_exit_status()
            out = stdout.read().decode()
            err = stderr.read().decode()
            result = out if out else err
            result = f"After running the command:-->{command} it returned --> {result.strip()}" or f"(command returned exit status {exit_status})"
        else:
            result = f"Connected to {host} (no command supplied)"

        # Close automatically unless the caller wants to keep it around
        if not keep_alive:
            SSHManager().close(host)

        return result

    except paramiko.ssh_exception.AuthenticationException:
        log.exception("Authentication failed for %s@%s", username, host)
        return "Error: authentication failed"
    except paramiko.ssh_exception.SSHException as e:
        log.exception("SSH error while connecting to %s", host)
        return f"Error: SSH problem – {e}"
    except Exception:
        # Log the full traceback – this is invaluable for debugging
        log.exception("Unexpected error in ssh_connect")
        return "Error: unexpected failure (see logs for details)"




ssh_connect_tool = Tool(
    name="ssh_connect_tool",
    description="Connect to a remote server via SSH (optionally run a command)",
    args_schema={"host": "string","username": "string","password": "string", "command": "string","keep_alive": "boolean"},
    func=ssh_connect,
)