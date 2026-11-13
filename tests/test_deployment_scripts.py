"""Deployment utility regression tests."""
from __future__ import annotations

import gzip
import importlib.util
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "backup_restore_drill", ROOT / "scripts" / "备份恢复演练.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_database_identity_normalizes_async_mysql_driver_and_password_encoding():
    assert MODULE._database_identity(
        "mysql+aiomysql://operator:p%40ss@db.internal:3307/production?charset=utf8mb4"
    ) == ("db.internal", 3307, "production")


def test_database_identity_distinguishes_drill_database():
    production = MODULE._database_identity(
        "mysql+aiomysql://user:secret@db.internal:3306/smart_noodle"
    )
    drill = MODULE._database_identity(
        "mysql+pymysql://user:secret@db.internal:3306/smart_noodle_drill"
    )
    assert production != drill


@pytest.mark.parametrize(
    "url",
    [
        "",
        "redis://localhost:6379/0",
        "mysql://db.internal:3306/",
        "not-a-database-url",
    ],
)
def test_database_identity_rejects_invalid_mysql_target(url):
    with pytest.raises(ValueError):
        MODULE._database_identity(url)


BACKUP_SPEC = importlib.util.spec_from_file_location(
    "backup_database", ROOT / "scripts" / "备份数据库.py"
)
assert BACKUP_SPEC is not None and BACKUP_SPEC.loader is not None
BACKUP_MODULE = importlib.util.module_from_spec(BACKUP_SPEC)
BACKUP_SPEC.loader.exec_module(BACKUP_MODULE)


def test_mysqldump_command_uses_least_privilege_options():
    command = BACKUP_MODULE._build_mysqldump_command(
        "mysqldump",
        {
            "host": "127.0.0.1",
            "port": "3306",
            "user": "snh",
            "password": "unused",
            "database": "smart_noodle_house",
        },
    )

    assert "--single-transaction" in command
    assert "--triggers" in command
    assert "--events" in command
    assert "--no-tablespaces" in command
    assert "--set-gtid-purged=OFF" in command
    assert "--routines" not in command
    assert command[-1] == "smart_noodle_house"


def test_backup_prune_handles_microsecond_and_legacy_filenames(tmp_path, monkeypatch):
    BACKUP_MODULE.BACKUP_DIR = tmp_path
    old_microseconds = tmp_path / "smart_noodle_house_20000101_010203_123456.sql.gz"
    old_legacy = tmp_path / "smart_noodle_house_20000101_010203.sql.gz"
    malformed = tmp_path / "smart_noodle_house_invalid.sql.gz"
    recent = tmp_path / "smart_noodle_house_20990101_010203_123456.sql.gz"
    for path in (old_microseconds, old_legacy, malformed, recent):
        path.write_bytes(b"backup")

    removed = BACKUP_MODULE.prune(keep_days=14)

    assert removed == 2
    assert not old_microseconds.exists()
    assert not old_legacy.exists()
    assert malformed.exists()
    assert recent.exists()


def test_backup_gzip_verification_rejects_empty_and_truncated_files(tmp_path):
    empty = tmp_path / "empty.sql.gz"
    with gzip.open(empty, "wb"):
        pass
    with pytest.raises(ValueError, match="为空"):
        BACKUP_MODULE._verify_gzip_backup(empty)

    valid = tmp_path / "valid.sql.gz"
    with gzip.open(valid, "wb") as stream:
        stream.write(b"SELECT 1;\\n" * 100)
    assert BACKUP_MODULE._verify_gzip_backup(valid) == len(b"SELECT 1;\\n" * 100)

    truncated = tmp_path / "truncated.sql.gz"
    truncated.write_bytes(valid.read_bytes()[:-8])
    with pytest.raises((EOFError, OSError)):
        BACKUP_MODULE._verify_gzip_backup(truncated)


