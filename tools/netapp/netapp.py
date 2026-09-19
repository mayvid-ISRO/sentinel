import logging
from typing import Optional, List, Dict, Any
from netapp_ontap import HostConnection
from netapp_ontap.resources import Volume, Svm, Aggregate, Qtree, CifsShare, QuotaRule
from netapp_ontap.error import NetAppRestError
from tools.base import Tool

log = logging.getLogger(__name__)


# -----------------------------
# 🔐 Connection helper — no global state
# -----------------------------
def _make_connection(cluster: str, api_user: str, api_pass: str, verify: bool = False) -> HostConnection:
    """
    Create a new HostConnection. Not cached — safe for concurrent use.
    """
    return HostConnection(cluster, username=api_user, password=api_pass, verify=verify)


# -----------------------------
# 🔍 Helpers for auto-resolution
# -----------------------------
def _list_aggregates(conn: HostConnection) -> List[str]:
    """Return list of aggregate names. Returns [] if not authorized (non-breaking)."""
    try:
        return [agg.name for agg in Aggregate.get_collection(connection=conn)]
    except NetAppRestError as e:
        log.warning("Failed to list aggregates (likely missing cluster perms): %s", e)
        return []


def _list_svms(conn: HostConnection) -> List[Dict[str, Any]]:
    """Return list of SVMs with name & uuid."""
    try:
        return [
            {"name": svm.name, "uuid": svm.uuid}
            for svm in Svm.get_collection(connection=conn, fields="name,uuid")
        ]
    except NetAppRestError as e:
        log.error("Failed to list SVMs: %s", e)
        return []


def _get_svm_by_name(conn: HostConnection, svm_name: str) -> Optional[Dict[str, Any]]:
    """Get SVM info by name. Returns None if not found."""
    svms = _list_svms(conn)
    for svm in svms:
        if svm["name"] == svm_name:
            return svm
    return None


# -----------------------------
# 🛠️ Tool Functions — all return structured dict
# -----------------------------
def list_volumes(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """
    List volumes. If SVM name is missing, lists ALL volumes across all SVMs.
    """
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        kwargs = {}
        if svm_name:
            kwargs["svm.name"] = svm_name
        volumes = Volume.get_collection(
            connection=conn,
            **kwargs,
            fields="name,uuid,size,svm.name,type"
        )
        data = [
            {
                "name": vol.name,
                "uuid": vol.uuid,
                "size_mb": vol.size // (1024 * 1024) if vol.size else None,
                "svm": vol.svm.name if vol.svm else None,
                "type": vol.type or "unknown",
            }
            for vol in volumes
        ]
        return {"status": "success", "volumes": data}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}


def create_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    aggregate_name: Optional[str] = None,
    volume_name: Optional[str] = None,
    size_mb: int = 1024,  # default 1GB
    volume_type: str = "rw",
    style: str = "flexvol",
    verify: bool = False,
) -> Dict[str, Any]:
    """
    Create a volume with fallbacks:
      - If SVM/aggregate missing: auto-resolve
      - If still missing: return prompt_required (agent asks user)
    """
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)

    # Step 1: Resolve SVM
    if not svm_name:
        svms = _list_svms(conn)
        if not svms:
            return {
                "status": "prompt_required",
                "prompt": "No SVMs found. Please provide the SVM name (e.g., 'itnd').",
                "missing_fields": ["svm_name"]
            }
        svm_name = svms[0]["name"]
        log.info("Auto-selected SVM: %s", svm_name)

    # Step 2: Resolve aggregate (only after SVM known)
    if not aggregate_name:
        aggregates = _list_aggregates(conn)
        if not aggregates:
            return {
                "status": "prompt_required",
                "prompt": f"SVM '{svm_name}' has no accessible aggregates (likely due to permission). "
                          "Please provide the aggregate name manually.",
                "missing_fields": ["aggregate_name"],
                "suggested_svm": svm_name,
            }
        aggregate_name = aggregates[0]
        log.info("Auto-selected aggregate: %s", aggregate_name)

    # Step 3: Validate required name
    if not volume_name:
        return {
            "status": "prompt_required",
            "prompt": "Volume name is required. Please provide it (e.g., 'vol_backup').",
            "missing_fields": ["volume_name"],
            "suggested_svm": svm_name,
            "suggested_aggregate": aggregate_name,
        }

    # Step 4: Create volume
    try:
        vol = Volume(
            svm={"name": svm_name},
            aggregates=[{"name": aggregate_name}],
            name=volume_name,
            size=size_mb * 1024 * 1024,
            type=volume_type,
            style=style,
            connection=conn,
        )
        if vol.post(poll=True):
            return {
                "status": "success",
                "message": f"Volume '{volume_name}' created on SVM '{svm_name}'.",
                "volume": {
                    "name": vol.name,
                    "uuid": vol.uuid,
                    "size_mb": size_mb
                }
            }
        else:
            return {"status": "error", "message": "Volume creation request returned failure."}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        log.exception("Unexpected error in create_volume")
        return {"status": "error", "message": f"Unexpected error: {e}"}


