from types import SimpleNamespace

from ai_quant_os.holdout_service_security import (
    HoldoutDirectoryAcl,
    build_holdout_acl,
    build_rpc_pipe_acl,
    configure_holdout_service_sid,
    current_process_user_sid,
    verify_pipe_server_identity,
    verify_holdout_directory_tree,
    _require_exact_acl,
)


class FakeAcl:
    def __init__(self):
        self.entries = []

    def AddAccessAllowedAceEx(self, revision, flags, access, sid):
        self.entries.append((revision, flags, access, sid))

    def GetAceCount(self):
        return len(self.entries)

    def GetAce(self, index):
        revision, flags, access, sid = self.entries[index]
        return (0, flags), access, sid


class FakeDescriptor:
    def __init__(self, dacl, owner):
        self.dacl, self.owner = dacl, owner

    def GetSecurityDescriptorDacl(self):
        return self.dacl

    def GetSecurityDescriptorOwner(self):
        return self.owner


class FakeSecurity:
    ACL_REVISION = 2
    OBJECT_INHERIT_ACE = 1
    CONTAINER_INHERIT_ACE = 2
    FILE_ALL_ACCESS = 2032127
    ACCESS_ALLOWED_ACE_TYPE = 0
    SE_FILE_OBJECT = 1
    DACL_SECURITY_INFORMATION = 4
    OWNER_SECURITY_INFORMATION = 1
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    SYNCHRONIZE = 0x00100000

    def __init__(self):
        self.acl = FakeAcl()

    def ACL(self):
        return self.acl

    @staticmethod
    def ConvertStringSidToSid(value):
        return value

    @staticmethod
    def ConvertSidToStringSid(value):
        return value

    def GetNamedSecurityInfo(self, path, object_type, flags):
        assert object_type == self.SE_FILE_OBJECT
        assert flags == self.DACL_SECURITY_INFORMATION | self.OWNER_SECURITY_INFORMATION
        return FakeDescriptor(self.acl, self.owner)


def test_holdout_acl_grants_only_service_sid_and_system_with_inheritance():
    security = FakeSecurity()
    service_sid = "S-1-5-80-1-2-3-4-5"
    rules = HoldoutDirectoryAcl(service_sid=service_sid, system_sid="S-1-5-18")

    dacl = build_holdout_acl(rules, security=security)

    assert dacl is security.acl
    assert dacl.entries == [
        (2, 3, 2032127, service_sid),
        (2, 3, 2032127, "S-1-5-18"),
    ]


def test_directory_verifier_requires_exact_owner_and_ace_inventory():
    security = FakeSecurity()
    rules = HoldoutDirectoryAcl("S-1-5-80-1-2-3-4-5")
    security.owner = rules.service_sid
    security.acl = build_holdout_acl(rules, security=security)

    _require_exact_acl("holdout", rules, security=security)

    security.acl.entries.append((2, 3, security.FILE_ALL_ACCESS, "S-1-5-11"))
    try:
        _require_exact_acl("holdout", rules, security=security)
    except PermissionError:
        return
    raise AssertionError("Unexpected directory ACE was accepted")


def test_directory_tree_verifier_checks_sidecars_and_nested_backups(tmp_path):
    from pathlib import Path

    root = tmp_path / "vault"
    backup = root / "backups" / "archive.sqlite3.bak"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"not a real database")
    service_sid = "S-1-5-80-1-2-3-4-5"
    rules = HoldoutDirectoryAcl(service_sid)
    security = FakeSecurity()
    security.owner = service_sid
    security.acl = build_holdout_acl(rules, security=security)

    verify_holdout_directory_tree(Path(root), rules, security=security)

    security.acl.entries.append((2, 3, security.FILE_ALL_ACCESS, "S-1-5-11"))
    try:
        verify_holdout_directory_tree(Path(root), rules, security=security)
    except PermissionError:
        return
    raise AssertionError("Unexpected backup ACL was accepted")


