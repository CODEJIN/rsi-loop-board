"""역할 설정 해석 · 런 사용량 예산 · 규칙 기반 자동 조정기 (docs/ROLES_DESIGN.md §3~4).

설정 우선순위: Spec의 roles/allocation ← 런 시작 시 덮어쓰기(스냅샷에 이미 합쳐짐) ← 실행 중 set_roles(control.json).
자동 조정(auto)은 사용자가 켠 역할과 고른 계층 "이하"에서만 바꿉니다(울타리).
"""
import copy

TIERS = ["light", "standard", "high"]
DEFAULT_TIER_FACTOR = {"high": 5.0, "standard": 1.0, "light": 0.25}
DEFAULT_MODEL_TIERS = {"high": "opus", "standard": "sonnet", "light": "haiku"}
PLANNER_WHEN = ["off", "start_only", "start_plateau", "start_plateau_accept"]
REVIEWER_WHEN = ["off", "on_accept_candidate"]

DEFAULT_ROLES = {
    "planner": {"enabled": True, "when": "start_plateau_accept", "tier": "standard", "timeout_min": 10},
    "implementer": {"enabled": True, "when": "always", "tier": "standard", "timeout_min": 20},
    "reviewer": {"enabled": True, "when": "on_accept_candidate", "tier": "standard", "timeout_min": 5},
}
DEFAULT_ALLOCATION = {"mode": "manual", "run_budget_pct": None, "plateau_after": 2, "attempts_in_prompt": 15}
PRESSURE_UTIL = 0.6
RELIEF_UTIL = 0.3
DEFAULT_ITERATION_COST = 3.0    # %p — 비용 데이터가 없을 때 반복 1회 추정치(첫 런 implementer 기준 2~3%p)


def resolve_roles(spec):
    """roles 키가 없는 Spec은 implementer만(기존 동작). 있으면 기본값 위에 덮어씁니다."""
    if "roles" not in spec:
        roles = {"implementer": dict(DEFAULT_ROLES["implementer"])}
        agent = spec.get("agent", {})
        roles["implementer"]["timeout_min"] = agent.get("timeout_min", 20)
        return roles
    roles = {}
    for name, base in DEFAULT_ROLES.items():
        cfg = dict(base)
        cfg.update(spec["roles"].get(name) or {})
        roles[name] = cfg
    roles["implementer"]["enabled"] = True          # 구현은 끌 수 없음
    roles["implementer"]["when"] = "always"
    return roles


def resolve_allocation(spec):
    alloc = dict(DEFAULT_ALLOCATION)
    alloc.update(spec.get("allocation") or {})
    return alloc


def role_active(roles, name):
    r = roles.get(name)
    return bool(r and r.get("enabled") and r.get("when") != "off")


def model_for(spec, roles, name):
    """계층 → 모델 별칭. implementer는 Spec의 agent.model이 있으면 그것이 우선합니다. None이면 Claude Code 기본 모델."""
    if name == "implementer" and spec.get("agent", {}).get("model"):
        return spec["agent"]["model"]
    tiers = {**DEFAULT_MODEL_TIERS, **(spec.get("model_tiers") or {})}
    return tiers.get(roles[name].get("tier", "standard")) or None


def weighted_tokens(usage, tier, factors=None):
    f = (factors or DEFAULT_TIER_FACTOR).get(tier, 1.0)
    u = usage or {}
    return round((u.get("output_tokens", 0) + 0.25 * u.get("cache_creation_input_tokens", 0)
                  + 0.1 * u.get("input_tokens", 0) + 0.02 * u.get("cache_read_input_tokens", 0)) * f, 1)


class UsageMeter:
    """5시간 창 사용률의 '양수 증가분'만 누적합니다. 창이 리셋되면(resetsAt 변경·값 급락) 그 구간은 0으로 처리합니다."""

    def __init__(self):
        self.last_util = None
        self.last_reset = None
        self.spent = 0.0              # %p
        self.iteration_costs = []     # 반복별 %p

    def observe(self, util, resets_at):
        if util is None:
            return
        if self.last_util is not None:
            reset = (resets_at and self.last_reset and resets_at != self.last_reset) or util < self.last_util - 0.05
            if not reset and util > self.last_util:
                self.spent += (util - self.last_util) * 100
        self.last_util, self.last_reset = util, resets_at or self.last_reset

    def avg_iteration_cost(self):
        recent = [c for c in self.iteration_costs[-3:] if c > 0]
        return sum(recent) / len(recent) if recent else DEFAULT_ITERATION_COST

    def budget_left(self, budget_pct):
        return None if budget_pct is None else budget_pct - self.spent


class AutoAllocator:
    """규칙 기반 조정기 (§4.1). 반환값: [(변경 내용, 이유)] — 루프가 roles_changed 이벤트로 남깁니다."""

    def __init__(self, user_roles):
        self.user_roles = copy.deepcopy(user_roles)   # 울타리: 사용자가 정한 설정
        self.downgraded = False
        self.changed_at = -99
        self.relief_streak = 0
        self.planner_plateau_runs = 0

    def set_user_roles(self, roles):
        self.user_roles = copy.deepcopy(roles)

    def adjust(self, roles, iteration, util, budget_left, budget_pct):
        changes = []
        pressure = (util is not None and util >= PRESSURE_UTIL) or \
            (budget_pct and budget_left is not None and budget_left <= budget_pct * 0.3)
        relief = util is not None and util <= RELIEF_UTIL
        self.relief_streak = self.relief_streak + 1 if relief else 0
        if pressure and not self.downgraded:
            for name in ("planner", "reviewer"):
                if role_active(roles, name):
                    cur = roles[name]["tier"]
                    lower = TIERS[max(0, TIERS.index(cur) - 1)]
                    if lower != cur:
                        roles[name]["tier"] = lower
                        changes.append({"role": name, "tier": [cur, lower]})
            if changes:
                self.downgraded, self.changed_at = True, iteration
                return changes, f"사용량 압박 (5시간 {round((util or 0) * 100)}%"\
                    + (f", 런 예산 잔여 {budget_left:.1f}%p" if budget_left is not None else "") + ") — 보조 역할 계층 하향"
        if self.downgraded and self.relief_streak >= 2 and iteration - self.changed_at >= 2:
            for name in ("planner", "reviewer"):
                if name in roles and name in self.user_roles and roles[name]["tier"] != self.user_roles[name]["tier"]:
                    changes.append({"role": name, "tier": [roles[name]["tier"], self.user_roles[name]["tier"]]})
                    roles[name]["tier"] = self.user_roles[name]["tier"]
            self.downgraded, self.changed_at = False, iteration
            if changes:
                return changes, "사용량 여유 회복 — 사용자 설정 계층으로 복원"
        return [], None
