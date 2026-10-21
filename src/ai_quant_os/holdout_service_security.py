"""Windows HOLDOUT directory ACL creation and post-write verification."""

from dataclasses import dataclass
import os
from pathlib import Path
import re

_SERVICE_SID = re.compile(r"^S-1-5-80-(?:\d+-){4}\d+$")
_SID = re.compile(r"^S-1-\d+(?:-\d+){1,15}$")
_SYSTEM_SID = "S-1-5-18"
_FILE_ALL_ACCESS = 0x1F01FF
_PIPE_CLIENT_ACCESS = (0x80000000 | 0x40000000 | 0x00100000) - 0x100000000


@dataclass(frozen=True)
class HoldoutDirectoryAcl:
    service_sid: str
    system_sid: str = _SYSTEM_SID


def build_holdout_acl(rules: HoldoutDirectoryAcl, *, security):
    """Build a protected DACL that grants inheritable full access to two SIDs."""
    if (
        not _SERVICE_SID.fullmatch(rules.service_sid)
        or rules.system_sid != _SYSTEM_SID
        or rules.service_sid == rules.system_sid
    ):
        raise ValueError("Invalid HOLDOUT ACL principals")
    acl = security.ACL()
    inheritance = security.OBJECT_INHERIT_ACE | security.CONTAINER_INHERIT_ACE
    for sid_text in (rules.service_sid, rules.system_sid):
        acl.AddAccessAllowedAceEx(
            security.ACL_REVISION,
            inheritance,
            _FILE_ALL_ACCESS,
            security.ConvertStringSidToSid(sid_text),
        )
    return acl


def build_rpc_pipe_acl(service_sid: str, operator_sid: str, *, security):
    """Build a pipe DACL limited to the validator, interactive operator, and SYSTEM."""
    if (
        not _SERVICE_SID.fullmatch(service_sid)
        or not _SID.fullmatch(operator_sid)
        or len({service_sid, operator_sid, _SYSTEM_SID}) != 3
    ):
        raise ValueError("Invalid validator pipe ACL principals")
    acl = security.ACL()
    for sid_text in (service_sid, operator_sid, _SYSTEM_SID):
        acl.AddAccessAllowedAceEx(
            security.ACL_REVISION,
            0,
            _PIPE_CLIENT_ACCESS,
            security.ConvertStringSidToSid(sid_text),
        )
    return acl


def service_sid_for_name(service_name: str, *, security) -> str:
    if service_name != "AIQuantHoldout":
        raise ValueError("Unexpected HOLDOUT service name")
    sid, _, _ = security.LookupAccountName(None, f"NT SERVICE\\{service_name}")
    rendered = security.ConvertSidToStringSid(sid)
    if not _SERVICE_SID.fullmatch(rendered):
        raise ValueError("Windows returned an invalid HOLDOUT service SID")
    return rendered


def current_process_user_sid(*, security=None, process_api=None) -> str:
    """Read the caller's primary user SID to scope the local RPC pipe DACL."""
    if security is None or process_api is None:
        if os.name != "nt":
            raise OSError("Windows process identity is required")
        import win32api
        import win32security

        security = security or win32security
        process_api = process_api or win32api
    token = security.OpenProcessToken(process_api.GetCurrentProcess(), security.TOKEN_QUERY)
    try:
        sid, _attributes = security.GetTokenInformation(token, security.TokenUser)
        rendered = security.ConvertSidToStringSid(sid)
        if not _SID.fullmatch(rendered):
            raise PermissionError("Current Windows user SID is invalid")
        return rendered
    finally:
        process_api.CloseHandle(token)


def verify_pipe_server_identity(
    pipe_handle,
    service_sid: str,
    *,
    pipe_api,
    process_api,
    security,
    process_query_right: int,
) -> None:
    """Require an enabled per-service SID in the connected pipe server token."""
    if not _SERVICE_SID.fullmatch(service_sid):
        raise ValueError("Invalid validator service SID")
    process = token = None
    try:
        process_id = pipe_api.GetNamedPipeServerProcessId(pipe_handle)
        process = process_api.OpenProcess(process_query_right, False, process_id)
        token = security.OpenProcessToken(process, security.TOKEN_QUERY)
        groups = security.GetTokenInformation(token, security.TokenGroups)
        for sid, attributes in groups:
            if (
                security.ConvertSidToStringSid(sid) == service_sid
                and attributes & security.SE_GROUP_ENABLED
                and not attributes & getattr(security, "SE_GROUP_USE_FOR_DENY_ONLY", 0)
            ):
                return
    except Exception:
        raise PermissionError("Unable to verify validator pipe server identity") from None
    finally:
        if token is not None:
            process_api.CloseHandle(token)
        if process is not None:
            process_api.CloseHandle(process)
    raise PermissionError("Pipe server does not run with the validator service SID")