def test_secure_directory_adopts_only_the_staged_tree(tmp_path, monkeypatch):
    import sys

    from ai_quant_os import holdout_service_security

    root = tmp_path / "service-data"
    root.mkdir()
    archives = (root / "holdout.sqlite3", root / "validation.sqlite3")
    for archive in archives:
        archive.write_bytes(b"staged")
    service_sid = "S-1-5-80-1-2-3-4-5"
    security = FakeSecurity()
    security.DACL_SECURITY_INFORMATION = 4
    security.PROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
    security.OWNER_SECURITY_INFORMATION = 1
    security.SE_FILE_OBJECT = 1
    security.set_paths = []

    class FakeSid(str):
        pass

    security.ConvertStringSidToSid = FakeSid
    security.ConvertSidToStringSid = str

    def set_security(path, _object_type, _flags, owner, _group, dacl, _sacl):
        assert type(owner) is FakeSid
        security.set_paths.append(path)
        security.owner = owner
        security.acl = dacl

    security.SetNamedSecurityInfo = set_security
    security.TOKEN_ADJUST_PRIVILEGES = 32
    security.TOKEN_QUERY = 8
    security.TokenPrivileges = 3
    security.PROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
    security.SE_PRIVILEGE_ENABLED = 2
    security.SE_RESTORE_NAME = "SeRestorePrivilege"
    security.LookupPrivilegeValue = lambda _system, name: 42
    privilege_attributes = [0]
    security.OpenProcessToken = lambda _process, _access: "token"
    security.GetTokenInformation = lambda _token, _kind: [(42, privilege_attributes[0])]
    privilege_calls = []

    def adjust_privileges(token, disable, state):
        privilege_calls.append((token, disable, state))
        privilege_attributes[0] = state[0][1]

    security.AdjustTokenPrivileges = adjust_privileges
    security.GetSecurityInfo = lambda _handle, _object_type, _flags: FakeDescriptor(
        security.acl, security.owner
    )
    opened_handles = []

    def create_handle(path, *_args):
        opened_handles.append(path)
        return path

    file_api = SimpleNamespace(
        CreateFile=create_handle,
    )
    constants = SimpleNamespace(
        READ_CONTROL=0x20000,
        FILE_SHARE_READ=1,
        FILE_SHARE_WRITE=2,
        FILE_SHARE_DELETE=4,
        OPEN_EXISTING=3,
        FILE_FLAG_BACKUP_SEMANTICS=0x02000000,
    )
    process_api = SimpleNamespace(
        GetCurrentProcess=lambda: "process",
        CloseHandle=lambda handle: privilege_calls.append(("close", handle)),
    )
    monkeypatch.setitem(sys.modules, "win32api", process_api)
    monkeypatch.setitem(sys.modules, "win32con", constants)
    monkeypatch.setitem(sys.modules, "win32file", file_api)
    monkeypatch.setitem(sys.modules, "win32security", security)
    monkeypatch.setattr(
        holdout_service_security,
        "os",
        SimpleNamespace(**{**vars(holdout_service_security.os), "name": "nt"}),
    )
    monkeypatch.setattr(
        holdout_service_security,
        "service_sid_for_name",
        lambda _name, security: service_sid,
    )

    holdout_service_security.secure_holdout_directory(root, adopt_existing=True)

    assert set(security.set_paths) == {str(root), *(str(path) for path in archives)}
    assert type(security.owner) is FakeSid
    assert privilege_calls == [
        ("token", False, ((42, 2),)),
        ("close", str(root)),
        *(("close", str(path)) for path in archives),
        ("token", False, ((42, 0),)),
        ("close", "token"),
    ]
    assert set(opened_handles) == {str(root), *(str(path) for path in archives)}


def test_secure_directory_requires_restore_privilege_before_mutating_tree(tmp_path, monkeypatch):
    import sys

    from ai_quant_os import holdout_service_security

    root = tmp_path / "service-data"
    service_sid = "S-1-5-80-1-2-3-4-5"
    security = FakeSecurity()
    security.TOKEN_ADJUST_PRIVILEGES = 32
    security.TOKEN_QUERY = 8
    security.TokenPrivileges = 3
    security.PROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
    security.SE_PRIVILEGE_ENABLED = 2
    security.SE_RESTORE_NAME = "SeRestorePrivilege"
    security.LookupPrivilegeValue = lambda _system, _name: 42
    security.OpenProcessToken = lambda _process, _access: "token"
    security.GetTokenInformation = lambda _token, _kind: [(7, 0)]
    security.owner = service_sid
    security.acl = build_holdout_acl(HoldoutDirectoryAcl(service_sid), security=security)
    security.set_paths = []
    security.SetNamedSecurityInfo = lambda *args: security.set_paths.append(args[0])
    monkeypatch.setitem(sys.modules, "win32security", security)
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        SimpleNamespace(GetCurrentProcess=lambda: "process", CloseHandle=lambda _h: None),
    )
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "win32file", SimpleNamespace())
    monkeypatch.setattr(
        holdout_service_security,
        "os",
        SimpleNamespace(**{**vars(holdout_service_security.os), "name": "nt"}),
    )
    monkeypatch.setattr(
        holdout_service_security,
        "service_sid_for_name",
        lambda _name, security: service_sid,
    )

    try:
        holdout_service_security.secure_holdout_directory(root, adopt_existing=True)
    except PermissionError as exc:
        assert "SeRestorePrivilege" in str(exc)
    else:
        raise AssertionError("Missing restore privilege was accepted")

    assert security.set_paths == []
    assert not root.exists()


