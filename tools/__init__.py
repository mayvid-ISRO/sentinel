TOOLS = {}

try:
    from tools.input.mouse import click_tool
    TOOLS[click_tool.name] = click_tool
except ImportError:
    pass

try:
    from tools.input.keyboard import type_tool
    TOOLS[type_tool.name] = type_tool
except ImportError:
    pass

try:
    from tools.system.apps import open_app_tool
    TOOLS[open_app_tool.name] = open_app_tool
except ImportError:
    pass

try:
    from tools.system.terminal import run_terminal_command_tool
    TOOLS[run_terminal_command_tool.name] = run_terminal_command_tool
except ImportError:
    pass

try:
    from tools.vision.screenshot import take_screenshot_tool
    TOOLS[take_screenshot_tool.name] = take_screenshot_tool
except ImportError:
    pass

try:
    from tools.system.ssh_connect import ssh_connect_tool
    TOOLS[ssh_connect_tool.name] = ssh_connect_tool
except ImportError:
    pass

try:
    from tools.system.metrics import get_system_metrics_tool
    TOOLS[get_system_metrics_tool.name] = get_system_metrics_tool
except ImportError:
    pass

try:
    from tools.system.metrics import get_top_memory_processes_tool
    TOOLS[get_top_memory_processes_tool.name] = get_top_memory_processes_tool
except ImportError:
    pass

try:
    from tools.browser.browser_click import browser_click_tool
    TOOLS[browser_click_tool.name] = browser_click_tool
except ImportError:
    pass

try:
    from tools.browser.browser_close import browser_close_tool
    TOOLS[browser_close_tool.name] = browser_close_tool
except ImportError:
    pass

try:
    from tools.browser.browser_extract import browser_extract_tool
    TOOLS[browser_extract_tool.name] = browser_extract_tool
except ImportError:
    pass

try:
    from tools.browser.browser_Fullpage_extract import browser_dom_tool
    TOOLS[browser_dom_tool.name] = browser_dom_tool
except ImportError:
    pass

try:
    from tools.browser.browser_open import browser_open_tool
    TOOLS[browser_open_tool.name] = browser_open_tool
except ImportError:
    pass

try:
    from tools.browser.browser_scroll import browser_scroll_tool
    TOOLS[browser_scroll_tool.name] = browser_scroll_tool
except ImportError:
    pass

try:
    from tools.browser.browser_type import browser_type_tool
    TOOLS[browser_type_tool.name] = browser_type_tool
except ImportError:
    pass

try:
    from tools.netapp.netapp import create_volume_tool, delete_volume_tool, list_volumes_tool, patch_volume_tool, list_qtrees_tool, create_qtree_tool, delete_qtree_tool, create_quota_tool, create_cifs_share_tool, save_netapp_credentials_tool, netapp_status_tool
    TOOLS[create_volume_tool.name] = create_volume_tool
    TOOLS[delete_volume_tool.name] = delete_volume_tool
    TOOLS[list_volumes_tool.name] = list_volumes_tool
    TOOLS[patch_volume_tool.name] = patch_volume_tool
    TOOLS[list_qtrees_tool.name] = list_qtrees_tool
    TOOLS[create_qtree_tool.name] = create_qtree_tool
    TOOLS[delete_qtree_tool.name] = delete_qtree_tool
    TOOLS[create_quota_tool.name] = create_quota_tool
    TOOLS[create_cifs_share_tool.name] = create_cifs_share_tool
    TOOLS[save_netapp_credentials_tool.name] = save_netapp_credentials_tool
    TOOLS[netapp_status_tool.name] = netapp_status_tool
except ImportError:
    pass

# Remote fleet: for every node listed in config.json ("nodes" array),
# register node_<name>_<tool> proxies that forward actions to that PC's
# IRIS Node service (iris_node/main.py). Node auth tokens are read from
# the token_env environment variable — never from the config file.
try:
    from iris_config import get as cfg_get
    from tools.web.node_tools import make_node_tools
    for node in (cfg_get("nodes") or []):
        try:
            import os as _os
            node_tools = make_node_tools(
                node["host"],
                port=node.get("port", 9000),
                token=_os.getenv(node.get("token_env", ""), ""),
                prefix=node.get("name", node["host"]).replace("-", "_"),
            )
            TOOLS.update(node_tools)
        except Exception:
            # One unreachable/misconfigured node must not break tool loading.
            pass
except ImportError:
    pass
