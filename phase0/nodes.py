"""실행 노드 — 홈서버(local)와 SSH로 붙는 다른 머신(Windows·Linux·Pi)에서 명령을 실행하고 코드를 보냅니다.

노드 정보는 대시보드 DB의 nodes 테이블에 있고, 런 시작 때 오케스트레이터가 Spec 스냅샷의 `nodes:`에 합쳐 넘깁니다
(루프는 DB를 읽지 않음). SSH는 노드 전용 키(~/.ssh/rsi_nodes_ed25519, BatchMode)만 씁니다.

코드 전송: `git archive`(해당 커밋의 추적 파일) → ssh → 원격 `tar -x`. Windows 10 1803+에는 tar.exe가 기본으로 있어 rsync가 필요 없습니다.
"""
import base64
import shlex
import subprocess
from pathlib import Path

KEY_PATH = Path.home() / ".ssh" / "rsi_nodes_ed25519"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]


def ensure_key():
    """노드 전용 SSH 키가 없으면 만들고 공개키를 돌려줍니다(대시보드에서 복사해 노드에 등록)."""
    if not KEY_PATH.exists():
        KEY_PATH.parent.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "rsi-loop-nodes", "-f", str(KEY_PATH)],
                       check=True, capture_output=True)
    return (KEY_PATH.with_suffix(".pub")).read_text().strip()


def _ssh_base(node):
    target = f"{node['user']}@{node['host']}" if node.get("user") else node["host"]
    return ["ssh", "-i", str(KEY_PATH), "-p", str(node.get("port") or 22), *SSH_OPTS, target]


def _wrap(node, command):
    """원격 셸에 맞게 명령을 감쌉니다. Windows 노드는 항상 PowerShell로 실행하고,
    shell은 'SSH 기본 셸'을 뜻합니다: powershell(기본 셸을 PowerShell로 바꾼 경우) | cmd(설치 기본값)."""
    if node.get("shell") in ("powershell", "cmd"):
        # 출력은 UTF-8로(콘솔 기본 CP949면 한글이 깨짐), 진행 표시는 끔(stderr에 CLIXML로 섞여 나옴).
        # Python 자식 프로세스도 UTF-8로 출력하게 합니다.
        command = ("$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                   "$OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONIOENCODING='utf-8'; " + command +
                   # 마지막 외부 프로그램의 종료 코드를 그대로 전달(PowerShell은 기본으로 1로 뭉갬)
                   "; if ($LASTEXITCODE) { exit $LASTEXITCODE }")
        # 따옴표·$ 이스케이프 문제를 피하려고 UTF-16LE base64로 넘깁니다(-EncodedCommand).
        enc = base64.b64encode(command.encode("utf-16-le")).decode()
        inner = f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}"
        # 기본 셸이 PowerShell이면 바깥 셸이 안쪽 종료 코드를 1로 뭉개므로 다시 넘겨줍니다(cmd는 그대로 전달됨).
        return inner + "; exit $LASTEXITCODE" if node.get("shell") == "powershell" else inner
    return command


def _clean_stderr(text):
    """PowerShell이 비대화형 실행에서 stderr로 내보내는 CLIXML 블록을 걷어내고 오류 문장만 남깁니다."""
    if "#< CLIXML" not in text:
        return text
    import re
    errors = re.findall(r'<S S="Error">(.*?)</S>', text, re.S)
    rest = re.sub(r"#< CLIXML\s*<Objs.*?</Objs>", "", text, flags=re.S)
    return ("".join(e.replace("_x000D__x000A_", "\n") for e in errors) + rest).strip()


def run(node, command, timeout=600, stdin=None):
    """노드에서 명령 실행 → (returncode, stdout, stderr). local은 홈서버에서 bash로 실행합니다."""
    if node.get("transport", "local") == "local":
        p = subprocess.run(["bash", "-lc", command], cwd=node.get("workdir") or None, input=stdin,
                           capture_output=True, timeout=timeout)
    else:
        p = subprocess.run(_ssh_base(node) + [_wrap(node, command)], input=stdin, capture_output=True, timeout=timeout)
    out, err = p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace")
    if node.get("os") == "windows":
        out, err = out.replace("\r\n", "\n"), _clean_stderr(err).replace("\r\n", "\n")
    return p.returncode, out, err


