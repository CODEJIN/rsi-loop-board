"""Spec 버전 관리 — 대시보드 DB가 원본입니다. 저장할 때마다 새 버전이 쌓이고, 기존 버전은 바꾸지 않습니다.

phase0/specs/*.yaml 은 처음 가져오기용(서버 시작 시 DB에 없는 이름·버전만 추가)이고,
런을 시작하면 오케스트레이터가 DB의 해당 버전을 런 폴더에 spec.yaml 로 써서 루프에 넘깁니다.
"""
import os
import re
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, db

SPECS_IMPORT_DIR = Path(os.getenv("SPECS_IMPORT_DIR", "/data/specs"))
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
router = APIRouter(prefix="/api/specs", dependencies=[Depends(auth.require_admin)])


def validate(data) -> list[str]:
    """루프가 실제로 돌 수 있는지 필수 항목을 검사합니다. 오류 메시지 목록(비어 있으면 통과)."""
    errs = []
    if not isinstance(data, dict):
        return ["YAML 최상위가 매핑(키: 값)이어야 합니다."]
    g = lambda path: _get(data, path)
    if not NAME_RE.match(str(g("spec") or "")):
        errs.append("spec: 소문자·숫자·하이픈으로 된 이름이 필요합니다 (예: astro-tonight-filter-speed)")
    for path in ("workspace.repo", "workspace.best_branch", "workspace.base_ref", "target.goal",
                 "evaluation.command", "metric.name"):
        if not g(path):
            errs.append(f"{path}: 필수 항목입니다")
    paths = g("target.editable_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
        errs.append("target.editable_paths: 수정 가능한 경로(glob) 목록이 필요합니다")
    if g("metric.direction") not in ("minimize", "maximize"):
        errs.append("metric.direction: minimize 또는 maximize")
    nt = g("metric.noise_threshold")
    if nt is not None and not isinstance(nt, (int, float)):
        errs.append("metric.noise_threshold: 숫자여야 합니다 (모르면 비워두고 검증 런으로 측정)")
    if (g("judge.policy") or "auto") not in ("auto", "human", "hybrid"):
        errs.append("judge.policy: auto, human, hybrid 중 하나")
    errs += validate_roles(data.get("roles"), data.get("allocation"))
    node = g("evaluation.node")
    if node is not None and not isinstance(node, str):
        errs.append("evaluation.node: 노드 ID 또는 'tag:이름'")
    matrix = g("evaluation.matrix")
    if matrix is not None:
        if not isinstance(matrix, list) or not all(isinstance(m, dict) and m.get("node") and m.get("command") for m in matrix):
            errs.append("evaluation.matrix: [{node, command, name?, timeout_min?}] 목록")
    stages = g("evaluation.stages")
    if stages is not None:
        if not isinstance(stages, list) or not stages or not all(isinstance(x, dict) and x.get("name") for x in stages):
            errs.append("evaluation.stages: [{name, args?, timeout_min?, promote_within_pct?, early_stop?}] 목록")
        elif len({x["name"] for x in stages}) != len(stages):
            errs.append("evaluation.stages: 단계 이름이 겹칩니다")
        else:
            for x in stages:
                es = x.get("early_stop")
                if es is not None and not (isinstance(es, dict) and es.get("direction", "minimize") in ("minimize", "maximize")):
                    errs.append(f"evaluation.stages[{x['name']}].early_stop: {{after_pct|after_step, worse_pct, patience, direction}}")
                if x.get("promote_within_pct") is not None and not isinstance(x["promote_within_pct"], (int, float)):
                    errs.append(f"evaluation.stages[{x['name']}].promote_within_pct: 숫자(%)")
    mi = g("budget.max_iterations")
    if mi is not None and not (isinstance(mi, int) and 1 <= mi <= 50):
        errs.append("budget.max_iterations: 1~50")
    return errs


ROLE_WHEN = {"planner": {"off", "start_only", "start_plateau", "start_plateau_accept"},
             "reviewer": {"off", "on_accept_candidate"}, "implementer": {"always"}}
TIERS = {"light", "standard", "high"}


def validate_roles(roles, allocation):
    """역할·할당 설정 검사 (docs/ROLES_DESIGN.md §3.2). roles가 없으면 구현 역할만 도는 기존 동작."""
    errs = []
    if roles is not None:
        if not isinstance(roles, dict):
            return ["roles: 역할 이름을 키로 하는 매핑이어야 합니다"]
        for name, cfg in roles.items():
            if name not in ROLE_WHEN:
                errs.append(f"roles.{name}: 알 수 없는 역할 (planner, implementer, reviewer)")
                continue
            if not isinstance(cfg, dict):
                errs.append(f"roles.{name}: 매핑이어야 합니다")
                continue
            if "when" in cfg and cfg["when"] not in ROLE_WHEN[name]:
                errs.append(f"roles.{name}.when: {', '.join(sorted(ROLE_WHEN[name]))} 중 하나")
            if "tier" in cfg and cfg["tier"] not in TIERS:
                errs.append(f"roles.{name}.tier: light, standard, high 중 하나")
            if name == "implementer" and cfg.get("enabled") is False:
                errs.append("roles.implementer: 구현 역할은 끌 수 없습니다")
    if allocation is not None:
        if not isinstance(allocation, dict):
            return errs + ["allocation: 매핑이어야 합니다"]
        if allocation.get("mode", "manual") not in ("manual", "auto"):
            errs.append("allocation.mode: manual 또는 auto")
        b = allocation.get("run_budget_pct")
        if b is not None and not (isinstance(b, (int, float)) and 0 < b <= 100):
            errs.append("allocation.run_budget_pct: 0~100 사이 숫자(%p) 또는 비움")
    return errs


def _get(d, path):
    for k in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _set_version(text: str, version: int) -> str:
    """주석을 지키기 위해 YAML을 다시 직렬화하지 않고 version 줄만 바꿉니다."""
    if re.search(r"(?m)^version:\s*.*$", text):
        return re.sub(r"(?m)^version:\s*.*$", f"version: {version}", text, count=1)
    return re.sub(r"(?m)^(spec:.*)$", rf"\1\nversion: {version}", text, count=1)


def save(text: str, note: str | None, source: str):
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HTTPException(400, f"YAML 문법 오류: {e}")
    errs = validate(data)
    if errs:
        raise HTTPException(400, {"message": "Spec 검사 실패", "errors": errs})
    name = data["spec"]
    with db.connect() as conn:
        latest = conn.execute("SELECT MAX(version) FROM spec_versions WHERE name=?", (name,)).fetchone()[0] or 0
        version = latest + 1
        text = _set_version(text, version)
        conn.execute("INSERT INTO spec_versions(name, version, yaml, title, note, source, created_at) VALUES (?,?,?,?,?,?,?)",
                     (name, version, text, data.get("title"), note, source, datetime.now().isoformat(timespec="seconds")))
    return {"name": name, "version": version}


def import_files():
    if not SPECS_IMPORT_DIR.exists():
        return
    with db.connect() as conn:
        for p in sorted(SPECS_IMPORT_DIR.glob("*.yaml")):
            text = p.read_text()
            data = yaml.safe_load(text)
            if validate(data):
                continue
            exists = conn.execute("SELECT 1 FROM spec_versions WHERE name=? AND version=?",
                                  (data["spec"], data.get("version", 1))).fetchone()
            if not exists:
                conn.execute("INSERT INTO spec_versions(name, version, yaml, title, note, source, created_at) VALUES (?,?,?,?,?,?,?)",
                             (data["spec"], data.get("version", 1), text, data.get("title"), data.get("note"),
                              f"file:{p.name}", datetime.now().isoformat(timespec="seconds")))


def summary(row):
    data = yaml.safe_load(row["yaml"]) or {}
    return {
        "name": row["name"], "version": row["version"], "title": row["title"], "note": row["note"],
        "source": row["source"], "created_at": row["created_at"],
        "goal": _get(data, "target.goal"), "metric": data.get("metric"), "judge": _get(data, "judge.policy") or "auto",
        "max_iterations": _get(data, "budget.max_iterations"), "project": data.get("project"),
    }


def get_version(name, version=None):
    with db.connect() as conn:
        if version:
            row = conn.execute("SELECT * FROM spec_versions WHERE name=? AND version=?", (name, version)).fetchone()
        else:
            row = conn.execute("SELECT * FROM spec_versions WHERE name=? ORDER BY version DESC LIMIT 1", (name,)).fetchone()
    return row


class SaveRequest(BaseModel):
    yaml: str
    note: str | None = None


@router.get("")
def list_specs():
    with db.connect() as conn:
        rows = conn.execute("SELECT s.* FROM spec_versions s JOIN (SELECT name, MAX(version) v FROM spec_versions GROUP BY name) m "
                            "ON s.name = m.name AND s.version = m.v ORDER BY s.created_at DESC").fetchall()
        runs = {r["spec"]: dict(r) for r in conn.execute(
            "SELECT spec, COUNT(*) AS runs, MAX(started_at) AS last_run FROM runs GROUP BY spec")}
    return [{**summary(r), **{k: runs.get(r["name"], {}).get(k) for k in ("runs", "last_run")}} for r in rows]


@router.get("/{name}")
def spec_versions(name: str):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM spec_versions WHERE name=? ORDER BY version DESC", (name,)).fetchall()
        runs = [dict(r) for r in conn.execute(
            "SELECT id, spec_version, status, started_at, baseline_objective, best_objective, dry_run FROM runs "
            "WHERE spec=? ORDER BY started_at DESC LIMIT 30", (name,))]
    if not rows:
        raise HTTPException(404, "Spec을 찾을 수 없습니다.")
    return {"versions": [summary(r) for r in rows], "runs": runs}


@router.get("/{name}/{version}")
def spec_version(name: str, version: int):
    row = get_version(name, version)
    if not row:
        raise HTTPException(404, "해당 버전이 없습니다.")
    return {**summary(row), "yaml": row["yaml"], "data": yaml.safe_load(row["yaml"])}


@router.post("")
def save_spec(req: SaveRequest):
    return save(req.yaml, req.note, "dashboard")


@router.post("/validate")
def validate_spec(req: SaveRequest):
    try:
        data = yaml.safe_load(req.yaml)
    except yaml.YAMLError as e:
        return {"ok": False, "errors": [f"YAML 문법 오류: {e}"]}
    errs = validate(data)
    return {"ok": not errs, "errors": errs}
