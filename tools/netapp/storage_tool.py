import logging
from netapp_ontap import config, NetAppRestError
from netapp_ontap.resources import Volume, Svm, Aggregate, Qtree, QuotaRule, Igroup, IgroupInitiator, Disk, IpInterface, Lun
from netapp_ontap import HostConnection
from tools.base import Tool

log = logging.getLogger(__name__)


def _setup_connection(cluster: str, api_user: str, api_pass: str, verify: bool = False) -> None:
    """Configure the global SDK connection.
    The SDK expects a single global ``config.CONNECTION`` object.
    ``verify`` defaults to ``False`` because many ONTAP labs use self‑signed certs.
    """
    config.CONNECTION = HostConnection(cluster, username=api_user, password=api_pass, verify=verify)


# ---------------------------------------------------------------------------
# Volume helpers – these are thin wrappers around the NetApp SDK that return a
# string representation of the result. The agent can surface the string to the
# user or use it for further logic.
# ---------------------------------------------------------------------------

def list_volumes(cluster: str, api_user: str, api_pass: str, svm_name: str) -> str:
    _setup_connection(cluster, api_user, api_pass)
    try:
        volumes = Volume.get_collection(**{"svm.name": svm_name}, fields="uuid")
        output = []
        for vol in volumes:
            output.append(f"Volume: {vol.name}, UUID: {vol.uuid}")
        return "\n".join(output) if output else "No volumes found for SVM {svm_name}."
    except NetAppRestError as e:
        log.error("Error listing volumes: %s", e)
        return f"Error listing volumes: {e}"


def create_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    aggregate_name: str,
    volume_name: str,
    size_mb: int,
    volume_type: str = "rw",
    style: str = "flexvol",
) -> str:
    _setup_connection(cluster, api_user, api_pass)
    payload = {
        "svm": {"name": svm_name},
        "aggregates": [{"name": aggregate_name}],
        "name": volume_name,
        "size": size_mb * 1024 * 1024,  # MB → Bytes
        "type": volume_type,
        "style": style,
    }
    try:
        vol = Volume.from_dict(payload)
        if vol.post(poll=True):
            return f"Volume '{volume_name}' created successfully on SVM '{svm_name}'."
        else:
            return f"Failed to create volume '{volume_name}'."
    except NetAppRestError as e:
        log.error("Error creating volume: %s", e)
        return f"Error creating volume: {e}"


def patch_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    volume_name: str,
    new_name: str = None,
    new_size_mb: int = None,
) -> str:
    _setup_connection(cluster, api_user, api_pass)
    try:
        vol = Volume.find(name=volume_name)
    except NetAppRestError as e:
        return f"Could not locate volume '{volume_name}': {e}"

    if new_name:
        vol.name = new_name
    if new_size_mb:
        vol.size = new_size_mb * 1024 * 1024
    try:
        if vol.patch(poll=True):
            return f"Volume '{volume_name}' patched successfully."
        else:
            return f"Patch request for volume '{volume_name}' did not succeed."
    except NetAppRestError as e:
        return f"Error patching volume: {e}"


def delete_volume(cluster: str, api_user: str, api_pass: str, volume_name: str) -> str:
    _setup_connection(cluster, api_user, api_pass)
    try:
        vol = Volume.find(name=volume_name)
        if vol.delete(poll=True):
            return f"Volume '{volume_name}' deleted successfully."
        else:
            return f"Failed to delete volume '{volume_name}'."
    except NetAppRestError as e:
        return f"Error deleting volume: {e}"


def clone_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    source_svm: str,
    source_vol: str,
    clone_name: str,
) -> str:
    _setup_connection(cluster, api_user, api_pass)
    # Resolve UUIDs for parent SVM and volume
    try:
        svm_uuid = next(Svm.get_collection(**{"name": source_svm}), None).uuid
        vol_uuid = next(Volume.get_collection(**{"svm.name": source_svm, "name": source_vol}), None).uuid
    except Exception as e:
        return f"Failed to resolve source identifiers: {e}"

    payload = {
        "svm": {"uuid": svm_uuid},
        "name": clone_name,
        "clone": {
            "is_flexclone": True,
            "parent_svm": {"name": source_svm, "uuid": svm_uuid},
            "parent_volume": {"name": source_vol, "uuid": vol_uuid},
        },
    }
    try:
        clone = Volume.from_dict(payload)
        if clone.post(poll=True):
            return f"Clone '{clone_name}' created successfully from volume '{source_vol}'."
        else:
            return f"Failed to create clone '{clone_name}'."
    except NetAppRestError as e:
        return f"Error cloning volume: {e}"

# ---------------------------------------------------------------------------
# Register tools for the Claude‑Code agent
# ---------------------------------------------------------------------------

list_volumes_tool = Tool(
    name="netapp_list_volumes",
    description="List volumes for a given SVM.",
    args_schema={
        "cluster": "string",
        "api_user": "string",
        "api_pass": "string",
        "svm_name": "string",
    },
    func=list_volumes,
)

create_volume_tool = Tool(
    name="netapp_create_volume",
    description="Create a new volume on a specified SVM and aggregate.",
    args_schema={
        "cluster": "string",
        "api_user": "string",
        "api_pass": "string",
        "svm_name": "string",
        "aggregate_name": "string",
        "volume_name": "string",
        "size_mb": "integer",
        "volume_type": "string",
        "style": "string",
    },
    func=create_volume,
)

patch_volume_tool = Tool(
    name="netapp_patch_volume",
    description="Update volume name or size.",
    args_schema={
        "cluster": "string",
        "api_user": "string",
        "api_pass": "string",
        "volume_name": "string",
        "new_name": "string",
        "new_size_mb": "integer",
    },
    func=patch_volume,
)

delete_volume_tool = Tool(
    name="netapp_delete_volume",
    description="Delete a volume by name.",
    args_schema={
        "cluster": "string",
        "api_user": "string",
        "api_pass": "string",
        "volume_name": "string",
    },
    func=delete_volume,
)

clone_volume_tool = Tool(
    name="netapp_clone_volume",
    description="Create a flexclone from an existing volume.",
    args_schema={
        "cluster": "string",
        "api_user": "string",
        "api_pass": "string",
        "source_svm": "string",
        "source_vol": "string",
        "clone_name": "string",
    },
    func=clone_volume,
)

__all__ = [
    "list_volumes_tool",
    "create_volume_tool",
    "patch_volume_tool",
    "delete_volume_tool",
    "clone_volume_tool",
]
