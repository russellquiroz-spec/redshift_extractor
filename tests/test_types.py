from redshift_extractor.types import SSHConfig, RedshiftConfig, AppConfig


def test_types_construct():
    ssh = SSHConfig(host="h", port=22, user="u", pkey_path="/tmp/k.pem")
    rs = RedshiftConfig(host="rh", port=5439, dbname="d", user="ru", password="p")
    app = AppConfig(log_level="INFO", output_dir="./output")
    assert ssh.port == 22
    assert rs.port == 5439
    assert app.output_dir.endswith("output")