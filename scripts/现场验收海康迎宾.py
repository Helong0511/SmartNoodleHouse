# -*- coding: utf-8 -*-
"""本地调试：对库内摄像头执行现场验收可自动项（1/2/5；推流可选；出声须 --heard）。

避免 import face_service（会拖 InsightFace 卡住）。凭据用 FACE_ENCRYPTION_KEY 本地 Fernet 解密。

场景：Windows 本机联调。PowerShell：
  $env:PYTHONPATH='E:\\SmartNoodleHouse;E:\\SmartNoodleHouse\\backend'
  python scripts\\现场验收海康迎宾.py --camera-id 1
  python scripts\\现场验收海康迎宾.py --camera-id 1 --play-g711 --heard yes
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = ROOT / ".env"
    if not p.is_file():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _fernet_from_material(material: str, *, legacy: bool = False):
    from cryptography.fernet import Fernet

    raw = (material or "").encode("utf-8")
    if legacy:
        key = base64.urlsafe_b64encode(raw[:32].ljust(32, b"0"))
    else:
        key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def _decrypt_camera_password(encrypted: str, env: dict[str, str]) -> str:
    from cryptography.fernet import InvalidToken

    material = env.get("FACE_ENCRYPTION_KEY") or env.get("SECRET_ENCRYPTION_KEY") or ""
    prev = (env.get("FACE_ENCRYPTION_KEY_PREVIOUS") or "").strip()
    token = (encrypted or "").encode("utf-8")
    f = _fernet_from_material(material, legacy=False)
    try:
        return f.decrypt(token).decode()
    except InvalidToken:
        if prev:
            try:
                return _fernet_from_material(prev, legacy=False).decrypt(token).decode()
            except InvalidToken:
                pass
        return _fernet_from_material(material, legacy=True).decrypt(token).decode()


def _db_conn(env: dict[str, str]):
    import pymysql

    url = env.get("DATABASE_URL", "")
    u = url.replace("mysql+aiomysql://", "mysql://").replace("mysql+pymysql://", "mysql://")
    p = urlparse(u)
    return pymysql.connect(
        host=p.hostname or "127.0.0.1",
        port=p.port or 3306,
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        database=(p.path or "/").lstrip("/").split("?")[0],
        charset="utf8mb4",
    )


def _tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def _host_from_rtsp(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


async def _run(args: argparse.Namespace) -> dict:
    # 延迟导入：仅 hikvision（勿碰 face_service）
    from app.services.hikvision_audio import (
        HikvisionTwoWayAudio,
        resolve_isapi_base,
        normalize_g711_codec,
    )

    print("load_env...", flush=True)
    env = _load_env()
    print("db...", flush=True)
    conn = _db_conn(env)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, source_url, username, password_encrypted, status "
        "FROM cameras WHERE id=%s",
        (args.camera_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise SystemExit(f"cameras.id={args.camera_id} 不存在")

    cam_id, name, source_url, username, enc, status = row
    print("decrypt...", flush=True)
    pwd = _decrypt_camera_password(enc or "", env)
    host = _host_from_rtsp(source_url)
    base = resolve_isapi_base(source_url)
    out: dict = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "camera_id": cam_id,
        "name": name,
        "source_url": source_url,
        "db_status": status,
        "isapi_base": base,
        "steps": {},
    }

    out["steps"]["tcp_554"] = {"ok": _tcp(host, 554), "host": host}
    out["steps"]["tcp_80"] = {"ok": _tcp(host, 80), "host": host}
    print("tcp", out["steps"]["tcp_554"], out["steps"]["tcp_80"], flush=True)

    client = HikvisionTwoWayAudio(base, username, pwd or "", timeout=20.0)

    t0 = time.perf_counter()
    print("codec_probe...", flush=True)
    try:
        codec = await client.resolve_codec(block=True)
        out["steps"]["1_codec"] = {
            "ok": True,
            "codec": normalize_g711_codec(codec) or codec,
            "ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as e:
        out["steps"]["1_codec"] = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "ms": int((time.perf_counter() - t0) * 1000),
        }
    print("1_codec", out["steps"]["1_codec"], flush=True)

    out["steps"]["5_http_isapi"] = {
        "ok": str(base).lower().startswith("http://"),
        "base": base,
        "note": "方案 A：HTTPS 基址被拒绝",
    }

    # RTSP 需注入账号密码（库内 source_url 通常无明文口令）
    from urllib.parse import quote

    rtsp_auth = source_url
    try:
        pu = urlparse(source_url)
        if pu.scheme in ("rtsp", "rtsps") and username and pwd and not pu.username:
            netloc = f"{quote(username, safe='')}:{quote(pwd, safe='')}@{pu.hostname}"
            if pu.port:
                netloc += f":{pu.port}"
            rtsp_auth = pu._replace(netloc=netloc).geturl()
    except Exception:
        rtsp_auth = source_url

    t1 = time.perf_counter()
    print("rtsp...", flush=True)
    try:
        import cv2

        cap = cv2.VideoCapture(rtsp_auth, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
        except Exception:
            pass
        ok_open = bool(cap.isOpened())
        frame_ok = False
        wh = None
        if ok_open:
            ok_read, frame = cap.read()
            frame_ok = bool(ok_read and frame is not None)
            if frame_ok:
                wh = [int(frame.shape[1]), int(frame.shape[0])]
        cap.release()
        out["steps"]["2_rtsp"] = {
            "ok": ok_open and frame_ok,
            "opened": ok_open,
            "frame": frame_ok,
            "wh": wh,
            "ms": int((time.perf_counter() - t1) * 1000),
        }
    except Exception as e:
        out["steps"]["2_rtsp"] = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "ms": int((time.perf_counter() - t1) * 1000),
        }
    print("2_rtsp", out["steps"]["2_rtsp"], flush=True)

    if args.play_g711 and out["steps"].get("1_codec", {}).get("ok"):
        print("play_g711...", flush=True)
        # LocalTTS 可能较重；仅在需要时导入
        from app.workers.tts_worker import LocalTTS

        codec = out["steps"]["1_codec"]["codec"]
        t2 = time.perf_counter()
        try:
            local = LocalTTS()
            g711 = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: local.synthesize_g711_sync(
                    args.text, codec=codec, volume_percent=int(args.volume)
                ),
            )
            play = await client.play_g711(g711, codec=codec)
            out["steps"]["play_g711"] = {
                "ok": True,
                "result": play if isinstance(play, dict) else {"raw": str(play)},
                "ms": int((time.perf_counter() - t2) * 1000),
                "device_playback": (play or {}).get("device_playback")
                if isinstance(play, dict)
                else None,
                "heard": args.heard,
                "note": "推流完成≠已听到；heard 须人工 yes/no",
            }
        except Exception as e:
            out["steps"]["play_g711"] = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "ms": int((time.perf_counter() - t2) * 1000),
            }
        print("play_g711", out["steps"]["play_g711"], flush=True)

    out["checklist_auto"] = {
        "1_codec": out["steps"].get("1_codec", {}).get("ok"),
        "2_rtsp": out["steps"].get("2_rtsp", {}).get("ok"),
        "3_heard_member": args.heard == "yes" if args.heard else None,
        "5_http_isapi": out["steps"].get("5_http_isapi", {}).get("ok"),
        "play_push": out["steps"].get("play_g711", {}).get("ok")
        if "play_g711" in out["steps"]
        else None,
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="海康迎宾现场验收可自动项")
    ap.add_argument("--camera-id", type=int, default=1)
    ap.add_argument("--play-g711", action="store_true", help="合成短句并 ISAPI 推流")
    ap.add_argument("--text", default="欢迎光临", help="试听文本")
    ap.add_argument("--volume", type=int, default=80)
    ap.add_argument(
        "--heard",
        choices=["yes", "no"],
        default=None,
        help="人耳是否听到扬声器（唯一真出声证据）",
    )
    ap.add_argument("--out", default="", help="结果 JSON 路径")
    args = ap.parse_args()
    result = asyncio.run(_run(args))
    out_path = Path(
        args.out
        or (ROOT / "tests" / "_out" / f"field_camera_{args.camera_id}.json")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    print(f"\nWrote {out_path}", flush=True)
    bad = [k for k, v in (result.get("checklist_auto") or {}).items() if v is False]
    if bad:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
