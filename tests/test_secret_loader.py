import sys
import types

from redshift_extractor.secret_loader import parse_credentials_secret, resolve_secret_reference


def test_resolve_secret_reference_reads_keyring_manager_entry(tmp_path, monkeypatch) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    keyring_dir = appdata / "KeyringManager"
    keyring_dir.mkdir(parents=True)
    (keyring_dir / "credentials.json").write_text(
        '[{"env_var":"SHARED_ANALYTICS_SECRET","usuario":"shared_user","service":"Analytics Service"}]',
        encoding="utf-8",
    )

    fake_keyring = types.SimpleNamespace(
        get_password=lambda service, user: "shared_secret"
        if service == "Analytics Service" and user == "shared_user"
        else None
    )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    user, password = resolve_secret_reference("SHARED_ANALYTICS_SECRET")

    assert user == "shared_user"
    assert password == "shared_secret"


def test_parse_credentials_secret_reads_escaped_json_payload() -> None:
    credentials = parse_credentials_secret(
        '{\\"user\\":\\"shared_user\\",\\"password\\":\\"shared_secret\\",\\"note\\":\\"ok\\"}'
    )

    assert credentials == ("shared_user", "shared_secret")
