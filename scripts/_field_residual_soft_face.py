# -*- coding: utf-8 -*-
"""本地调试：软重开 RTSP + 当前帧 InsightFace 探测（项7/8 辅助）。勿 import face_service。"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

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


def _fernet(material: str, *, legacy: bool = False):
    from cryptography.fernet import Fernet

    raw = (material or "").encode("utf-8")
    if legacy:
        key = base64.urlsafe_b64encode(raw[:32].ljust(32, b"0"))
    else:
        key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def _decrypt(enc: str, env: dict[str, str]) -> str:
    from cryptography.fernet import InvalidToken

    material = env.get("FACE_ENCRYPTION_KEY") or env.get("SECRET_ENCRYPTION_KEY") or ""
    prev = (env.get("FACE_ENCRYPTION_KEY_PREVIOUS") or "").strip()
    token = (enc or "").encode("utf-8")
    try:
        return _fernet(material).decrypt(token).decode()
    except InvalidToken:
        if prev:
            try:
                return _fernet(prev).decrypt(token).decode()
            except InvalidToken:
                pass
        return _fernet(material, legacy=True).decrypt(token).decode()


def main() -> None:
    import cv2
    import pymysql

    env = _load_env()
    url = env.get("DATABASE_URL", "")
    u = url.replace("mysql+aiomysql://", "mysql://").replace("mysql+pymysql://", "mysql://")
    p = urlparse(u)
    conn = pymysql.connect(
        host=p.hostname or "127.0.0.1",
        port=p.port or 3306,
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        database=(p.path or "/").lstrip("/").split("?")[0],
        charset="utf8mb4",
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT source_url, username, password_encrypted, status FROM cameras WHERE id=1"
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise SystemExit("cameras.id=1 missing")
    src, user, enc, status = row
    pwd = _decrypt(enc or "", env)
    pu = urlparse(src)
    user_q = quote(user or "", safe="")
    pwd_q = quote(pwd or "", safe="")
    host = pu.hostname or ""
    netloc = f"{user_q}:{pwd_q}@{host}"
    if pu.port:
        netloc = f"{netloc}:{pu.port}"
    rtsp = pu._replace(netloc=netloc).geturl()

    out: dict = {"db_status": status, "soft_reconnect": [], "face": None}
    frame = None
    for i in range(2):
        t0 = time.perf_counter()
        cap = cv2.VideoCapture(rtsp, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
        except Exception:
            pass
        ok = bool(cap.isOpened())
        fr = False
        wh = None
        if ok:
            r, frame = cap.read()
            fr = bool(r and frame is not None)
            if fr:
                wh = [int(frame.shape[1]), int(frame.shape[0])]
        cap.release()
        out["soft_reconnect"].append(
            {
                "i": i + 1,
                "ok": ok and fr,
                "wh": wh,
                "ms": int((time.perf_counter() - t0) * 1000),
            }
        )
        time.sleep(0.25)

    out_dir = ROOT / "tests" / "_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_path = out_dir / "field_residual_frame_cam1.jpg"
    if frame is not None:
        cv2.imwrite(str(frame_path), frame)
        out["frame_path"] = str(frame_path)
        try:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=-1, det_size=(640, 640))
            h, w = frame.shape[:2]
            scale = 720.0 / max(h, w)
            small = (
                cv2.resize(frame, (int(w * scale), int(h * scale)))
                if scale < 1
                else frame
            )
            faces = app.get(small) or []
            sides = []
            for f in faces:
                box = f.bbox
                side_s = min(float(box[2] - box[0]), float(box[3] - box[1]))
                side = int(side_s / scale) if scale < 1 else int(side_s)
                sides.append(
                    {
                        "det_score": float(getattr(f, "det_score", 0) or 0),
                        "face_side_px": side,
                    }
                )
            out["face"] = {
                "count": len(faces),
                "faces": sides,
                "note": "insightface direct; face_side_px mapped to source resolution",
            }
        except Exception as ex:
            out["face"] = {"error": f"{type(ex).__name__}: {ex}"}

    dest = out_dir / "field_residual_soft_face.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
    print(f"Wrote {dest}", flush=True)


if __name__ == "__main__":
    main()