def configure_holdout_service_sid(service_name: str = "AIQuantHoldout", *, service_api) -> None:
    """Enable and verify the service's unrestricted per-service SID."""
    if service_name != "AIQuantHoldout":
        raise ValueError("Unexpected HOLDOUT service name")
    manager = service_api.OpenSCManager(None, None, service_api.SC_MANAGER_CONNECT)
    try:
        service = service_api.OpenService(
            manager,
            service_name,
            service_api.SERVICE_CHANGE_CONFIG | service_api.SERVICE_QUERY_CONFIG,
        )
        try:
            info = service_api.SERVICE_CONFIG_SERVICE_SID_INFO
            required = service_api.SERVICE_SID_TYPE_UNRESTRICTED
            if service_api.QueryServiceConfig2(service, info) != required:
                service_api.ChangeServiceConfig2(service, info, required)
            if service_api.QueryServiceConfig2(service, info) != required:
                raise PermissionError("HOLDOUT service SID configuration did not persist")
        finally:
            service_api.CloseServiceHandle(service)
    finally:
        service_api.CloseServiceHandle(manager)


def _require_exact_acl(
    path: Path,
    rules: HoldoutDirectoryAcl,
    *,
    security,
    require_owner: bool = True,
    require_inheritable: bool = True,
) -> None:
    descriptor = security.GetNamedSecurityInfo(
        str(path),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION | security.OWNER_SECURITY_INFORMATION,
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    owner = security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
    if dacl is None or (require_owner and owner != rules.service_sid):
        raise PermissionError("HOLDOUT directory owner or DACL is unavailable")
    observed = []
    for index in range(dacl.GetAceCount()):
        header, access, sid = dacl.GetAce(index)
        ace_type, ace_flags = header
        if ace_type != security.ACCESS_ALLOWED_ACE_TYPE:
            raise PermissionError("Unexpected HOLDOUT directory ACE")
        if require_inheritable and (
            (ace_flags & security.OBJECT_INHERIT_ACE) == 0
            or (ace_flags & security.CONTAINER_INHERIT_ACE) == 0
        ):
            raise PermissionError("HOLDOUT directory ACE is not inherited")
        observed.append((access, security.ConvertSidToStringSid(sid)))
    expected = sorted(
        (
            (_FILE_ALL_ACCESS, rules.service_sid),
            (_FILE_ALL_ACCESS, rules.system_sid),
        )
    )
    if sorted(observed) != expected:
        raise PermissionError("HOLDOUT directory DACL differs from policy")


def verify_holdout_directory_tree(path: Path, rules: HoldoutDirectoryAcl, *, security) -> None:
    """Verify every existing file/directory, including SQLite sidecars and backups."""
    import stat

    root = Path(path)
    _require_exact_acl(root, rules, security=security)
    for child, is_directory in _holdout_children(root, stat):
        _require_exact_acl(
            child,
            rules,
            security=security,
            require_owner=False,
            require_inheritable=is_directory,
        )


def _holdout_children(root: Path, stat_module):
    def walk_error(_error):
        raise PermissionError("Unable to inspect the complete HOLDOUT directory tree") from None

    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=walk_error
    ):
        current_path = Path(current)
        for name in (*directories, *files):
            child = current_path / name
            metadata = os.lstat(child)
            if (
                stat_module.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & 0x400
            ):
                raise PermissionError("HOLDOUT directory contains a reparse point")
            yield child, stat_module.S_ISDIR(metadata.st_mode)


def secure_holdout_directory(
    path: Path,
    *,
    service_name: str = "AIQuantHoldout",
    adopt_existing: bool = False,
) -> None:
    """Create and verify a protected service-owned directory. Requires elevation."""
    if os.name != "nt":
        raise OSError("HOLDOUT service ACLs require Windows")
    import win32security

    target = Path(path)
    if not target.is_absolute():
        raise ValueError("HOLDOUT target must be an absolute path")
    sid = service_sid_for_name(service_name, security=win32security)
    if target.exists() and (
        target.is_symlink()
        or not target.is_dir()
        or getattr(os.lstat(target), "st_file_attributes", 0) & 0x400
    ):
        raise ValueError("HOLDOUT target must be a real directory")
    children = ()
    if target.is_dir():
        import stat

        children = tuple(_holdout_children(target, stat))
        if not adopt_existing:
            for child, is_directory in children:
                _require_exact_acl(
                    child,
                    HoldoutDirectoryAcl(sid),
                    security=win32security,
                    require_owner=False,
                    require_inheritable=is_directory,
                )
    target.mkdir(parents=True, exist_ok=True)
    rules = HoldoutDirectoryAcl(service_sid=sid)
    dacl = build_holdout_acl(rules, security=win32security)
    security_flags = (
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION
        | win32security.OWNER_SECURITY_INFORMATION
    )
    if adopt_existing:
        for child, _is_directory in children:
            win32security.SetNamedSecurityInfo(
                str(child),
                win32security.SE_FILE_OBJECT,
                security_flags,
                sid,
                None,
                dacl,
                None,
            )
    win32security.SetNamedSecurityInfo(
        str(target),
        win32security.SE_FILE_OBJECT,
        security_flags,
        sid,
        None,
        dacl,
        None,
    )
    verify_holdout_directory_tree(target, rules, security=win32security)