def test_restore_privilege_state_is_restored_when_acl_setup_raises():
    from ai_quant_os.holdout_service_security import _enabled_restore_privilege

    attributes = [0]
    calls = []

    class Security:
        TOKEN_ADJUST_PRIVILEGES = 32
        TOKEN_QUERY = 8
        TokenPrivileges = 3
        SE_RESTORE_NAME = "SeRestorePrivilege"
        SE_PRIVILEGE_ENABLED = 2

        @staticmethod
        def OpenProcessToken(process, access):
            assert process == "process" and access == 40
            return "token"

        @staticmethod
        def LookupPrivilegeValue(_system, name):
            assert name == "SeRestorePrivilege"
            return 18

        @staticmethod
        def GetTokenInformation(token, kind):
            assert token == "token" and kind == 3
            return [(18, attributes[0])]

        @staticmethod
        def AdjustTokenPrivileges(token, disable, state):
            assert token == "token" and disable is False
            attributes[0] = state[0][1]
            calls.append(attributes[0])

    process_api = SimpleNamespace(
        GetCurrentProcess=lambda: "process",
        CloseHandle=lambda handle: calls.append(("closed", handle)),
    )

    try:
        with _enabled_restore_privilege(security=Security, process_api=process_api):
            assert attributes == [Security.SE_PRIVILEGE_ENABLED]
            raise RuntimeError("synthetic ACL write failure")
    except RuntimeError as exc:
        assert str(exc) == "synthetic ACL write failure"
    else:
        raise AssertionError("The ACL write failure was swallowed")

    assert attributes == [0]
    assert calls == [2, 0, ("closed", "token")]


def test_holdout_acl_refuses_unexpected_or_duplicate_principals():
    security = FakeSecurity()
    for rules in (
        HoldoutDirectoryAcl(service_sid="S-1-5-18", system_sid="S-1-5-18"),
        HoldoutDirectoryAcl(service_sid="S-1-5-80-1-2-3-4-5", system_sid="S-1-5-19"),
        HoldoutDirectoryAcl(service_sid="", system_sid="S-1-5-18"),
    ):
        try:
            build_holdout_acl(rules, security=security)
        except ValueError:
            continue
        raise AssertionError("Invalid HOLDOUT ACL principals were accepted")


def test_staging_acl_adds_only_the_explicit_operator_sid():
    security = FakeSecurity()
    rules = HoldoutDirectoryAcl(
        "S-1-5-80-1-2-3-4-5",
        staging_sid="S-1-5-21-1-2-3-1001",
    )

    acl = build_holdout_acl(rules, security=security)

    assert [entry[3] for entry in acl.entries] == [
        "S-1-5-80-1-2-3-4-5",
        "S-1-5-18",
        "S-1-5-21-1-2-3-1001",
    ]


def test_rpc_pipe_acl_allows_only_operator_service_and_system():
    security = FakeSecurity()
    service_sid = "S-1-5-80-1-2-3-4-5"
    operator_sid = "S-1-5-21-1-2-3-1001"

    acl = build_rpc_pipe_acl(service_sid, operator_sid, security=security)

    assert [entry[3] for entry in acl.entries] == [service_sid, operator_sid, "S-1-5-18"]
    assert all(entry[2] == -1072693248 for entry in acl.entries)


def test_rpc_pipe_acl_rejects_service_as_operator():
    security = FakeSecurity()
    service_sid = "S-1-5-80-1-2-3-4-5"
    try:
        build_rpc_pipe_acl(service_sid, service_sid, security=security)
    except ValueError:
        return
    raise AssertionError("Service identity was accepted as the operator identity")


class FakeServiceApi:
    SC_MANAGER_CONNECT = 1
    SERVICE_CHANGE_CONFIG = 2
    SERVICE_QUERY_CONFIG = 4
    SERVICE_CONFIG_SERVICE_SID_INFO = 5
    SERVICE_SID_TYPE_UNRESTRICTED = 1

    def __init__(self, initial):
        self.sid_type = initial
        self.changed_to = []
        self.closed = []

    def OpenSCManager(self, machine, database, access):
        assert (machine, database, access) == (None, None, self.SC_MANAGER_CONNECT)
        return "scm"

    def OpenService(self, scm, name, access):
        assert scm == "scm" and name == "AIQuantHoldout"
        assert access == self.SERVICE_CHANGE_CONFIG | self.SERVICE_QUERY_CONFIG
        return "service"

    def QueryServiceConfig2(self, service, info):
        assert service == "service" and info == self.SERVICE_CONFIG_SERVICE_SID_INFO
        return self.sid_type

    def ChangeServiceConfig2(self, service, info, sid_type):
        assert service == "service" and info == self.SERVICE_CONFIG_SERVICE_SID_INFO
        self.changed_to.append(sid_type)
        self.sid_type = sid_type

    def CloseServiceHandle(self, handle):
        self.closed.append(handle)