def delete_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    volume_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Delete a volume by name."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        vol = Volume.find(name=volume_name, connection=conn)
        if not vol:
            return {"status": "error", "message": f"Volume '{volume_name}' not found."}
        if vol.delete(poll=True):
            return {"status": "success", "message": f"Volume '{volume_name}' deleted."}
        else:
            return {"status": "error", "message": "Delete request failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def patch_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    volume_name: str,
    new_name: Optional[str] = None,
    new_size_mb: Optional[int] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """Update volume name and/or size."""
    if not new_name and new_size_mb is None:
        return {"status": "error", "message": "At least one of 'new_name' or 'new_size_mb' must be provided."}

    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        vol = Volume.find(name=volume_name, connection=conn)
        if not vol:
            return {"status": "error", "message": f"Volume '{volume_name}' not found."}

        if new_name:
            vol.name = new_name
        if new_size_mb is not None:
            vol.size = new_size_mb * 1024 * 1024

        if vol.patch(poll=True):
            return {
                "status": "success",
                "message": f"Volume '{volume_name}' updated successfully.",
                "updated": {
                    "name": vol.name,
                    "size_mb": vol.size // (1024 * 1024) if vol.size else None
                }
            }
        else:
            return {"status": "error", "message": "Patch request failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


# -----------------------------
# 🧩 Register Tools (for your agent)

# ---------------------------------------------------------------------
# New NetApp helper functions (Qtree, Quota, CIFS share, etc.)
# ---------------------------------------------------------------------

def list_qtrees(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """Return a list of Qtrees (optionally limited to an SVM)."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        kwargs = {}
        if svm_name:
            kwargs["svm.name"] = svm_name
        qtrees = Qtree.get_collection(connection=conn, **kwargs, fields="name,svm.name,path")
        data = [
            {"name": q.name, "svm": q.svm.name if q.svm else None, "path": q.path}
            for q in qtrees
        ]
        return {"status": "success", "qtrees": data}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}


def create_qtree(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    volume_name: str,
    qtree_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Create a Qtree under a given volume."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        # q = Qtree.from_dict(
        #     svm={"name": svm_name},
        #     volume={"name": volume_name},
        #     name=qtree_name,
        #     connection=conn,
        # )
        q = Qtree.from_dict({
            'name': qtree_name,
            'volume.name': volume_name,
            'svm.name': svm_name
        })

        q.set_connection(conn)
        print(q.get_connection())

        if q.post(poll=True):
            return {"status": "success", "message": f"Qtree '{qtree_name}' created."}
        else:
            return {"status": "error", "message": "Qtree creation failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}


def delete_qtree(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    volume_name: str,
    qtree_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Delete a Qtree by name."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        q = Qtree.find(
            svm={"name": svm_name},
            volume={"name": volume_name},
            name=qtree_name,
            connection=conn,
        )
        if not q:
            return {"status": "error", "message": f"Qtree '{qtree_name}' not found."}
        if q.delete(poll=True):
            return {"status": "success", "message": f"Qtree '{qtree_name}' deleted."}
        else:
            return {"status": "error", "message": "Delete request failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}


def create_quota(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    volume_name: str,
    qtree_name: Optional[str] = None,
    size_mb: int = 1024,
    soft_limit_mb: Optional[int] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """Create a hard‑limit (and optional soft‑limit) quota on a volume or Qtree.

    ``size_mb`` is the hard‑limit in MiB. ``soft_limit_mb`` (if provided) sets a soft‑limit that the system will warn about but not enforce.
    """
    # Basic validation
    if size_mb <= 0:
        return {"status": "error", "message": "size_mb must be a positive integer"}
    if not svm_name or not volume_name:
        return {"status": "error", "message": "Both svm_name and volume_name are required"}

    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        quota_kwargs: Dict[str, Any] = {
            "svm": {"name": svm_name},
            "volume": {"name": volume_name},
            "hard_limit": size_mb * 1024 * 1024,
            "type": "tree",
            "enabled": True,
            "connection": conn,
        }
        if qtree_name:
            quota_kwargs["qtree"] = {"name": qtree_name}
        if soft_limit_mb is not None:
            quota_kwargs["soft_limit"] = soft_limit_mb * 1024 * 1024

        quota_rule = QuotaRule(**quota_kwargs)
        if quota_rule.post(poll=True):
            target = f"Qtree '{qtree_name}'" if qtree_name else f"Volume '{volume_name}'"
            result = {
                "status": "success",
                "message": f"Quota of {size_mb}\u202fMiB created on {target}.",
                "quota": {
                    "svm": svm_name,
                    "volume": volume_name,
                    "qtree": qtree_name,
                    "hard_limit_mb": size_mb,
                },
            }
            if soft_limit_mb is not None:
                result["quota"]["soft_limit_mb"] = soft_limit_mb
            return result
        else:
            return {"status": "error", "message": "Quota creation request failed"}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}
    """Create a hard‑limit quota on a volume or Qtree.

    If ``qtree_name`` is omitted the quota applies to the volume.
    """
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        quota = Quota(
            svm={"name": svm_name},
            volume={"name": volume_name},
            **({"qtree": {"name": qtree_name}} if qtree_name else {}),
            hard_limit=size_mb * 1024 * 1024,
            type="tree",
            enabled=True,
            connection=conn,
        )
        if quota.post(poll=True):
            target = f"Qtree '{qtree_name}'" if qtree_name else f"Volume '{volume_name}'"
            return {"status": "success", "message": f"Quota of {size_mb} MiB created on {target}."}
        else:
            return {"status": "error", "message": "Quota creation failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}


def create_cifs_share(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    share_name: str,
    path: str,
    comment: str = "",
    verify: bool = False,
) -> Dict[str, Any]:
    """Create a CIFS (SMB) share pointing at a path (volume/qtree)."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        share = CifsShare(
            svm={"name": svm_name},
            name=share_name,
            path=path,
            comment=comment,
            enabled=True,
            connection=conn,
        )
        if share.post(poll=True):
            return {"status": "success", "message": f"CIFS share '{share_name}' created at {path}."}
        else:
            return {"status": "error", "message": "CIFS share creation failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e)}

# -----------------------------
# 🧩 Register Tools (for your agent)
# -----------------------------
list_volumes_tool = Tool(
    name="netapp_list_volumes",
    description="List NetApp volumes. Optionally filter by SVM name. Returns structured volume data.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: list_volumes(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_volume_tool = Tool(
    name="netapp_create_volume",
    description=(
        "Create a NetApp volume with auto-resolution for SVM and aggregate. "
        "If required info is missing, returns a prompt for user input."
    ),
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
        "aggregate_name": "string (optional)",
        "volume_name": "string (optional)",
        "size_mb": "integer (optional, default: 1024)",
        "volume_type": "string (optional, default: 'rw')",
        "style": "string (optional, default: 'flexvol')",
    },
    func=lambda **kw: create_volume(
        **{k: v for k, v in kw.items() if k not in {"verify"}}
    ),
)

delete_volume_tool = Tool(
    name="netapp_delete_volume",
    description="Delete a NetApp volume by name.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "volume_name": "string (required)",
    },
    func=lambda **kw: delete_volume(**kw),
)

patch_volume_tool = Tool(
    name="netapp_patch_volume",
    description="Update a volume's name and/or size.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "volume_name": "string (required)",
        "new_name": "string (optional)",
        "new_size_mb": "integer (optional)",
    },
    func=lambda **kw: patch_volume(**kw),
)

