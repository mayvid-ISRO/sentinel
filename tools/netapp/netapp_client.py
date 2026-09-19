"""
NetApp ONTAP Integration - Authoritative Module

This module provides comprehensive CRUD operations for NetApp ONTAP storage resources:
- Volumes (create, delete, patch, clone, resize, offline, online)
- Qtrees (create, delete, list, modify ownership)
- Quotas (list, create, delete, modify)
- CIFS Shares (create, delete, list, modify)
- NFS Exports (create, delete, list)
- iGroups (create, delete, list, add initiators)
- LUNs (create, delete, list, map)
- SVMs (list, get details)
- Aggregates (list, get details)
- Cluster health monitoring
"""

import logging
from typing import Optional, List, Dict, Any
from netapp_ontap import HostConnection
from netapp_ontap.resources import (
    Volume, Svm, Aggregate, Qtree, CifsShare, QuotaRule, Igroup, IgroupInitiator, Disk, IpInterface, Lun, NfsExport
)
from netapp_ontap.error import NetAppRestError

log = logging.getLogger(__name__)


# ===== CONNECTION HELPER =====

def _make_connection(cluster: str, api_user: str, api_pass: str, verify: bool = False) -> HostConnection:
    """
    Create a new HostConnection. Not cached - safe for concurrent use.

    Args:
        cluster: NetApp cluster hostname or IP
        api_user: API username
        api_pass: API password
        verify: Verify SSL certificates (default False for self-signed certs)

    Returns:
        HostConnection instance
    """
    return HostConnection(cluster, username=api_user, password=api_pass, verify=verify)


# ===== HELPER FUNCTIONS =====

def _list_aggregates(conn: HostConnection) -> List[str]:
    """Return list of aggregate names. Returns [] if not authorized."""
    try:
        return [agg.name for agg in Aggregate.get_collection(connection=conn)]
    except NetAppRestError as e:
        log.warning("Failed to list aggregates: %s", e)
        return []


def _list_svms(conn: HostConnection) -> List[Dict[str, Any]]:
    """Return list of SVMs with name and uuid."""
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


def _auto_resolve_svm(conn: HostConnection) -> Optional[str]:
    """Auto-resolve SVM name. Returns None if no SVMs available."""
    svms = _list_svms(conn)
    if svms:
        log.info("Auto-selected SVM: %s", svms[0]["name"])
        return svms[0]["name"]
    return None


def _auto_resolve_aggregate(conn: HostConnection, svm_name: str) -> Optional[str]:
    """Auto-resolve aggregate for SVM. Returns None if no aggregates available."""
    aggregates = _list_aggregates(conn)
    if aggregates:
        log.info("Auto-selected aggregate: %s", aggregates[0])
        return aggregates[0]
    return None


def _format_volume_info(vol: Volume) -> Dict[str, Any]:
    """Format Volume object to dictionary."""
    return {
        "name": vol.name,
        "uuid": vol.uuid,
        "size_mb": vol.size // (1024 * 1024) if vol.size else None,
        "svm": vol.svm.name if vol.svm else None,
        "type": vol.type or "unknown",
        "style": vol.style or "unknown",
        "state": vol.state or "unknown",
        "flexclone": getattr(vol, "clone", {}).get("is_flexclone", False) if hasattr(vol, "clone") else False
    }


# ===== VOLUME OPERATIONS =====

