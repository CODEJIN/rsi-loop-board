# file: eval.py
"""AViewer mkv 클릭→재생 시작 시간 평가.

실행: python3 evals/aviewer-mkv-start/eval.py --repo <경로> --out <json> --label <이름>
정리: python3 evals/aviewer-mkv-start/eval.py --cleanup

설계 메모: 기준 코드는 mkv를 아예 재생하지 못한다. 그래서 "mkv가 재생된다"는 조건은 gate가 아니라
objective 패널티(재생 실패 = 상한값)로 반영하고, 재생이 일부 반복에서만 성공하는 경우만 gate로 막는다.
스트림 코덱·인증 gate는 재생이 한 번이라도 성공한 영상에만 적용한다.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
FIX_DIR = HERE / ".fixtures"
FIX_VERSION = "1"
FFMPEG_IMAGE = "rsi-aviewer-eval-ffmpeg:1"
BROWSER_IMAGE = "rsi-aviewer-eval-browser:1"
CONTAINER_PREFIX = "rsi-aviewer-eval-"
PASSWORD = "eval-password"
REPS = 5
MP4_REPS = 3
PLAY_TIMEOUT_MS = 20000
ALLOWED_WRITE_PREFIXES = ("/app/thumbnails", "/app/preview_frames")

FFMPEG_DOCKERFILE = "FROM node:20-alpine\nRUN apk add --no-cache ffmpeg\n"
BROWSER_DOCKERFILE = """FROM mcr.microsoft.com/playwright:v1.47.0-jammy
RUN mkdir /probe && cd /probe && npm init -y >/dev/null && npm install playwright@1.47.0 && npx playwright install chrome
ENV NODE_PATH=/probe/node_modules
"""

COMMON_ENC = ["-preset", "veryfast", "-crf", "26", "-g", "60", "-shortest",
              "-fflags", "+bitexact", "-flags:v", "+bitexact"]
FIXTURES = {
    "FX/hi10_aac.mkv": ["-c:v", "libx264", "-profile:v", "high10", "-pix_fmt", "yuv420p10le", "-c:a", "aac"],
    "FX/hi10_ac3.mkv": ["-c:v", "libx264", "-profile:v", "high10", "-pix_fmt", "yuv420p10le", "-c:a", "ac3"],
    "FX/h264_aac.mkv": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "FX/ref.mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"],
}

PROBE_JS = r'''
const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  const [base, password, filename, outPath, timeoutMs] = process.argv.slice(2);
  const res = { ok: false, error: null, t: {}, mediaUrls: [], state: null, progress: null };
  let browser;
  try {
    browser = await chromium.launch({
      channel: 'chrome', headless: true,
      args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required',
             '--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1'],
    });
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const login = await ctx.request.post(base + '/api/login', { data: { password } });
    if (!login.ok()) throw new Error('login failed: ' + login.status());
    const page = await ctx.newPage();
    page.on('request', (r) => { if (r.resourceType() === 'media') res.mediaUrls.push(r.url()); });
    await page.addInitScript(() => {
      window.__t = {};
      const mark = (name) => (e) => {
        if (e.target && e.target.tagName === 'VIDEO' && window.__t[name] == null) window.__t[name] = performance.now();
      };
      document.addEventListener('click', () => { if (window.__t.click == null) window.__t.click = performance.now(); }, true);
      document.addEventListener('loadedmetadata', mark('meta'), true);
      document.addEventListener('loadeddata', mark('data'), true);
      document.addEventListener('timeupdate', (e) => {
        const v = e.target;
        if (v && v.tagName === 'VIDEO' && v.currentTime > 0.1 && window.__t.play == null) window.__t.play = performance.now();
      }, true);
    });
    const name = filename.split('/').pop();
    await page.goto(`${base}/?search=${encodeURIComponent(name)}`);
    await page.waitForFunction((alt) => {
      const i = [...document.images].find((x) => x.alt === alt);
      return i && i.complete && i.naturalWidth > 0;
    }, filename, { timeout: 30000 });
    await page.getByAltText(filename, { exact: true }).click();
    try {
      await page.waitForFunction(() => window.__t.play != null, null, { timeout: Number(timeoutMs), polling: 50 });
      res.ok = true;
    } catch (e) {
      res.error = `재생 시작 시간 초과(${timeoutMs}ms)`;
    }
    res.t = await page.evaluate(() => window.__t);
    res.state = await page.evaluate(() => {
      const v = document.querySelector('video');
      return v ? { readyState: v.readyState, currentTime: v.currentTime, src: v.currentSrc,
                   error: v.error ? { code: v.error.code, message: v.error.message } : null } : null;
    });
    if (res.ok) {
      const c0 = res.state.currentTime;
      await page.waitForTimeout(1500);
      const c1 = await page.evaluate(() => document.querySelector('video').currentTime);
      res.progress = c1 - c0;
      if (res.progress < 0.5) { res.ok = false; res.error = '재생이 진행되지 않음'; }
    }
  } catch (e) {
    res.error = String((e && e.message) || e);
  } finally {
    if (browser) await browser.close().catch(() => {});
    fs.writeFileSync(outPath, JSON.stringify(res));
  }
})();
'''


# ---------- 공용 헬퍼 ----------
def sh(cmd, timeout=120, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def tail(r, n=500):
    return ((r.stdout or "") + (r.stderr or ""))[-n:]


def safe(s):
    return re.sub(r"[^a-z0-9_.-]", "-", s.lower())[:40]


def vid(filename):
    return base64.urlsafe_b64encode(filename.encode()).rstrip(b"=").decode()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_hash():
    salt, it = "evalsalt", 1000
    h = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt.encode(), it, 32).hex()
    return f"pbkdf2:sha256:{it}${salt}${h}"


def req(port, method, path, headers=None, body=None, cookie=None, timeout=30):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = cookie
    conn.request(method, path, body=body, headers=h)
    return conn, conn.getresponse()


def get_json(port, path, cookie):
    conn, r = req(port, "GET", path, cookie=cookie)
    data = r.read()
    conn.close()
    return r.status, (json.loads(data) if r.status == 200 else None)


def login(port):
    conn, r = req(port, "POST", "/api/login", {"Content-Type": "application/json"},
                  json.dumps({"password": PASSWORD}))
    r.read()
    sc = r.getheader("Set-Cookie")
    conn.close()
    if r.status != 200 or not sc:
        raise RuntimeError(f"로그인 실패: {r.status}")
    return sc.split(";")[0]


def ffmpeg_count(cname):
    r = sh(["docker", "top", cname, "-eo", "args"])
    return sum(1 for line in r.stdout.splitlines()[1:] if "ffmpeg" in line)


def wait_ffmpeg_idle(cname, secs):
    end = time.time() + secs
    while time.time() < end:
        if ffmpeg_count(cname) == 0:
            return True
        time.sleep(0.5)
    return ffmpeg_count(cname) == 0


def docker_diff(cname):
    return set(sh(["docker", "diff", cname]).stdout.splitlines())


def ensure_image(tag, dockerfile):
    if sh(["docker", "image", "inspect", tag]).returncode == 0:
        return
    r = subprocess.run(["docker", "build", "-t", tag, "-"], input=dockerfile,
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"이미지 빌드 실패({tag}): {tail(r)}")


def ensure_fixtures():
    marker = FIX_DIR / "VERSION"
    if marker.exists() and marker.read_text().strip() == FIX_VERSION:
        return
    shutil.rmtree(FIX_DIR, ignore_errors=True)
    (FIX_DIR / "FX").mkdir(parents=True)
    user = f"{os.getuid()}:{os.getgid()}"
    for name, enc in FIXTURES.items():
        cmd = ["docker", "run", "--rm", "--user", user, "-v", f"{FIX_DIR}:/out", FFMPEG_IMAGE,
               "ffmpeg", "-y", "-v", "error",
               "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30000/1001",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
               "-t", "240", *enc, *COMMON_ENC, f"/out/{name}"]
        r = sh(cmd, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"fixture 생성 실패({name}): {tail(r)}")
    marker.write_text(FIX_VERSION)


def fixture_hashes():
    out = {}
    for name in FIXTURES:
        h = hashlib.sha256()
        with open(FIX_DIR / name, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out[name] = h.hexdigest()
    return out


def run_probe(work, port, filename, tag):
    out = work / f"probe_{tag}.json"
    cmd = ["docker", "run", "--rm", "--network", "host", "--ipc=host", "-v", f"{work}:/work",
           BROWSER_IMAGE, "node", "/work/probe.cjs", f"http://127.0.0.1:{port}", PASSWORD,
           filename, f"/work/{out.name}", str(PLAY_TIMEOUT_MS)]
    try:
        r = sh(cmd, timeout=PLAY_TIMEOUT_MS / 1000 + 90)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "probe 실행 시간 초과", "t": {}, "mediaUrls": []}
    if out.exists():
        return json.loads(out.read_text())
    return {"ok": False, "error": "probe 출력 없음: " + tail(r, 300), "t": {}, "mediaUrls": []}


def span(p, key):
    t = p.get("t") or {}
    if t.get("click") is None or t.get(key) is None:
        return None
    return t[key] - t["click"]


def fetch_sample(port, url, cookie, limit=6_000_000, secs=20):
    sp = urlsplit(url)
    target = sp.path + ("?" + sp.query if sp.query else "")
    t0 = time.time()
    conn, r = req(port, "GET", target, {"Range": "bytes=0-"}, cookie=cookie)
    info = {"status": r.status, "ctype": r.getheader("Content-Type"), "ttfb_ms": None}
    data = b""
    while len(data) < limit and time.time() - t0 < secs:
        chunk = r.read1(65536)
        if not chunk:
            break
        if info["ttfb_ms"] is None:
            info["ttfb_ms"] = (time.time() - t0) * 1000
        data += chunk
    conn.close()
    return info, data


def check_stream(work, data, tag):
    """스트림 앞부분을 ffprobe로 검사. 문제가 있으면 사유 문자열, 없으면 None."""
    i_moov, i_moof = data.find(b"moov"), data.find(b"moof")
    if data[4:8] != b"ftyp" or i_moov < 0 or i_moof < 0 or i_moov > i_moof:
        return "fragmented mp4 구조가 아님(ftyp/moov/moof 순서 확인 필요)"
    f = work / f"sample_{tag}.mp4"
    f.write_bytes(data)
    r = sh(["docker", "run", "--rm", "-v", f"{work}:/work", "--entrypoint", "ffprobe", FFMPEG_IMAGE,
            "-v", "error", "-show_entries", "stream=codec_type,codec_name,profile,pix_fmt",
            "-of", "json", f"/work/{f.name}"])
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except Exception:
        return "ffprobe 파싱 실패: " + tail(r, 200)
    v = [s for s in streams if s.get("codec_type") == "video"]
    a = [s for s in streams if s.get("codec_type") == "audio"]
    if not v or v[0].get("codec_name") != "h264" or v[0].get("pix_fmt") != "yuv420p":
        return f"영상 스트림이 h264/yuv420p가 아님: {v[:1]}"
    if "10" in (v[0].get("profile") or ""):
        return f"영상 프로파일이 10비트: {v[0].get('profile')}"
    if not a or a[0].get("codec_name") != "aac":
        return f"오디오가 aac가 아님: {a[:1]}"
    return None


def cleanup_all():
    ids = sh(["docker", "ps", "-aq", "--filter", f"name={CONTAINER_PREFIX}"]).stdout.split()
    if ids:
        sh(["docker", "rm", "-f", *ids])


# ---------- 평가 본체 ----------
def evaluate(a, R):
    def gate(name, passed, detail=None):
        R["gates"].append({"gate": name, "passed": bool(passed), "detail": None if passed else detail})

    ensure_image(FFMPEG_IMAGE, FFMPEG_DOCKERFILE)
    ensure_image(BROWSER_IMAGE, BROWSER_DOCKERFILE)
    ensure_fixtures()
    hashes_before = fixture_hashes()

    tag = CONTAINER_PREFIX + safe(a.label)
    b = sh(["docker", "build", "-t", tag, a.repo], timeout=1500)
    gate("빌드", b.returncode == 0, tail(b))
    if b.returncode != 0:
        return

    port, cname = free_port(), tag
    work = Path(tempfile.mkdtemp(prefix="rsi-aviewer-eval-"))
    (work / "probe.cjs").write_text(PROBE_JS)
    try:
        sh(["docker", "rm", "-f", cname])
        r = sh(["docker", "run", "-d", "--name", cname, "-p", f"127.0.0.1:{port}:3000",
                "-v", f"{FIX_DIR}:/app/videos:ro", "-e", "NODE_ENV=production",
                "-e", f"ADMIN_PASSWORD_HASH={make_hash()}", "-e", "SESSION_SECRET_KEY=eval", tag])
        up = False
        if r.returncode == 0:
            for _ in range(90):
                try:
                    conn, resp = req(port, "GET", "/api/auth/check", timeout=2)
                    resp.read()
                    conn.close()
                    if resp.status == 200:
                        up = True
                        break
                except Exception:
                    pass
                time.sleep(0.5)
        gate("서버 기동", up, tail(r) or sh(["docker", "logs", "--tail", "20", cname]).stderr[-400:])
        if not up:
            return

        cookie = login(port)
        ids = {fx: vid(fx) for fx in FIXTURES}

        # 인증
        conn, resp = req(port, "GET", "/videos?limit=40")
        resp.read()
        conn.close()
        gate("인증 유지(목록)", resp.status == 401, f"쿠키 없이 /videos → {resp.status}")

        # 목록 API
        st, items = get_json(port, "/videos?limit=40&sort_by=name", cookie)
        st2, cnt = get_json(port, "/videos/count", cookie)
        got = sorted((i["id"], i["filename"], i["format"]) for i in (items or []))
        exp = sorted((ids[f], f, f.rsplit(".", 1)[1]) for f in FIXTURES)
        gate("목록 API 동일", got == exp and (cnt or {}).get("count") == len(exp),
             f"status={st}/{st2}, 기대={exp}, 실제={got}, count={cnt}")

        # 썸네일·프리뷰 (목록 조회가 생성을 트리거함)
        pending, end = set(ids.values()), time.time() + 120
        while pending and time.time() < end:
            for i in list(pending):
                c1, r1 = req(port, "GET", f"/thumbnail/{i}", cookie=cookie)
                r1.read(); c1.close()
                c2, r2 = req(port, "GET", f"/preview_frames/{i}/4", cookie=cookie)
                r2.read(); c2.close()
                if r1.status == 200 and r2.status == 200:
                    pending.discard(i)
            time.sleep(1)
        gate("썸네일·프리뷰 생성", not pending, f"생성되지 않은 영상 id: {sorted(pending)}")
        wait_ffmpeg_idle(cname, 60)
        diff0 = docker_diff(cname)

        # 재생 시간 측정
        per = {}
        for fx in FIXTURES:
            runs = []
            for k in range(MP4_REPS if fx.endswith(".mp4") else REPS):
                runs.append(run_probe(work, port, fx, f"{safe(os.path.basename(fx))}_{k}"))
                wait_ffmpeg_idle(cname, 15)
            per[fx] = runs

        medians, bd = {}, R["breakdown"]
        for fx, runs in per.items():
            n = os.path.basename(fx).replace(".", "_")
            plays = [span(p, "play") if p.get("ok") and span(p, "play") is not None else PLAY_TIMEOUT_MS
                     for p in runs]
            medians[fx] = statistics.median(plays)
            bd[f"{n}_play_ms"] = round(medians[fx])
            for key in ("meta", "data"):
                vals = [span(p, key) for p in runs if p.get("ok") and span(p, key) is not None]
                if vals:
                    bd[f"{n}_{key}_ms"] = round(statistics.median(vals))
            bd[f"{n}_success"] = sum(1 for p in runs if p.get("ok"))

        mkvs = [fx for fx in FIXTURES if fx.endswith(".mkv")]
        R["objective"] = round(max(medians[fx] for fx in mkvs))
        worst = max(mkvs, key=lambda fx: medians[fx])
        steps = {"가장 느린 영상": f"{worst} 중앙값 {round(medians[worst])}ms (상한 {PLAY_TIMEOUT_MS}ms)"}
        w = os.path.basename(worst).replace(".", "_")
        if f"{w}_meta_ms" in bd:
            steps["클릭→loadedmetadata"] = f"{bd[f'{w}_meta_ms']}ms"
        if f"{w}_data_ms" in bd:
            steps["클릭→loadeddata(첫 프레임)"] = f"{bd[f'{w}_data_ms']}ms"
        fails = [f"{fx}: {p.get('error')} {p.get('state')}" for fx in mkvs for p in per[fx] if not p.get("ok")]
        if fails:
            steps["재생 실패 사례"] = fails[0][:300]
        R["details"]["slowest_steps"] = steps

        def fail_list(fxs):
            return "; ".join(f"{fx}: {p.get('error')}" for fx in fxs for p in per[fx] if not p.get("ok"))[:500]

        # 재생 gate. mkv가 전혀 재생되지 않는 경우는 objective 상한값으로만 반영하고,
        # 일부 반복에서만 재생되는(불안정한) 경우를 gate로 막는다.
        played = {fx: sum(1 for p in per[fx] if p.get("ok")) for fx in mkvs}
        partial = [f"{fx}({played[fx]}/{len(per[fx])}회 재생)" for fx in mkvs if 0 < played[fx] < len(per[fx])]
        gate("mkv 재생 일관성", not partial, f"일부 반복에서만 재생됨: {partial}; " + fail_list(mkvs))
        gate("mp4 실제 재생(회귀 없음)", all(p.get("ok") for p in per["FX/ref.mp4"]), fail_list(["FX/ref.mp4"]))

        # mp4 Range
        conn, resp = req(port, "GET", f"/video/{ids['FX/ref.mp4']}", {"Range": "bytes=0-99"}, cookie=cookie)
        resp.read(100); conn.close()
        cr = resp.getheader("Content-Range") or ""
        gate("mp4 Range 지원", resp.status == 206 and cr.startswith("bytes 0-99/"), f"status={resp.status}, Content-Range={cr}")

        # 스트림 출력 코덱·인증·프로세스 정리 (재생이 한 번이라도 성공한 영상에만 적용)
        stream_problems, leftover, unauth_ok, skipped = [], [], True, []
        for fx in ("FX/hi10_aac.mkv", "FX/hi10_ac3.mkv"):
            if played[fx] == 0:
                skipped.append(fx)  # 재생 불가는 objective 상한값으로 이미 반영됨
                continue
            urls = next((p["mediaUrls"] for p in per[fx] if p.get("ok") and p.get("mediaUrls")), [])
            if not urls:
                stream_problems.append(f"{fx}: 재생은 되지만 미디어 요청이 없음(검증할 스트림 주소를 찾지 못함)")
                continue
            info, data = fetch_sample(port, urls[0], cookie)
            n = os.path.basename(fx).replace(".", "_")
            if info["ttfb_ms"] is not None:
                bd[f"{n}_stream_ttfb_ms"] = round(info["ttfb_ms"])
            if info["status"] not in (200, 206) or not data:
                stream_problems.append(f"{fx}: 스트림 응답 {info}")
            else:
                why = check_stream(work, data, n)
                if why:
                    stream_problems.append(f"{fx}: {why}")
            if not wait_ffmpeg_idle(cname, 10):
                leftover.append(fx)
            sp = urlsplit(urls[0])
            conn, resp = req(port, "GET", sp.path + ("?" + sp.query if sp.query else ""))
            resp.read(1024); conn.close()
            if resp.status != 401:
                unauth_ok = False
        bd["stream_checks_skipped"] = len(skipped)
        gate("스트림 출력 코덱·구조", not stream_problems, "; ".join(stream_problems)[:500])
        gate("인증 유지(스트림)", unauth_ok, "쿠키 없이 스트림 요청이 401이 아님")
        gate("ffmpeg 프로세스 정리", not leftover, f"연결 종료 후에도 ffmpeg이 남음: {leftover}")

        # 추가 디스크 사용
        new = [line for line in docker_diff(cname) - diff0
               if line.startswith("A ") and not line[2:].startswith(ALLOWED_WRITE_PREFIXES)]
        gate("추가 디스크 사용 없음", not new, f"새로 생긴 파일: {new[:5]}")

        gate("원본 불변", fixture_hashes() == hashes_before, "fixture 해시가 변경됨")
    finally:
        sh(["docker", "rm", "-f", cname])
        sh(["docker", "rmi", "-f", tag])
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--out")
    ap.add_argument("--label", default="cand")
    ap.add_argument("--cleanup", action="store_true")
    a = ap.parse_args()
    if a.cleanup:
        cleanup_all()
        return
    if not (a.repo and a.out):
        ap.error("--repo 와 --out 이 필요합니다")
    R = {"objective": PLAY_TIMEOUT_MS, "gates": [], "breakdown": {},
         "details": {"slowest_steps": {}}, "errors": []}
    try:
        evaluate(a, R)
    except Exception as e:
        R["errors"].append(f"{type(e).__name__}: {e}")
        R["gates"].append({"gate": "평가 실행", "passed": False, "detail": f"{type(e).__name__}: {e}"[:500]})
    finally:
        Path(a.out).write_text(json.dumps(R, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
