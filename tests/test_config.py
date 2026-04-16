import os
import sys
import types

import redshift_extractor.secret_loader as secret_loader_module
from redshift_extractor.config import load_config


def _clear_config_env(monkeypatch) -> None:
    managed_keys = {
        "SSH_HOST",
        "SSH_PORT",
        "SSH_USER",
        "SSH_PKEY_PATH",
        "LOG_LEVEL",
        "OUTPUT_DIR",
        "REDSHIFT_EXTRACTOR_ENV_FILE",
        "REDSHIFT_RUSSELL_KEY",
        "REDSHIFT_DEV_RUSSELL_KEY",
        "REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
        "REDSHIFT_RUSSELL_DEV_KEY",
    }
    for key in list(os.environ):
        if key.startswith("REDSHIFT__") or key in managed_keys:
            monkeypatch.delenv(key, raising=False)


def test_load_config_reads_credentials_from_system_env_per_alias(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__data-rabbit-prod__HOST=prod-cluster.example.com",
                "REDSHIFT__data-rabbit-prod__PORT=5439",
                "REDSHIFT__data-rabbit-prod__DBNAME=data-rabbit-prod",
                "REDSHIFT__data-rabbit-prod__CREDENTIALS_ENV=REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
                "REDSHIFT__dev__HOST=dev-cluster.example.com",
                "REDSHIFT__dev__PORT=5439",
                "REDSHIFT__dev__DBNAME=dev",
                "REDSHIFT__dev__CREDENTIALS_ENV=REDSHIFT_RUSSELL_DEV_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv(
        "REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
        '{"user":"prod_user","password":"prod_secret"}',
    )
    monkeypatch.setenv("REDSHIFT_RUSSELL_DEV_KEY", "dev_user:dev_secret")

    ssh, rs_map = load_config()

    assert ssh.host == "bastion.example.com"
    assert rs_map["data-rabbit-prod"].user == "prod_user"
    assert rs_map["data-rabbit-prod"].password == "prod_secret"
    assert rs_map["dev"].user == "dev_user"
    assert rs_map["dev"].password == "dev_secret"