def netapp_list_volumes(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """
    List volumes. If SVM name is missing, lists ALL volumes across all SVMs.

    Args:
        cluster: NetApp cluster hostname or IP
        api_user: API username
        api_pass: API password
        svm_name: Optional SVM name filter
        verify: Verify SSL certificates

    Returns:
        Dict with status and volumes list
    """
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        kwargs = {}
        if svm_name:
            kwargs["svm.name"] = svm_name
        volumes = Volume.get_collection(
            connection=conn,
            **kwargs,
            fields="name,uuid,size,svm.name,type,style,state,clone"
        )
        data = [_format_volume_info(vol) for vol in volumes]
        return {"status": "success", "volumes": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": str(e), "count": 0}


def netapp_create_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    aggregate_name: Optional[str] = None,
    volume_name: Optional[str] = None,
    size_mb: int = 1024,
    volume_type: str = "rw",
    style: str = "flexvol",
    verify: bool = False,
) -> Dict[str, Any]:
    """
    Create a NetApp volume with auto-resolution for SVM and aggregate.

    Args:
        cluster: NetApp cluster hostname or IP
        api_user: API username
        api_pass: API password
        svm_name: SVM name (auto-resolved if not provided)
        aggregate_name: Aggregate name (auto-resolved if not provided)
        volume_name: Volume name (required, prompts if not provided)
        size_mb: Volume size in MB (default 1024)
        volume_type: Volume type 'rw' or 'dp' (default 'rw')
        style: Volume style 'flexvol' or 'flexgroup' (default 'flexvol')
        verify: Verify SSL certificates

    Returns:
        Dict with status and result message
    """
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)

    # Auto-resolve SVM
    if not svm_name:
        svm_name = _auto_resolve_svm(conn)
        if not svm_name:
            return {
                "status": "prompt_required",
                "prompt": "No SVMs found. Please provide the SVM name.",
                "missing_fields": ["svm_name"]
            }

    # Auto-resolve aggregate
    if not aggregate_name:
        aggregate_name = _auto_resolve_aggregate(conn, svm_name)
        if not aggregate_name:
            return {
                "status": "prompt_required",
                "prompt": f"SVM '{svm_name}' has no accessible aggregates. Please provide aggregate name.",
                "missing_fields": ["aggregate_name"],
                "suggested_svm": svm_name,
            }

    # Check if volume name provided
    if not volume_name:
        return {
            "status": "prompt_required",
            "prompt": "Volume name is required. Please provide it (e.g., 'vol_backup').",
            "missing_fields": ["volume_name"],
            "suggested_svm": svm_name,
            "suggested_aggregate": aggregate_name,
        }

    # Create volume
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
                "message": f"Volume '{volume_name}' created on SVM '{svm_name}' ({size_mb}MB).",
                "volume": {
                    "name": vol.name,
                    "uuid": vol.uuid,
                    "size_mb": size_mb,
                    "svm": svm_name,
                }
            }
        else:
            return {"status": "error", "message": "Volume creation request returned failure."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        log.exception("Unexpected error in create_volume")
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_delete_volume(
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
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_patch_volume(
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
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_clone_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    source_svm: str,
    source_vol: str,
    clone_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """
    Create a flexclone from an existing volume.

    Args:
        cluster: NetApp cluster hostname or IP
        api_user: API username
        api_pass: API password
        source_svm: Source volume SVM name
        source_vol: Source volume name
        clone_name: Name for the clone
        verify: Verify SSL certificates

    Returns:
        Dict with status and result message
    """
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        # Get source volume info
        source_volume = Volume.find(
            name=source_vol,
            connection=conn,
            params={"svm.name": source_svm}
        )
        if not source_volume:
            return {"status": "error", "message": f"Source volume '{source_vol}' not found in SVM '{source_svm}'."}

        payload = {
            "svm": {"uuid": source_volume.svm.uuid},
            "name": clone_name,
            "clone": {
                "is_flexclone": True,
                "parent_svm": {"uuid": source_volume.svm.uuid},
                "parent_volume": {"uuid": source_volume.uuid},
            },
        }
        clone = Volume.from_dict(payload, connection=conn)
        if clone.post(poll=True):
            return {
                "status": "success",
                "message": f"Clone '{clone_name}' created from '{source_vol}'.",
                "clone": {
                    "name": clone.name,
                    "uuid": clone.uuid,
                    "parent": source_vol
                }
            }
        else:
            return {"status": "error", "message": "Clone creation request failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_resize_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    volume_name: str,
    new_size_mb: int,
    verify: bool = False,
) -> Dict[str, Any]:
    """Resize a volume (grow only, ONTAP limitation)."""
    return netapp_patch_volume(
        cluster=cluster,
        api_user=api_user,
        api_pass=api_pass,
        volume_name=volume_name,
        new_size_mb=new_size_mb,
        verify=verify
    )


def netapp_offline_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    volume_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Take a volume offline."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        vol = Volume.find(name=volume_name, connection=conn)
        if not vol:
            return {"status": "error", "message": f"Volume '{volume_name}' not found."}

        original_state = vol.state
        vol.state = "offline"
        if vol.patch(poll=True):
            return {
                "status": "success",
                "message": f"Volume '{volume_name}' taken offline (was '{original_state}')."
            }
        else:
            return {"status": "error", "message": "Failed to take volume offline."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_online_volume(
    cluster: str,
    api_user: str,
    api_pass: str,
    volume_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Bring a volume online."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        vol = Volume.find(name=volume_name, connection=conn)
        if not vol:
            return {"status": "error", "message": f"Volume '{volume_name}' not found."}

        original_state = vol.state
        vol.state = "online"
        if vol.patch(poll=True):
            return {
                "status": "success",
                "message": f"Volume '{volume_name}' brought online (was '{original_state}')."
            }
        else:
            return {"status": "error", "message": "Failed to bring volume online."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


# ===== QTREE OPERATIONS =====

def netapp_list_qtrees(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    volume_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """
    List Qtrees. Optionally filter by SVM and/or volume.

    Args:
        cluster: NetApp cluster hostname or IP
        api_user: API username
        api_pass: API password
        svm_name: Optional SVM name filter
        volume_name: Optional volume name filter
        verify: Verify SSL certificates

    Returns:
        Dict with status and qtrees list
    """
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        kwargs = {}
        if svm_name:
            kwargs["svm.name"] = svm_name
        if volume_name:
            kwargs["volume.name"] = volume_name

        qtrees = Qtree.get_collection(connection=conn, **kwargs, fields="name,svm.name,volume.name,path,owner")
        data = [
            {
                "name": q.name,
                "svm": q.svm.name if q.svm else None,
                "volume": q.volume.name if q.volume else None,
                "path": q.path,
                "owner": q.owner
            }
            for q in qtrees
        ]
        return {"status": "success", "qtrees": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}", "count": 0}


def netapp_create_qtree(
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
        # Verify volume exists
        vol = Volume.find(name=volume_name, connection=conn, params={"svm.name": svm_name})
        if not vol:
            return {"status": "error", "message": f"Volume '{volume_name}' not found in SVM '{svm_name}'."}

        q = Qtree.from_dict({
            'name': qtree_name,
            'volume.name': volume_name,
            'svm.name': svm_name
        })
        q.set_connection(conn)

        if q.post(poll=True):
            return {
                "status": "success",
                "message": f"Qtree '{qtree_name}' created in volume '{volume_name}'.",
                "qtree": {
                    "name": q.name,
                    "volume": volume_name,
                    "svm": svm_name
                }
            }
        else:
            return {"status": "error", "message": "Qtree creation failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_delete_qtree(
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
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_qtree_chown(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    volume_name: str,
    qtree_name: str,
    new_owner: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Change qtree ownership."""
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

        q.owner = new_owner
        if q.patch(poll=True):
            return {
                "status": "success",
                "message": f"Qtree '{qtree_name}' ownership changed to '{new_owner}'.",
                "qtree": {"name": q.name, "owner": q.owner}
            }
        else:
            return {"status": "error", "message": "Ownership change failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


# ===== QUOTA OPERATIONS =====

def netapp_list_quotas(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    volume_name: Optional[str] = None,
    qtree_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """List quotas. Optionally filter by SVM, volume, or qtree."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        kwargs = {}
        if svm_name:
            kwargs["svm.name"] = svm_name
        if volume_name:
            kwargs["volume.name"] = volume_name
        if qtree_name:
            kwargs["qtree.name"] = qtree_name

        quotas = QuotaRule.get_collection(connection=conn, **kwargs,
                                          fields="svm.name,volume.name,qtree.name,hard_limit,soft_limit,type,enabled")
        data = [
            {
                "svm": q.svm.name if q.svm else None,
                "volume": q.volume.name if q.volume else None,
                "qtree": q.qtree.name if q.qtree else None,
                "hard_limit_mb": q.hard_limit // (1024 * 1024) if q.hard_limit else None,
                "soft_limit_mb": q.soft_limit // (1024 * 1024) if q.soft_limit else None,
                "type": q.type,
                "enabled": q.enabled
            }
            for q in quotas
        ]
        return {"status": "success", "quotas": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}", "count": 0}


def netapp_create_quota(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    volume_name: str,
    qtree_name: Optional[str] = None,
    hard_limit_mb: int = 1024,
    soft_limit_mb: Optional[int] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """
    Create a hard-limit (and optional soft-limit) quota on a volume or qtree.

    Args:
        cluster: NetApp cluster hostname or IP
        api_user: API username
        api_pass: API password
        svm_name: SVM name (required)
        volume_name: Volume name (required)
        qtree_name: Optional qtree name. If omitted, quota applies to volume.
        hard_limit_mb: Hard limit in MB (required, default 1024)
        soft_limit_mb: Optional soft limit in MB
        verify: Verify SSL certificates

    Returns:
        Dict with status and result message
    """
    if not svm_name or not volume_name:
        return {"status": "error", "message": "Both svm_name and volume_name are required."}
    if hard_limit_mb <= 0:
        return {"status": "error", "message": "hard_limit_mb must be a positive integer."}

    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        quota_kwargs = {
            "svm": {"name": svm_name},
            "volume": {"name": volume_name},
            "hard_limit": hard_limit_mb * 1024 * 1024,
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
                "message": f"Quota of {hard_limit_mb}MB hard limit created on {target}.",
                "quota": {
                    "svm": svm_name,
                    "volume": volume_name,
                    "qtree": qtree_name,
                    "hard_limit_mb": hard_limit_mb,
                },
            }
            if soft_limit_mb is not None:
                result["quota"]["soft_limit_mb"] = soft_limit_mb
            return result
        else:
            return {"status": "error", "message": "Quota creation request failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_delete_quota(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    volume_name: str,
    qtree_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """Delete a quota by volume (and optional qtree)."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        # Find quota by volume (and qtree if specified)
        quotas = QuotaRule.get_collection(
            connection=conn,
            **{"svm.name": svm_name, "volume.name": volume_name}
        )

        deleted_count = 0
        for q in quotas:
            if qtree_name and q.qtree and q.qtree.name == qtree_name:
                if q.delete(poll=True):
                    deleted_count += 1
            elif not qtree_name and not q.qtree:
                if q.delete(poll=True):
                    deleted_count += 1

        if deleted_count > 0:
            return {
                "status": "success",
                "message": f"Deleted {deleted_count} quota(s) on {volume_name}" +
                          (f" qtree {qtree_name}" if qtree_name else "")
            }
        else:
            return {"status": "error", "message": "No matching quota found to delete."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


# ===== CIFS SHARE OPERATIONS =====

def netapp_list_cifs_shares(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """List CIFS shares. Optionally filter by SVM."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        kwargs = {}
        if svm_name:
            kwargs["svm.name"] = svm_name

        shares = CifsShare.get_collection(connection=conn, **kwargs,
                                          fields="svm.name,name,path,comment,enabled")
        data = [
            {
                "svm": s.svm.name if s.svm else None,
                "name": s.name,
                "path": s.path,
                "comment": s.comment,
                "enabled": s.enabled
            }
            for s in shares
        ]
        return {"status": "success", "shares": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}", "count": 0}


def netapp_create_cifs_share(
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
            return {
                "status": "success",
                "message": f"CIFS share '{share_name}' created at {path}.",
                "share": {
                    "name": share.name,
                    "path": share.path,
                    "comment": share.comment,
                    "svm": svm_name
                }
            }
        else:
            return {"status": "error", "message": "CIFS share creation failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_delete_cifs_share(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    share_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Delete a CIFS share by name."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        share = CifsShare.find(name=share_name, connection=conn, params={"svm.name": svm_name})
        if not share:
            return {"status": "error", "message": f"CIFS share '{share_name}' not found."}
        if share.delete(poll=True):
            return {"status": "success", "message": f"CIFS share '{share_name}' deleted."}
        else:
            return {"status": "error", "message": "Delete request failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


# ===== NFS EXPORT OPERATIONS =====

def netapp_list_nfs_exports(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """List NFS exports. Optionally filter by SVM."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        kwargs = {}
        if svm_name:
            kwargs["svm.name"] = svm_name

        exports = NfsExport.get_collection(connection=conn, **kwargs,
                                           fields="svm.name,path,export_policy")
        data = [
            {
                "svm": e.svm.name if e.svm else None,
                "path": e.path,
                "export_policy": e.export_policy.name if e.export_policy else None
            }
            for e in exports
        ]
        return {"status": "success", "exports": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}", "count": 0}


def netapp_create_nfs_export(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    path: str,
    export_policy: str = "default",
    verify: bool = False,
) -> Dict[str, Any]:
    """Create an NFS export."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        export = NfsExport(
            svm={"name": svm_name},
            path=path,
            export_policy={"name": export_policy},
            connection=conn,
        )
        if export.post(poll=True):
            return {
                "status": "success",
                "message": f"NFS export '{path}' created with policy '{export_policy}'.",
                "export": {
                    "path": export.path,
                    "export_policy": export_policy,
                    "svm": svm_name
                }
            }
        else:
            return {"status": "error", "message": "NFS export creation failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


# ===== IGROUP OPERATIONS =====

def netapp_list_igroups(
    cluster: str,
    api_user: str,
    api_pass: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """List all iGroups."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        igroups = Igroup.get_collection(connection=conn, fields="name,protocol,os_type,initiators")
        data = [
            {
                "name": ig.name,
                "protocol": ig.protocol,
                "os_type": ig.os_type,
                "initiators": [init.name for init in ig.initiators] if ig.initiators else []
            }
            for ig in igroups
        ]
        return {"status": "success", "igroups": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}", "count": 0}


def netapp_create_igroup(
    cluster: str,
    api_user: str,
    api_pass: str,
    igroup_name: str,
    protocol: str = "fcp",  # 'fcp' or 'iscsi'
    os_type: str = "linux",
    verify: bool = False,
) -> Dict[str, Any]:
    """Create an iGroup."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        ig = Igroup(
            name=igroup_name,
            protocol=protocol,
            os_type=os_type,
            connection=conn,
        )
        if ig.post(poll=True):
            return {
                "status": "success",
                "message": f"iGroup '{igroup_name}' created with protocol '{protocol}'.",
                "igroup": {
                    "name": ig.name,
                    "protocol": ig.protocol,
                    "os_type": ig.os_type
                }
            }
        else:
            return {"status": "error", "message": "iGroup creation failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


def netapp_add_igroup_initiator(
    cluster: str,
    api_user: str,
    api_pass: str,
    igroup_name: str,
    initiator_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Add an initiator to an iGroup."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        ig = Igroup.find(name=igroup_name, connection=conn)
        if not ig:
            return {"status": "error", "message": f"iGroup '{igroup_name}' not found."}

        initiator = IgroupInitiator.from_dict({"name": initiator_name})
        ig.initiators.append(initiator)

        if ig.patch(poll=True):
            return {
                "status": "success",
                "message": f"Initiator '{initiator_name}' added to iGroup '{igroup_name}'.",
                "igroup": {"name": ig.name, "initiators": [init.name for init in ig.initiators]}
            }
        else:
            return {"status": "error", "message": "Failed to add initiator."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


# ===== LUN OPERATIONS =====

def netapp_list_luns(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: Optional[str] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    """List all LUNs. Optionally filter by SVM."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        kwargs = {}
        if svm_name:
            kwargs["svm.name"] = svm_name

        luns = Lun.get_collection(connection=conn, **kwargs,
                                  fields="svm.name,name,path,uuid,size,online,map_state")
        data = [
            {
                "svm": l.svm.name if l.svm else None,
                "name": l.name,
                "path": l.path,
                "uuid": l.uuid,
                "size_mb": l.size // (1024 * 1024) if l.size else None,
                "online": l.online,
                "map_state": l.map_state
            }
            for l in luns
        ]
        return {"status": "success", "luns": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}", "count": 0}


def netapp_create_lun(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    volume_name: str,
    lun_name: str,
    size_mb: int,
    os_type: str = "linux",
    verify: bool = False,
) -> Dict[str, Any]:
    """Create a LUN in a volume."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        # Get volume to use its path
        vol = Volume.find(name=volume_name, connection=conn, params={"svm.name": svm_name})
        if not vol:
            return {"status": "error", "message": f"Volume '{volume_name}' not found in SVM '{svm_name}'."}

        path = f"/vol/{volume_name}/{lun_name}"

        lun = Lun(
            svm={"name": svm_name},
            name=lun_name,
            path=path,
            size=size_mb * 1024 * 1024,
            os_type=os_type,
            connection=conn,
        )
        if lun.post(poll=True):
            return {
                "status": "success",
                "message": f"LUN '{lun_name}' created ({size_mb}MB) in volume '{volume_name}'.",
                "lun": {
                    "name": lun.name,
                    "path": lun.path,
                    "size_mb": size_mb,
                    "svm": svm_name
                }
            }
        else:
            return {"status": "error", "message": "LUN creation failed."}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {e}"}


# ===== SVM OPERATIONS =====

def netapp_list_svms(
    cluster: str,
    api_user: str,
    api_pass: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """List all SVMs with basic info."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        svms = Svm.get_collection(connection=conn, fields="name,uuid,state,service_state")
        data = [
            {
                "name": s.name,
                "uuid": s.uuid,
                "state": s.state,
                "service_state": s.service_state
            }
            for s in svms
        ]
        return {"status": "success", "svms": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}", "count": 0}


def netapp_get_svm_info(
    cluster: str,
    api_user: str,
    api_pass: str,
    svm_name: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Get detailed information about an SVM."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        svm = Svm.find(name=svm_name, connection=conn, fields="*")
        if not svm:
            return {"status": "error", "message": f"SVM '{svm_name}' not found."}

        return {
            "status": "success",
            "svm": {
                "name": svm.name,
                "uuid": svm.uuid,
                "state": svm.state,
                "service_state": svm.service_state,
                "aggregate": svm.aggregate.name if svm.aggregate else None,
            }
        }
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}


# ===== CLUSTER MONITORING =====

def netapp_list_aggregates(
    cluster: str,
    api_user: str,
    api_pass: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """List all aggregates with capacity info."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        aggregates = Aggregate.get_collection(connection=conn,
                                               fields="name,size,used,available")
        data = [
            {
                "name": agg.name,
                "size_mb": agg.size // (1024 * 1024) if agg.size else None,
                "used_mb": agg.used // (1024 * 1024) if agg.used else None,
                "available_mb": agg.available // (1024 * 1024) if agg.available else None,
                "usage_pct": round((agg.used / agg.size * 100) if agg.size else 0, 2)
            }
            for agg in aggregates
        ]
        return {"status": "success", "aggregates": data, "count": len(data)}
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}", "count": 0}


def netapp_cluster_health(
    cluster: str,
    api_user: str,
    api_pass: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Get cluster health summary."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        # Get cluster info
        clusters = Svm.get_collection(connection=conn, fields="name,state")
        cluster_info = [{"name": c.name, "state": c.state} for c in clusters]

        # Get aggregate health
        aggregates = Aggregate.get_collection(connection=conn, fields="name,used")
        aggregate_health = [{"name": a.name, "used_pct": round(a.used / a.size * 100, 2) if a.size else 0}
                           for a in aggregates]

        return {
            "status": "success",
            "cluster": {
                "name": cluster,
                "svm_count": len(cluster_info),
                "aggregate_count": len(aggregate_health),
                "aggregate_health": aggregate_health
            }
        }
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}


def netapp_capacity_report(
    cluster: str,
    api_user: str,
    api_pass: str,
    verify: bool = False,
) -> Dict[str, Any]:
    """Generate capacity report for the cluster."""
    conn = _make_connection(cluster, api_user, api_pass, verify=verify)
    try:
        # Get all volumes
        volumes = Volume.get_collection(connection=conn, fields="name,size,used,svm.name")
        total_size = sum(v.size for v in volumes if v.size)
        total_used = sum(v.used for v in volumes if v.used)

        # Get SVM info
        svms = Svm.get_collection(connection=conn, fields="name")
        svm_volume_counts = {}
        for svm in svms:
            vols = Volume.get_collection(connection=conn, **{"svm.name": svm.name})
            svm_volume_counts[svm.name] = len(vols)

        return {
            "status": "success",
            "capacity": {
                "total_size_mb": total_size // (1024 * 1024) if total_size else 0,
                "total_used_mb": total_used // (1024 * 1024) if total_used else 0,
                "utilization_pct": round(total_used / total_size * 100, 2) if total_size else 0,
            },
            "volumes_by_svm": svm_volume_counts
        }
    except NetAppRestError as e:
        return {"status": "error", "message": f"NetApp error: {e}"}


# ===== TOOLS REGISTRATION =====

# Volume Tools
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
    func=lambda **kw: netapp_list_volumes(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_volume_tool = Tool(
    name="netapp_create_volume",
    description="Create a NetApp volume with auto-resolution for SVM and aggregate. "
                "If required info is missing, returns a prompt for user input.",
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
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_create_volume(**{k: v for k, v in kw.items() if k != "verify"}),
)

delete_volume_tool = Tool(
    name="netapp_delete_volume",
    description="Delete a NetApp volume by name.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "volume_name": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_delete_volume(**{k: v for k, v in kw.items() if k != "verify"}),
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
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_patch_volume(**{k: v for k, v in kw.items() if k != "verify"}),
)

clone_volume_tool = Tool(
    name="netapp_clone_volume",
    description="Create a flexclone from an existing volume. Requires source SVM and volume.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "source_svm": "string (required)",
        "source_vol": "string (required)",
        "clone_name": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_clone_volume(**{k: v for k, v in kw.items() if k != "verify"}),
)

# Qtree Tools
list_qtrees_tool = Tool(
    name="netapp_list_qtrees",
    description="List Qtrees. Optional filter by SVM name and/or volume name.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
        "volume_name": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_list_qtrees(**{k: v for k, v in kw.items() if k != "verify"}),
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
    func=lambda **kw: netapp_create_qtree(**{k: v for k, v in kw.items() if k != "verify"}),
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
    func=lambda **kw: netapp_delete_qtree(**{k: v for k, v in kw.items() if k != "verify"}),
)

qtree_chown_tool = Tool(
    name="netapp_qtree_chown",
    description="Change ownership of a Qtree.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "volume_name": "string (required)",
        "qtree_name": "string (required)",
        "new_owner": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_qtree_chown(**{k: v for k, v in kw.items() if k != "verify"}),
)

# Quota Tools
list_quotas_tool = Tool(
    name="netapp_list_quotas",
    description="List quotas. Optionally filter by SVM, volume, or qtree.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
        "volume_name": "string (optional)",
        "qtree_name": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_list_quotas(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_quota_tool = Tool(
    name="netapp_create_quota",
    description="Create a hard-limit (and optional soft-limit) quota on a volume or qtree.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "volume_name": "string (required)",
        "qtree_name": "string (optional)",
        "hard_limit_mb": "integer (required, default: 1024)",
        "soft_limit_mb": "integer (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_create_quota(**{k: v for k, v in kw.items() if k != "verify"}),
)

delete_quota_tool = Tool(
    name="netapp_delete_quota",
    description="Delete a quota by volume (and optional qtree).",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "volume_name": "string (required)",
        "qtree_name": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_delete_quota(**{k: v for k, v in kw.items() if k != "verify"}),
)

# CIFS Tools
list_cifs_shares_tool = Tool(
    name="netapp_list_cifs_shares",
    description="List CIFS shares. Optionally filter by SVM.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_list_cifs_shares(**{k: v for k, v in kw.items() if k != "verify"}),
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
        "path": "string (required)",
        "comment": "string (optional, default: '')",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_create_cifs_share(**{k: v for k, v in kw.items() if k != "verify"}),
)

delete_cifs_share_tool = Tool(
    name="netapp_delete_cifs_share",
    description="Delete a CIFS share by name.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "share_name": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_delete_cifs_share(**{k: v for k, v in kw.items() if k != "verify"}),
)

# NFS Tools
list_nfs_exports_tool = Tool(
    name="netapp_list_nfs_exports",
    description="List NFS exports. Optionally filter by SVM.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_list_nfs_exports(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_nfs_export_tool = Tool(
    name="netapp_create_nfs_export",
    description="Create an NFS export with specified export policy.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "path": "string (required)",
        "export_policy": "string (optional, default: 'default')",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_create_nfs_export(**{k: v for k, v in kw.items() if k != "verify"}),
)

# iGroup Tools
list_igroups_tool = Tool(
    name="netapp_list_igroups",
    description="List all iGroups.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_list_igroups(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_igroup_tool = Tool(
    name="netapp_create_igroup",
    description="Create an iGroup for iSCSI or FCP.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "igroup_name": "string (required)",
        "protocol": "string (optional, default: 'fcp')",
        "os_type": "string (optional, default: 'linux')",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_create_igroup(**{k: v for k, v in kw.items() if k != "verify"}),
)

add_igroup_initiator_tool = Tool(
    name="netapp_add_igroup_initiator",
    description="Add an initiator (IQN/WWPN) to an iGroup.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "igroup_name": "string (required)",
        "initiator_name": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_add_igroup_initiator(**{k: v for k, v in kw.items() if k != "verify"}),
)

# LUN Tools
list_luns_tool = Tool(
    name="netapp_list_luns",
    description="List all LUNs. Optionally filter by SVM.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (optional)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_list_luns(**{k: v for k, v in kw.items() if k != "verify"}),
)

create_lun_tool = Tool(
    name="netapp_create_lun",
    description="Create a LUN in a volume.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "volume_name": "string (required)",
        "lun_name": "string (required)",
        "size_mb": "integer (required)",
        "os_type": "string (optional, default: 'linux')",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_create_lun(**{k: v for k, v in kw.items() if k != "verify"}),
)

# SVM Tools
list_svms_tool = Tool(
    name="netapp_list_svms",
    description="List all SVMs with basic info.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_list_svms(**{k: v for k, v in kw.items() if k != "verify"}),
)

get_svm_info_tool = Tool(
    name="netapp_get_svm_info",
    description="Get detailed information about an SVM.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "svm_name": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_get_svm_info(**{k: v for k, v in kw.items() if k != "verify"}),
)

# Cluster Monitoring Tools
list_aggregates_tool = Tool(
    name="netapp_list_aggregates",
    description="List all aggregates with capacity info.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_list_aggregates(**{k: v for k, v in kw.items() if k != "verify"}),
)

cluster_health_tool = Tool(
    name="netapp_cluster_health",
    description="Get cluster health summary including SVM and aggregate counts.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_cluster_health(**{k: v for k, v in kw.items() if k != "verify"}),
)

capacity_report_tool = Tool(
    name="netapp_capacity_report",
    description="Generate capacity report for the cluster.",
    args_schema={
        "cluster": "string (required)",
        "api_user": "string (required)",
        "api_pass": "string (required)",
        "verify": "boolean (optional, default: false)",
    },
    func=lambda **kw: netapp_capacity_report(**{k: v for k, v in kw.items() if k != "verify"}),
)

# Export for tools/__init__.py
__all__ = [
    # Connection
    "_make_connection",
    # Volume operations
    "netapp_list_volumes",
    "netapp_create_volume",
    "netapp_delete_volume",
    "netapp_patch_volume",
    "netapp_clone_volume",
    "netapp_resize_volume",
    "netapp_offline_volume",
    "netapp_online_volume",
    # Qtree operations
    "netapp_list_qtrees",
    "netapp_create_qtree",
    "netapp_delete_qtree",
    "netapp_qtree_chown",
    # Quota operations
    "netapp_list_quotas",
    "netapp_create_quota",
    "netapp_delete_quota",
    # CIFS operations
    "netapp_list_cifs_shares",
    "netapp_create_cifs_share",
    "netapp_delete_cifs_share",
    # NFS operations
    "netapp_list_nfs_exports",
    "netapp_create_nfs_export",
    # iGroup operations
    "netapp_list_igroups",
    "netapp_create_igroup",
    "netapp_add_igroup_initiator",
    # LUN operations
    "netapp_list_luns",
    "netapp_create_lun",
    # SVM operations
    "netapp_list_svms",
    "netapp_get_svm_info",
    # Cluster monitoring
    "netapp_list_aggregates",
    "netapp_cluster_health",
    "netapp_capacity_report",
    # Tools
    "list_volumes_tool", "create_volume_tool", "delete_volume_tool", "patch_volume_tool",
    "clone_volume_tool", "resize_volume_tool", "offline_volume_tool", "online_volume_tool",
    "list_qtrees_tool", "create_qtree_tool", "delete_qtree_tool", "qtree_chown_tool",
    "list_quotas_tool", "create_quota_tool", "delete_quota_tool",
    "list_cifs_shares_tool", "create_cifs_share_tool", "delete_cifs_share_tool",
    "list_nfs_exports_tool", "create_nfs_export_tool",
    "list_igroups_tool", "create_igroup_tool", "add_igroup_initiator_tool",
    "list_luns_tool", "create_lun_tool",
    "list_svms_tool", "get_svm_info_tool",
    "list_aggregates_tool", "cluster_health_tool", "capacity_report_tool",
]
