#!/usr/bin/env python3
"""Astro_Manager · Tonight's Setup 필터 갱신 속도 평가 (RSI Loop 평가 계약 구현).

계약: python eval.py --repo <후보 저장소> --out <결과.json> [--label 이름]
결과 JSON:
  {"objective": 숫자, "gates": [{"gate", "passed", "detail"}], "breakdown": {...}, "details": {...}, "errors": [...]}
gate 하나라도 실패하면 루프는 그 후보를 기각합니다. --cleanup 은 후보 인스턴스만 정리하고 끝냅니다.

평가 과정: frontend 빌드(vite) → 테스트 인스턴스(DB 복사본, 127.0.0.1:35105) 실행 →
Playwright 벤치마크(bench/tonight.mjs, 외부 요청 차단·시각 고정) → golden 스냅샷 비교.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parents[2] / "bench"
GOLDEN = HERE / "golden_snapshot.json"
PASSWORD_HASH = HERE / ".bench_password_hash"
CAND_PORT = 35105
CAND_CONTAINER = "rsi-astro-cand"
CAND_DATA = HERE.parents[2] / "workspaces" / "astro-data-cand"
SOURCE_DATA = HERE.parents[2] / "workspaces" / "astro-data"
TEST_IMAGE = "rsi-astro-test:base"
ROUNDS = 5
CPU_THROTTLE = 1


def sh(cmd, check=True, timeout=None):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    return p


def build(repo: Path):
    fe = repo / "frontend"
    p = sh(["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "-v", f"{fe}:/f:Z", "-w", "/f",
            "node:20-alpine", "sh", "-c", "npx vite build --outDir dist-cand --emptyOutDir"], check=False, timeout=600)
    return p.returncode == 0, (p.stdout + p.stderr)[-2000:]


def start_instance(repo: Path):
    cleanup()
    if not CAND_DATA.exists():
        shutil.copytree(SOURCE_DATA, CAND_DATA)
    sh(["docker", "run", "-d", "--name", CAND_CONTAINER, "-p", f"127.0.0.1:{CAND_PORT}:5000",
        "-e", f"ADMIN_PASSWORD_HASH={PASSWORD_HASH.read_text().strip()}", "-e", "SESSION_SECRET_KEY=rsi-bench-secret",
        "-e", "TZ=Asia/Seoul", "-v", f"{CAND_DATA}:/app/data:Z",
        "-v", f"{repo / 'frontend' / 'dist-cand'}:/app/frontend/dist:ro,Z", TEST_IMAGE])
    for _ in range(60):
        if sh(["curl", "-sf", f"http://127.0.0.1:{CAND_PORT}/api/status"], check=False).returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("candidate instance did not start")


def cleanup():
    sh(["docker", "rm", "-f", CAND_CONTAINER], check=False)


def run_bench(label):
    rel = f"out/eval/{label}"
    (BENCH / "out" / "eval").mkdir(parents=True, exist_ok=True)
    p = sh([str(BENCH / "run_bench.sh"), f"http://127.0.0.1:{CAND_PORT}", str(ROUNDS), str(CPU_THROTTLE),
            f"{rel}.json", f"{rel}.snap.json"], check=False, timeout=1800)
    if p.returncode != 0:
        return None, None, (p.stdout + p.stderr)[-2000:]
    return json.loads((BENCH / f"{rel}.json").read_text()), json.loads((BENCH / f"{rel}.snap.json").read_text()), ""


def compare_snapshot(snap, golden):
    for key, rows in golden.items():
        if not isinstance(rows, list):
            continue
        got = snap.get(key)
        if got != rows:
            if got is None:
                return f"{key}: 스냅샷 없음"
            for i, (a, b) in enumerate(zip(rows, got)):
                if a != b:
                    return f"{key}: {i}번째 행 다름\n  기대: {a[:160]}\n  실제: {b[:160]}"
            return f"{key}: 행 수 다름 (기대 {len(rows)}, 실제 {len(got)})"
    return None


def evaluate(repo: Path, label: str):
    gates = []
    built, log = build(repo)
    gates.append({"gate": "build", "passed": built, "detail": None if built else log[-800:]})
    if not built:
        return {"objective": None, "gates": gates, "breakdown": {}, "errors": []}
    start_instance(repo)
    result, snap, err = run_bench(label)
    if result is None:
        gates.append({"gate": "bench", "passed": False, "detail": err[-800:]})
        return {"objective": None, "gates": gates, "breakdown": {}, "errors": []}
    diff = compare_snapshot(snap, json.loads(GOLDEN.read_text()))
    gates.append({"gate": "snapshot", "passed": diff is None, "detail": diff})
    clean = not result["errors"] and not result["timeouts"]
    gates.append({"gate": "no_page_errors", "passed": clean,
                  "detail": None if clean else {"errors": result["errors"][:3], "timeouts": result["timeouts"]}})
    # 에이전트에게 "가장 느린 조작"을 보여주기 위한 세부 정보
    slowest = sorted(result["steps"].items(), key=lambda kv: -kv[1]["median_ms"])[:6]
    return {
        "objective": result["objective_total_wait_ms"],
        "gates": gates,
        "breakdown": result["groups_ms"],
        "details": {"slowest_steps": {k: f"{v['median_ms']}ms (TBT {v['median_tbt_ms']}ms)" for k, v in slowest}},
        "errors": result["errors"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--out")
    ap.add_argument("--label", default="eval")
    ap.add_argument("--cleanup", action="store_true")
    a = ap.parse_args()
    if a.cleanup:
        cleanup()
        return
    out = evaluate(Path(a.repo), a.label)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