def test_mysqldump_streaming_does_not_deadlock_when_stderr_is_large(tmp_path):
    output = tmp_path / "backup.sql.gz"
    sql = b"CREATE TABLE smoke_test (id INT);\\n"
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('E' * 2000000); "
        "sys.stdout.buffer.write(" + repr(sql) + ")",
    ]

    return_code, error = BACKUP_MODULE._run_mysqldump_to_gzip(
        command, output, os.environ.copy()
    )

    assert return_code == 0
    assert len(error) == 1024 * 1024
    with gzip.open(output, "rb") as stream:
        assert stream.read() == sql


RESTORE_SPEC = importlib.util.spec_from_file_location(
    "restore_database", ROOT / "scripts" / "恢复数据库.py"
)
assert RESTORE_SPEC is not None and RESTORE_SPEC.loader is not None
RESTORE_MODULE = importlib.util.module_from_spec(RESTORE_SPEC)
RESTORE_SPEC.loader.exec_module(RESTORE_MODULE)


def test_restore_decompresses_gzip_backup_to_temporary_file_in_chunks(tmp_path):
    source = tmp_path / "backup.sql.gz"
    expected = (b"INSERT INTO orders VALUES (1);\\n" * 10000)
    with gzip.open(source, "wb") as stream:
        stream.write(expected)

    restored = RESTORE_MODULE._decompress_backup_to_temp(source)
    try:
        assert restored.read_bytes() == expected
        assert restored != source
    finally:
        restored.unlink(missing_ok=True)


def test_mysql_restore_command_forces_utf8mb4_client_charset():
    command = RESTORE_MODULE._build_mysql_restore_command(
        "mysql",
        {
            "host": "db.internal",
            "port": "3307",
            "user": "operator",
            "database": "smart_noodle_house",
        },
    )

    assert command == [
        "mysql",
        "--default-character-set=utf8mb4",
        "-h",
        "db.internal",
        "-P",
        "3307",
        "-u",
        "operator",
        "smart_noodle_house",
    ]



