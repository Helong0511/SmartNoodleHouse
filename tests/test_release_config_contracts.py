from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dotenv_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    raise AssertionError(f"{key} is missing from {path}")


def test_generic_env_file_is_an_overview_not_a_production_template() -> None:
    path = ROOT / ".env.example"
    content = path.read_text(encoding="utf-8")
    assert "仅供查阅，不可直接复制为运行配置" in content
    assert _dotenv_value(path, "APP_ENV") == "development"
    assert _dotenv_value(path, "APP_HOST") == "127.0.0.1"
    assert "请勿直接复制为 .env" in content


def test_production_webhook_allowlist_policy_matches_operator_guide() -> None:
    template = ROOT / ".env.production.example"
    docs = (ROOT / "docs/部署说明.md").read_text(encoding="utf-8")
    assert _dotenv_value(template, "WEBHOOK_REQUIRE_ALLOWLIST") == "false"
    assert "WEBHOOK_REQUIRE_ALLOWLIST=false" in docs
    assert "不得猜测或复制未经官方确认的 IP" in docs
    assert "签名/证书验签" in docs
    assert "数据库幂等" in docs



def test_production_template_uses_existing_secret_generator_and_disables_recognition_worker() -> None:
    template = ROOT / ".env.production.example"
    content = template.read_text(encoding="utf-8")
    assert "python scripts/生成密钥.py" in content
    assert (ROOT / "scripts/生成密钥.py").is_file()
    assert "scripts/gen_secrets.py" not in content
    assert _dotenv_value(template, "ENABLE_RECOGNITION_WORKER") == "0"


def test_systemd_install_guide_uses_pinned_backend_lockfile() -> None:
    guide = (ROOT / "systemd/安装说明.txt").read_text(encoding="utf-8")
    assert "pip install -r backend/requirements.lock" in guide
    executable_lines = [line.strip() for line in guide.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert not any("pip install -r backend/requirements.txt" in line for line in executable_lines)
    assert (ROOT / "backend/requirements.lock").is_file()

def test_production_guide_makes_alipay_phone_aes_key_conditional() -> None:
    template = ROOT / ".env.production.example"
    guide = (ROOT / "scripts/生产部署-ubuntu.txt").read_text(encoding="utf-8")
    assert _dotenv_value(template, "ENABLE_ALIPAY_PHONE_BIND") == "false"
    assert "仅启用支付宝手机号绑定时必填" in guide
    assert "ALIPAY_AES_KEY=（支付宝手机号解密，必填）" not in guide


def test_deployment_audit_keeps_camera_capture_separate_from_face_recognition() -> None:
    docs = (ROOT / "docs/audit/DEPLOYMENT_AND_OPERATIONS.md").read_text(encoding="utf-8")
    assert "人脸识别全局 hard-stop" in docs
    assert "不加载 InsightFace" in docs
    assert "摄像头采集不代表人脸识别已启用" in docs
    assert "不得运行 InsightFace、人脸身份匹配或读取人脸档案" in docs