def remote_dir(node, name):
    base = (node.get("workdir") or ("C:/rsi-work" if node.get("os") == "windows" else "~/rsi-work")).rstrip("/")
    return f"{base}/{name}"


def sync(node, repo, ref, name):
    """repo의 ref 커밋 파일을 노드의 workdir/name 에 풀어 놓습니다(이전 내용은 지우고). 반환: 원격 경로."""
    dest = remote_dir(node, name)
    if node.get("transport", "local") == "local":
        return str(repo)       # 홈서버는 작업 공간을 그대로 씁니다
    archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", ref], capture_output=True, check=True).stdout
    if node.get("os") == "windows":
        prep = f"Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '{dest}'; New-Item -ItemType Directory -Force -Path '{dest}' | Out-Null"
        rc, _, err = run(node, prep, timeout=120)
        if rc != 0:
            raise RuntimeError(f"원격 폴더 준비 실패: {err[-300:]}")
        p = subprocess.run(_ssh_base(node) + [f'tar -xf - -C "{dest}"'], input=archive, capture_output=True, timeout=600)
    else:
        q = shlex.quote(dest) if not dest.startswith("~") else dest
        p = subprocess.run(_ssh_base(node) + [f"rm -rf {q} && mkdir -p {q} && tar -xf - -C {q}"], input=archive,
                           capture_output=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"코드 전송 실패: {p.stderr.decode(errors='replace')[-300:]}")
    return dest


def send_dir(node, local_dir, dest):
    """로컬 폴더(예: phase0/evals/<폴더>)를 노드의 dest 폴더로 보냅니다(덮어씀). 평가 스크립트는 대상 저장소 밖에 있어 따로 보냅니다."""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(str(local_dir), arcname=".", filter=lambda t: None if "__pycache__" in t.name or t.name.endswith(".pyc") else t)
    if node.get("os") == "windows":
        rc, _, err = run(node, f"Remove-Item -Recurse -Force -ErrorAction SilentlyContinue '{dest}'; "
                               f"New-Item -ItemType Directory -Force -Path '{dest}' | Out-Null", timeout=120)
        if rc != 0:
            raise RuntimeError(f"원격 폴더 준비 실패: {err[-300:]}")
        p = subprocess.run(_ssh_base(node) + [f'tar -xf - -C "{dest}"'], input=buf.getvalue(), capture_output=True, timeout=600)
    else:
        q = shlex.quote(dest) if not dest.startswith("~") else dest
        p = subprocess.run(_ssh_base(node) + [f"rm -rf {q} && mkdir -p {q} && tar -xf - -C {q}"], input=buf.getvalue(),
                           capture_output=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"평가 스크립트 전송 실패: {p.stderr.decode(errors='replace')[-300:]}")


PROBE_LINUX = ("echo OS=$(uname -sr); echo ARCH=$(uname -m); echo CPU=$(nproc); "
               "echo MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo); "
               "echo GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1); "
               "echo TOOLS=$(for t in git python3 node docker; do command -v $t >/dev/null && printf '%s ' $t; done)")
PROBE_WINDOWS = ("$o=Get-CimInstance Win32_OperatingSystem; 'OS=' + $o.Caption + ' ' + $o.Version; "
                 "'ARCH=' + $env:PROCESSOR_ARCHITECTURE; 'CPU=' + $env:NUMBER_OF_PROCESSORS; "
                 "'MEM_MB=' + [int]($o.TotalVisibleMemorySize/1024); "
                 "$g=(Get-CimInstance Win32_VideoController | Select-Object -First 1 -ExpandProperty Name); 'GPU=' + $g; "
                 "'TOOLS=' + ((@('git','python','node','docker') | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue }) -join ' ')")


def probe(node):
    """연결 확인 + 사양 수집. 반환: (online, info dict, error)"""
    try:
        rc, out, err = run(node, PROBE_WINDOWS if node.get("os") == "windows" else PROBE_LINUX, timeout=25)
    except subprocess.TimeoutExpired:
        return False, {}, "시간 초과"
    if rc != 0:
        return False, {}, (err or out).strip()[-300:] or f"종료 코드 {rc}"
    info = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip().lower()] = v.strip()
    return True, info, None