def test_sensitive_log_scan_reports_missing_local_log_directory_as_skipped(
    tmp_path, monkeypatch, capsys
):
    module_path = ROOT / "scripts" / "上线前运维检查清单.py"
    spec = importlib.util.spec_from_file_location("predeploy_checklist", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(
        module.Path,
        "resolve",
        lambda self: tmp_path / "scripts" / "上线前运维检查清单.py",
    )
    result = module.scan_sensitive_logs()

    output = capsys.readouterr().out
    assert result == 0
    assert "[SKIP]" in output
    assert "[PASS]" not in output
    assert "不代表生产主机" in output


def test_sensitive_log_scan_reports_empty_local_log_directory_as_skipped(
    tmp_path, monkeypatch, capsys
):
    module_path = ROOT / "scripts" / "上线前运维检查清单.py"
    spec = importlib.util.spec_from_file_location("predeploy_checklist_empty", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    (tmp_path / "logs" / "backend").mkdir(parents=True)
    monkeypatch.setattr(
        module.Path,
        "resolve",
        lambda self: tmp_path / "scripts" / "上线前运维检查清单.py",
    )
    result = module.scan_sensitive_logs()

    output = capsys.readouterr().out
    assert result == 0
    assert "[SKIP]" in output
    assert "[PASS]" not in output
    assert "没有可扫描的 .log 文件" in output
    assert "不代表生产主机" in output


def test_nginx_denies_direct_customer_avatar_static_access():
    config = (ROOT / "nginx" / "smart-noodle.conf").read_text(encoding="utf-8")
    assert "location ^~ /uploads/avatars/ {" in config
    assert "location ^~ /uploads/visitors/ {" in config
    assert config.index("location ^~ /uploads/avatars/ {") < config.index("location /uploads/ {")

def test_private_avatar_signed_queries_are_not_written_to_access_logs():
    nginx = (ROOT / "nginx" / "smart-noodle.conf").read_text(encoding="utf-8")
    service = (ROOT / "systemd" / "smart-noodle.service").read_text(encoding="utf-8")
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    log_format = next(line for line in nginx.splitlines() if line.startswith("log_format private_media"))
    assert "$uri" in log_format
    assert '"$request ' not in log_format
    assert "$args" not in log_format
    assert "location ^~ /api/media/private/avatars/" in nginx
    assert "smart-noodle.private-media.access.log private_media" in nginx
    assert "error_log /dev/null emerg;" in nginx
    assert "--no-access-log" in service
    assert 'request.url.path.startswith("/api/media/private/avatars/")' in main



def test_nginx_http_acme_challenge_is_exempt_from_https_redirect():
    config = (ROOT / "nginx" / "smart-noodle.conf").read_text(encoding="utf-8")
    bootstrap = (ROOT / "nginx" / "acme-bootstrap.conf").read_text(encoding="utf-8")
    install = (ROOT / "nginx" / "安装说明.txt").read_text(encoding="utf-8")

    assert "location ^~ /.well-known/acme-challenge/ {" in config
    assert "try_files $uri =404;" in config
    assert "location / {" in config
    assert "return 301 https://$host$request_uri;" in config
    assert "location ^~ /.well-known/acme-challenge/ {" in bootstrap
    assert "certbot certonly --webroot" in install
    assert "REPLACE_DOMAIN/你的域名.com/g" in install
    assert "renewal-hooks/deploy" in install
    assert "\\nsudo certbot renew" not in install
    assert "sudo certbot renew --dry-run" in install


def test_nginx_static_locations_keep_server_security_headers():
    config = (ROOT / "nginx" / "smart-noodle.conf").read_text(encoding="utf-8")
    required_headers = (
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
    )

    def location_block(marker: str) -> str:
        start = config.index(marker)
        opening = config.index("{", start)
        depth = 0
        for index in range(opening, len(config)):
            if config[index] == "{":
                depth += 1
            elif config[index] == "}":
                depth -= 1
                if depth == 0:
                    return config[opening + 1:index]
        raise AssertionError(f"Unclosed Nginx location: {marker}")

    for marker in ("location /assets/ {", "location /uploads/ {"):
        block = location_block(marker)
        for header in required_headers:
            assert f"add_header {header} " in block, f"{marker} missing {header}"


def test_nginx_ocsp_trust_bundle_refresh_includes_chain_and_system_roots():
    ssl = (ROOT / "nginx" / "snippets" / "ssl-params.conf").read_text(encoding="utf-8")
    script = (ROOT / "nginx" / "refresh-ocsp-trust.sh").read_text(encoding="utf-8")
    install = (ROOT / "nginx" / "安装说明.txt").read_text(encoding="utf-8")

    assert "ssl_trusted_certificate /etc/nginx/ssl-trusted-chain.pem;" in ssl
    assert 'RENEWED_LINEAGE' in script
    assert 'chain="${lineage}/chain.pem"' in script
    assert "/etc/ssl/certs/ca-certificates.crt" in script
    assert "nginx -t" in script
    assert "systemctl reload nginx" in script
    assert "renewal-hooks/deploy" in install


def test_nginx_ocsp_stapling_verification_has_trusted_ca_bundle():
    config = (ROOT / "nginx" / "snippets" / "ssl-params.conf").read_text(encoding="utf-8")
    assert "ssl_stapling_verify on;" in config
    assert "ssl_trusted_certificate /etc/nginx/ssl-trusted-chain.pem;" in config
    install = (ROOT / "nginx" / "安装说明.txt").read_text(encoding="utf-8")
    assert "ca-certificates" in install


def test_systemd_log_directory_is_provisioned_without_broad_var_log_write_access():
    service = (ROOT / "systemd" / "smart-noodle.service").read_text(encoding="utf-8")
    install = (ROOT / "systemd" / "安装说明.txt").read_text(encoding="utf-8")

    assert "ReadWritePaths=REPLACE_DEPLOY_ROOT/logs" in service
    assert "ReadWritePaths=/var/log" not in service
    assert "mkdir -p /var/www/smart-noodle/uploads /var/www/smart-noodle/logs" in install
    assert "chown -R www-data:www-data /var/www/smart-noodle/uploads /var/www/smart-noodle/logs" in install