# -----------------------------
# 🧩 Register Tools (for your agent)

list_qtrees_tool = Tool(
    name="netapp_list_qtrees",
    description="List Qtrees. Optional filter by SVM name.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: list_qtrees(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_qtree_tool = Tool(
    name="netapp_create_qtree",
    description="Create a Qtree under a given volume.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "volume_name": "string (required)",
        "qtree_name": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: create_qtree(**{k: v for k, v in kw.items() if k != "verify"}),
)

delete_qtree_tool = Tool(
    name="netapp_delete_qtree",
    description="Delete a Qtree by name.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "volume_name": "string (required)",
        "qtree_name": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: delete_qtree(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_quota_tool = Tool(
    name="netapp_create_quota",
    description="Create a hard‑limit quota on a volume or Qtree.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "volume_name": "string (required)",
        "qtree_name": "string (optional)",
        "size_mb": "integer (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: create_quota(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_cifs_share_tool = Tool(
    name="netapp_create_cifs_share",
    description="Create a CIFS (SMB) share pointing at a path (volume/qtree).",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "share_name": "string (required)",
        "path": "string (required) – e.g. /vol/vol1/qtree1",
        "comment": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: create_cifs_share(**{k: v for k, v in kw.items() if k != "verify"}),
)

# -----------------------------
# 🔐 Credential auto-fill (Phase 0)
# -----------------------------
# Every netapp_* tool is wrapped below. The wrapper:
#   1. Saves credentials to the store (tools/credentials.py) the first time
#      the LLM passes them, so the user never re-enters them.
#   2. Auto-fills them on later calls when the LLM omits them (its context
#      may have been trimmed), instead of failing with missing args.
# Files that rely on this: tools/__init__.py (registration), tools/credentials.py.

_CONNECTION_KEYS = ("cluster", "api_user", "api_pass")

def _wrap_netapp_tool(tool: Tool) -> Tool:
    """Return a Tool whose func auto-fills/saves connection credentials."""
    def wrapped(**kwargs):
        try:
            import tools.credentials as cred
        except ImportError:
            return tool.func(**kwargs)

        stored = cred.load("netapp")

        # Save any freshly supplied credentials (per-key merge).
        fresh = {k: kwargs[k] for k in _CONNECTION_KEYS if kwargs.get(k)}
        if all(fresh.get(k) for k in _CONNECTION_KEYS):
            cred.save("netapp", fresh)
        else:
            # Fill missing keys from the store.
            for k in _CONNECTION_KEYS:
                if not kwargs.get(k) and stored.get(k):
                    kwargs[k] = stored[k]

        missing = [k for k in _CONNECTION_KEYS if not kwargs.get(k)]
        if missing:
            return {
                "status": "prompt_required",
                "prompt": f"Missing NetApp connection info: {', '.join(missing)}. "
                          "Ask the user for the cluster address, username and password, "
                          "then call netapp_save_credentials first.",
                "missing_fields": missing,
            }
        return tool.func(**kwargs)

    return Tool(
        name=tool.name,
        description=tool.description
        + " Connection credentials are optional if already saved in this session "
          "(call netapp_save_credentials once).",
        args_schema={
            **{k: "string (optional if saved)" for k in _CONNECTION_KEYS},
            **{k: v for k, v in tool.args_schema.items()
               if k not in _CONNECTION_KEYS},
        },
        func=wrapped,
    )


def save_netapp_credentials(cluster: str, api_user: str, api_pass: str,
                            svm_name: Optional[str] = None) -> Dict[str, Any]:
    """Persist NetApp connection info so subsequent tools don't need it."""
    try:
        import tools.credentials as cred
        data = {"cluster": cluster, "api_user": api_user, "api_pass": api_pass}
        if svm_name:
            data["svm_name"] = svm_name
        cred.save("netapp", data)
        return {"status": "success",
                "message": "NetApp credentials saved for this session "
                           "(stored under .iris/credentials.json)."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def netapp_status() -> Dict[str, Any]:
    """Report whether NetApp credentials are stored (never the values)."""
    try:
        import tools.credentials as cred
        stored = cred.load("netapp")
        return {
            "status": "success",
            "credentials_saved": bool(all(stored.get(k) for k in _CONNECTION_KEYS)),
            "cluster": stored.get("cluster"),  # host address is not a secret
            "svm_name": stored.get("svm_name"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


save_netapp_credentials_tool = Tool(
    name="netapp_save_credentials",
    description="Save NetApp cluster connection credentials (cluster address, "
                "username, password, optional default SVM) so all other netapp_* "
                "tools can be called without repeating them.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
    },
    func=save_netapp_credentials,
)

netapp_status_tool = Tool(
    name="netapp_status",
    description="Check whether NetApp credentials are already saved for this "
                "session. Never returns the password.",
    args_schema={},
    func=netapp_status,
)

# Wrap every operational tool so credential auto-fill applies uniformly.
list_volumes_tool = _wrap_netapp_tool(list_volumes_tool)
create_volume_tool = _wrap_netapp_tool(create_volume_tool)
delete_volume_tool = _wrap_netapp_tool(delete_volume_tool)
patch_volume_tool = _wrap_netapp_tool(patch_volume_tool)
list_qtrees_tool = _wrap_netapp_tool(list_qtrees_tool)
create_qtree_tool = _wrap_netapp_tool(create_qtree_tool)
delete_qtree_tool = _wrap_netapp_tool(delete_qtree_tool)
create_quota_tool = _wrap_netapp_tool(create_quota_tool)
create_cifs_share_tool = _wrap_netapp_tool(create_cifs_share_tool)

# Export for tools/__init__.py
__all__ = [
    "list_volumes_tool",
    "create_volume_tool",
    "delete_volume_tool",
    "patch_volume_tool",
    "list_qtrees_tool",
    "create_qtree_tool",
    "delete_qtree_tool",
    "create_quota_tool",
    "create_cifs_share_tool",
    "save_netapp_credentials_tool",
    "netapp_status_tool",
]