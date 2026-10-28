# -*- coding: utf-8 -*-
"""本地：拉 visitor_events + 抓拍图，量 face_side_px / 分辨率，汇总识别路径证据。"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "_out"
OUT.mkdir(parents=True, exist_ok=True)


def load_env() -> dict[str, str]:
    e: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        e[k.strip()] = v.strip().strip('"').strip("'")
    return e


def uploads_root(env: dict[str, str]) -> Path:
    """本地调试：后端常从 backend/ 启动，UPLOAD_DIR=uploads → backend/uploads。"""
    candidates: list[Path] = []
    for k in ("UPLOAD_DIR", "UPLOAD_ROOT", "LOCAL_UPLOAD_DIR"):
        if env.get(k):
            p = Path(env[k])
            if not p.is_absolute():
                candidates.extend([ROOT / "backend" / p, ROOT / p])
            else:
                candidates.append(p)
    candidates.extend(
        [
            ROOT / "backend" / "uploads",
            ROOT / "uploads",
        ]
    )
    for p in candidates:
        if (p / "visitors").is_dir() or p.is_dir():
            # 优先含 visitors 的目录
            if (p / "visitors").is_dir():
                return p
    for p in candidates:
        if p.is_dir():
            return p
    return ROOT / "backend" / "uploads"


def snap_file(uploads: Path, snapshot_path: str | None) -> Path | None:
    if not snapshot_path:
        return None
    s = snapshot_path.replace("\\", "/")
    if "/uploads/" in s:
        s = s.split("/uploads/", 1)[1]
    if s.startswith("uploads/"):
        s = s[len("uploads/") :]
    p = uploads / s
    return p if p.is_file() else None


def img_meta(path: Path) -> dict:
    import cv2

    img = cv2.imread(str(path))
    if img is None:
        return {"ok": False, "error": "imread_fail", "bytes": path.stat().st_size}
    h, w = img.shape[:2]
    return {"ok": True, "w": w, "h": h, "bytes": path.stat().st_size, "mp": round(w * h / 1e6, 3)}


def face_side(path: Path) -> dict:
    """用项目 live 提取路径估人脸边长（与线上一致）。"""
    try:
        sys.path.insert(0, str(ROOT / "backend"))
        import cv2

        from app.services.face_extractor import extract_live_face_hit  # type: ignore

        img = cv2.imread(str(path))
        if img is None:
            return {"faces": 0, "error": "imread"}
        hit = extract_live_face_hit(img, light=True)
        return {
            "faces": 1,
            "face_side_px": int(getattr(hit, "face_side_px", 0) or 0),
            "det_score": round(float(getattr(hit, "det_score", 0) or 0), 3),
            "face_area_ratio": round(float(getattr(hit, "face_area_ratio", 0) or 0), 4),
            "frame_w": int(getattr(hit, "frame_w", 0) or 0),
            "frame_h": int(getattr(hit, "frame_h", 0) or 0),
        }
    except Exception as ex:  # noqa: BLE001
        return {"faces": None, "error": f"{type(ex).__name__}:{ex}"}


def main() -> None:
    env = load_env()
    uploads = uploads_root(env)
    u = env["DATABASE_URL"].replace("mysql+aiomysql://", "mysql://").replace(
        "mysql+pymysql://", "mysql://"
    )
    p = urlparse(u)
    conn = pymysql.connect(
        host=p.hostname,
        port=p.port or 3306,
        user=unquote(p.username or ""),
        password=unquote(p.password or ""),
        database=(p.path or "/").lstrip("/").split("?")[0],
        charset="utf8mb4",
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT v.id, v.visitor_no, v.camera_id, v.customer_id, v.event_type,
               v.event_time, v.snapshot_path, v.created_at, v.expires_at,
               TIMESTAMPDIFF(MICROSECOND, v.event_time, v.created_at) / 1000.0 AS ingest_lag_ms,
               c.display_name, c.customer_no, c.nickname
        FROM visitor_events v
        LEFT JOIN customers c ON c.id = v.customer_id
        ORDER BY v.event_time DESC
        """
    )
    cols = [d[0] for d in cur.description]
    events = [dict(zip(cols, r)) for r in cur.fetchall()]
    for d in events:
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat(sep=" ", timespec="milliseconds")

    cur.execute(
        """
        SELECT id, customer_id, camera_id, welcome_text, created_at
        FROM welcome_logs ORDER BY id DESC LIMIT 30
        """
    )
    welcome = []
    for r in cur.fetchall():
        welcome.append(
            {
                "id": r[0],
                "customer_id": r[1],
                "camera_id": r[2],
                "welcome_text": r[3],
                "created_at": r[4].isoformat(sep=" ", timespec="milliseconds")
                if r[4]
                else None,
            }
        )

    cur.execute("SHOW COLUMNS FROM cameras")
    cam_cols = [r[0] for r in cur.fetchall()]
    want = [
        c
        for c in (
            "id",
            "name",
            "status",
            "source_url",
            "detect_fps",
            "fps",
            "updated_at",
            "created_at",
            "last_seen_at",
            "online_since",
        )
        if c in cam_cols
    ]
    cur.execute(f"SELECT {', '.join(want)} FROM cameras ORDER BY id")
    cams = []
    for r in cur.fetchall():
        row = dict(zip(want, r))
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat(sep=" ", timespec="seconds")
        cams.append(row)

    cur.execute(
        """
        SELECT setting_key, setting_value FROM system_settings
        WHERE setting_key LIKE %s OR setting_key LIKE %s OR setting_key LIKE %s
           OR setting_key LIKE %s OR setting_key LIKE %s
        ORDER BY setting_key
        """,
        ("%visitor%", "%face%", "%welcome%", "%camera%", "%speaker%"),
    )
    settings = {k: v for k, v in cur.fetchall()}
    conn.close()

    analyzed = []
    missing = 0
    for ev in events:
        sp = ev.get("snapshot_path")
        fp = snap_file(uploads, sp)
        item = {
            **ev,
            "snap_exists": bool(fp),
            "snap_local": str(fp) if fp else None,
        }
        if not fp:
            missing += 1
            analyzed.append(item)
            continue
        meta = img_meta(fp)
        item["img"] = meta
        # 人脸分析较慢，只对有图事件做
        item["face"] = face_side(fp)
        analyzed.append(item)

    type_cnt = Counter(e["event_type"] for e in events)
    member_cnt = sum(1 for e in events if e.get("customer_id"))
    sides = [
        a["face"]["face_side_px"]
        for a in analyzed
        if a.get("face") and a["face"].get("face_side_px")
    ]
    resos = [
        f"{a['img']['w']}x{a['img']['h']}"
        for a in analyzed
        if a.get("img", {}).get("ok")
    ]

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "uploads_root": str(uploads),
        "event_count": len(events),
        "snap_missing": missing,
        "type_counts": dict(type_cnt),
        "member_events": member_cnt,
        "temp_events": len(events) - member_cnt,
        "face_side_px": {
            "n": len(sides),
            "min": min(sides) if sides else None,
            "max": max(sides) if sides else None,
            "avg": round(sum(sides) / len(sides), 1) if sides else None,
            "values": sides,
        },
        "resolutions": dict(Counter(resos)),
        "cameras": cams,
        "welcome_log_count": len(welcome),
        "settings_keys": sorted(settings.keys()),
        "settings": settings,
    }

    payload = {"summary": summary, "events": analyzed, "welcome_logs": welcome}
    out_path = OUT / "visitor_events_analysis.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    print("OUT", out_path)
    print("events", len(events), "missing_snap", missing, "types", dict(type_cnt))
    print("face_side", summary["face_side_px"])
    print("resos", summary["resolutions"])
    print("cams", cams)
    for a in analyzed[:6]:
        fs = (a.get("face") or {}).get("face_side_px")
        img = a.get("img") or {}
        print(
            a["id"],
            a["event_type"],
            a["event_time"],
            f"{img.get('w')}x{img.get('h')}",
            "side",
            fs,
            "cust",
            a.get("display_name") or a.get("visitor_no"),
        )


if __name__ == "__main__":
    main()