def test_load_config_reads_credentials_from_keyring_manager_entry(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__prod__HOST=prod-cluster.example.com",
                "REDSHIFT__prod__PORT=5439",
                "REDSHIFT__prod__DBNAME=analytics",
                "REDSHIFT__prod__CREDENTIALS_ENV=REDSHIFT_PROD_CREDENTIALS",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    appdata = tmp_path / "AppData" / "Roaming"
    keyring_dir = appdata / "KeyringManager"
    keyring_dir.mkdir(parents=True)
    (keyring_dir / "credentials.json").write_text(
        '[{"env_var":"REDSHIFT_PROD_CREDENTIALS","usuario":"prod_user","service":"Redshift Prod"}]',
        encoding="utf-8",
    )

    fake_keyring = types.SimpleNamespace(
        get_password=lambda service, user: "prod_secret"
        if service == "Redshift Prod" and user == "prod_user"
        else None
    )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    _, rs_map = load_config()

    assert rs_map["prod"].user == "prod_user"
    assert rs_map["prod"].password == "prod_secret"


def test_load_config_normalizes_password_from_keyring_manager(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__prod__HOST=prod-cluster.example.com",
                "REDSHIFT__prod__PORT=5439",
                "REDSHIFT__prod__DBNAME=analytics",
                "REDSHIFT__prod__CREDENTIALS_ENV=REDSHIFT_PROD_CREDENTIALS",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    appdata = tmp_path / "AppData" / "Roaming"
    keyring_dir = appdata / "KeyringManager"
    keyring_dir.mkdir(parents=True)
    (keyring_dir / "credentials.json").write_text(
        '[{"env_var":"REDSHIFT_PROD_CREDENTIALS","usuario":"prod_user","service":"Redshift Prod"}]',
        encoding="utf-8",
    )

    fake_keyring = types.SimpleNamespace(
        get_password=lambda service, user: 'UQ&\\"8]gjhFu`KMx.zvk-Y'
    )

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    _, rs_map = load_config()

    assert rs_map["prod"].password == 'UQ&"8]gjhFu`KMx.zvk-Y'


def test_load_config_credentials_env_overrides_user_and_password_in_env(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__data-rabbit-prod__HOST=prod-cluster.example.com",
                "REDSHIFT__data-rabbit-prod__PORT=5439",
                "REDSHIFT__data-rabbit-prod__DBNAME=data-rabbit-prod",
                "REDSHIFT__data-rabbit-prod__USER=stale_user",
                "REDSHIFT__data-rabbit-prod__PASSWORD=stale_password",
                "REDSHIFT__data-rabbit-prod__CREDENTIALS_ENV=REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv(
        "REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
        '{"user":"prod_user","password":"prod_secret"}',
    )

    _, rs_map = load_config()

    assert rs_map["data-rabbit-prod"].user == "prod_user"
    assert rs_map["data-rabbit-prod"].password == "prod_secret"


def test_load_config_reads_nested_json_secret_with_extra_fields(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__data-rabbit-prod__HOST=prod-cluster.example.com",
                "REDSHIFT__data-rabbit-prod__PORT=5439",
                "REDSHIFT__data-rabbit-prod__DBNAME=data-rabbit-prod",
                "REDSHIFT__data-rabbit-prod__CREDENTIALS_ENV=REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv(
        "REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
        '{"comment":"secret json","metadata":{"owner":"russell"},"credentials":{"UserName":"prod_user","Password":"prod_secret"}}',
    )

    _, rs_map = load_config()

    assert rs_map["data-rabbit-prod"].user == "prod_user"
    assert rs_map["data-rabbit-prod"].password == "prod_secret"


def test_load_config_reads_json_secret_wrapped_as_string(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__data-rabbit-prod__HOST=prod-cluster.example.com",
                "REDSHIFT__data-rabbit-prod__PORT=5439",
                "REDSHIFT__data-rabbit-prod__DBNAME=data-rabbit-prod",
                "REDSHIFT__data-rabbit-prod__CREDENTIALS_ENV=REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv(
        "REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
        '"{\\"user\\": \\"prod_user\\", \\"password\\": \\"prod_secret\\", \\"comment\\": \\"wrapped\\"}"',
    )

    _, rs_map = load_config()

    assert rs_map["data-rabbit-prod"].user == "prod_user"
    assert rs_map["data-rabbit-prod"].password == "prod_secret"


def test_load_config_reads_python_style_secret_string(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__data-rabbit-prod__HOST=prod-cluster.example.com",
                "REDSHIFT__data-rabbit-prod__PORT=5439",
                "REDSHIFT__data-rabbit-prod__DBNAME=data-rabbit-prod",
                "REDSHIFT__data-rabbit-prod__CREDENTIALS_ENV=REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv(
        "REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
        '\'{"user": "prod_user", "password": "prod_secret", "comment": "single-quoted wrapper"}\'',
    )

    _, rs_map = load_config()

    assert rs_map["data-rabbit-prod"].user == "prod_user"
    assert rs_map["data-rabbit-prod"].password == "prod_secret"


def test_load_config_reads_escaped_json_without_outer_quotes(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__data-rabbit-prod__HOST=prod-cluster.example.com",
                "REDSHIFT__data-rabbit-prod__PORT=5439",
                "REDSHIFT__data-rabbit-prod__DBNAME=data-rabbit-prod",
                "REDSHIFT__data-rabbit-prod__CREDENTIALS_ENV=REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv(
        "REDSHIFT_RUSSELL_DATA_RABBIT_PROD_KEY",
        '{\\"user\\":\\"prod_user\\",\\"password\\":\\"prod_secret\\",\\"comment\\":\\"escaped\\"}',
    )

    _, rs_map = load_config()

    assert rs_map["data-rabbit-prod"].user == "prod_user"
    assert rs_map["data-rabbit-prod"].password == "prod_secret"


def test_load_config_reads_credentials_from_windows_registry_fallback(tmp_path, monkeypatch) -> None:
    _clear_config_env(monkeypatch)
    env_file = tmp_path / ".env.redshift_extractor"
    env_file.write_text(
        "\n".join(
            [
                "SSH_HOST=bastion.example.com",
                "SSH_PORT=22",
                "SSH_USER=ec2-user",
                "SSH_PKEY_PATH=/tmp/test.pem",
                "REDSHIFT__data-rabbit-prod__HOST=prod-cluster.example.com",
                "REDSHIFT__data-rabbit-prod__PORT=5439",
                "REDSHIFT__data-rabbit-prod__DBNAME=data-rabbit-prod",
                "REDSHIFT__data-rabbit-prod__CREDENTIALS_ENV=REDSHIFT_RUSSELL_KEY",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("REDSHIFT_EXTRACTOR_ENV_FILE", str(env_file))
    monkeypatch.setenv("APPDATA", str(tmp_path / "NoKeyring"))
    monkeypatch.setattr(
        secret_loader_module,
        "read_windows_env_value_from_registry",
        lambda env_name: '{"user":"prod_user","password":"prod_secret"}'
        if env_name == "REDSHIFT_RUSSELL_KEY"
        else None,
    )

    _, rs_map = load_config()

    assert rs_map["data-rabbit-prod"].user == "prod_user"
    assert rs_map["data-rabbit-prod"].password == "prod_secret"