def test_service_sid_configuration_is_idempotent_and_verified():
    api = FakeServiceApi(initial=0)

    configure_holdout_service_sid("AIQuantHoldout", service_api=api)

    assert api.changed_to == [api.SERVICE_SID_TYPE_UNRESTRICTED]
    assert sorted(api.closed) == ["scm", "service"]
    configure_holdout_service_sid("AIQuantHoldout", service_api=api)
    assert api.changed_to == [api.SERVICE_SID_TYPE_UNRESTRICTED]


def test_service_sid_configuration_refuses_unexpected_service_name():
    api = FakeServiceApi(initial=0)
    try:
        configure_holdout_service_sid("OtherService", service_api=api)
    except ValueError:
        return
    raise AssertionError("Unexpected service name was accepted")


class FakeTokenSecurity:
    TOKEN_QUERY = 8
    TokenUser = 1

    def __init__(self):
        self.closed = []

    def OpenProcessToken(self, process, access):
        assert process == "process" and access == self.TOKEN_QUERY
        return "token"

    def GetTokenInformation(self, token, kind):
        assert token == "token" and kind == self.TokenUser
        return ("S-1-5-21-1-2-3-1001", 0)

    @staticmethod
    def ConvertSidToStringSid(sid):
        return sid


def test_operator_sid_is_derived_from_current_process_token():
    security = FakeTokenSecurity()
    process = type(
        "ProcessApi",
        (),
        {
            "GetCurrentProcess": lambda: "process",
            "CloseHandle": lambda handle: security.closed.append(handle),
        },
    )

    sid = current_process_user_sid(
        security=security,
        process_api=process,
    )

    assert sid == "S-1-5-21-1-2-3-1001"
    assert security.closed == ["token"]


def test_pipe_server_identity_requires_enabled_validator_service_sid():
    service_sid = "S-1-5-80-1-2-3-4-5"

    class PipeApi:
        @staticmethod
        def GetNamedPipeServerProcessId(handle):
            assert handle == "pipe"
            return 4242

    class ProcessApi:
        closed = []

        @staticmethod
        def OpenProcess(access, inherit, process_id):
            assert (access, inherit, process_id) == (1, False, 4242)
            return "process"

        @staticmethod
        def CloseHandle(handle):
            ProcessApi.closed.append(handle)

    class SecurityApi:
        TOKEN_QUERY = 8
        TokenGroups = 2
        SE_GROUP_ENABLED = 4

        @staticmethod
        def OpenProcessToken(process, access):
            assert (process, access) == ("process", 8)
            return "token"

        @staticmethod
        def GetTokenInformation(token, kind):
            assert (token, kind) == ("token", 2)
            return [(service_sid, 4)]

        @staticmethod
        def ConvertSidToStringSid(sid):
            return sid

        @staticmethod
        def CloseHandle(handle):
            ProcessApi.closed.append(handle)

    verify_pipe_server_identity(
        "pipe",
        service_sid,
        pipe_api=PipeApi,
        process_api=ProcessApi,
        security=SecurityApi,
        process_query_right=1,
    )
    assert sorted(ProcessApi.closed) == ["process", "token"]


def test_pipe_server_identity_rejects_missing_service_sid():
    class PipeApi:
        @staticmethod
        def GetNamedPipeServerProcessId(handle):
            return 4242

    class ProcessApi:
        @staticmethod
        def OpenProcess(access, inherit, process_id):
            return "process"

        @staticmethod
        def CloseHandle(handle):
            pass

    class SecurityApi:
        TOKEN_QUERY = 8
        TokenGroups = 2
        SE_GROUP_ENABLED = 4

        @staticmethod
        def OpenProcessToken(process, access):
            return "token"

        @staticmethod
        def GetTokenInformation(token, kind):
            return [("S-1-5-80-9-8-7-6-5", 4)]

        @staticmethod
        def ConvertSidToStringSid(sid):
            return sid

        @staticmethod
        def CloseHandle(handle):
            pass

    try:
        verify_pipe_server_identity(
            "pipe",
            "S-1-5-80-1-2-3-4-5",
            pipe_api=PipeApi,
            process_api=ProcessApi,
            security=SecurityApi,
            process_query_right=1,
        )
    except PermissionError:
        return
    raise AssertionError("A pipe without the validator SID was accepted")
