from conduit.core.models import AuthMethod
from conduit.core.putty_import import PuttySession, _session_from_values, parse_session_file


def test_registry_style_values() -> None:
    session = _session_from_values(
        "My%20Server%20%28prod%29",
        {"HostName": "10.1.2.3", "PortNumber": 2222, "UserName": "deploy", "PublicKeyFile": r"C:\keys\deploy.ppk", "Protocol": "ssh"},
    )
    assert session == PuttySession("My Server (prod)", "10.1.2.3", 2222, "deploy", r"C:\keys\deploy.ppk", "ssh")
    conn = session.to_connection("Imported")
    assert conn.auth_method == AuthMethod.KEY
    assert conn.key_path.endswith("deploy.ppk")
    assert conn.group == "Imported"
    assert conn.port == 2222


def test_missing_host_is_ignored() -> None:
    assert _session_from_values("Default%20Settings", {"PortNumber": 22}) is None


def test_unix_session_file() -> None:
    text = "HostName=example.org\nPortNumber=22\nUserName=bob\nProtocol=ssh\nPublicKeyFile=\n"
    session = parse_session_file("box", text)
    assert session is not None
    assert session.host == "example.org" and session.username == "bob"
    assert session.to_connection().auth_method == AuthMethod.PASSWORD


def test_bad_port_falls_back_to_22() -> None:
    session = _session_from_values("x", {"HostName": "h", "PortNumber": "abc"})
    assert session is not None and session.port == 22
