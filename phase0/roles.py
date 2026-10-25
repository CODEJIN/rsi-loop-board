"""역할별 프롬프트와 출력 해석 (docs/ROLES_DESIGN.md §2).

각 역할은 루프가 독립된 Claude Code 세션으로 실행합니다. 출력 형식을 지키지 않으면 역할마다 안전한 기본값으로 처리합니다:
planner → 가설 없음(구현 단독), reviewer → 사람 승인으로 넘김.
"""
import json
import re

READ_ONLY_TOOLS = "Read,Glob,Grep"


def attempts_text(attempts, metric):
    """시도 기록(런을 넘어 누적)을 프롬프트용 텍스트로 — 가설, 결과, 측정 세부 변화가 함께 보이게 합니다."""
    if not attempts:
        return "- (없음)"
    unit = metric.get("unit", "")
    lines = []
    for a in attempts:
        gain = a.get("gain")
        gain_txt = "" if gain is None else (f" · 개선 {gain}{unit}" if gain > 0 else f" · 악화 {-gain}{unit}")
        changes = ", ".join(f"{k} {v:+}" for k, v in (a.get("top_changes") or {}).items())
        extra = f" · {a['failed_gate']} 실패" if a.get("failed_gate") else ""
        if a.get("decision") == "rejected_early":
            extra += f" (조기 탈락: {a.get('stage_reached') or '?'} 단계)"
        review = f" · 검토 {a['review']}" if a.get("review") else ""
        lines.append(f"- [{a.get('run', '')} #{a.get('iteration')}] {a.get('hypothesis') or a.get('summary') or '(요약 없음)'}"
                     f" → {a.get('decision')}{gain_txt}{extra}{review}" + (f" (세부 변화: {changes})" if changes else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- planner

def planner_prompt(spec, best, attempts_txt, used_titles):
    m = spec["metric"]
    better = "낮을수록" if m.get("direction", "minimize") == "minimize" else "높을수록"
    used = "\n".join(f"- {t}" for t in used_titles) or "- (없음)"
    return f"""너는 RSI Loop의 **분석(planner)** 역할이다. 코드를 고치지 않는다. 저장소를 읽고, 다음 반복들에서 시도할 **가설 후보**를 제안한다.
작업 디렉터리는 {spec.get('project', '대상')} 저장소의 격리된 복사본이다.

목표: {spec['target']['goal']}
수정 가능 범위: {', '.join(spec['target']['editable_paths'])}

## 규칙
{spec.get('agent', {}).get('instructions', '').rstrip()}

## 현재 최고 기록
- {m['name']}: {best.get('objective')}{m.get('unit', '')} ({better} 좋음)
- 세부: {json.dumps(best.get('breakdown') or {}, ensure_ascii=False)}
- 힌트: {json.dumps((best.get('details') or {}), ensure_ascii=False)}

## 측정 방법
{spec['evaluation'].get('description', '').rstrip()}

## 배경
{spec.get('agent', {}).get('context', '(없음)').rstrip()}

## 지금까지의 시도 (런을 넘어 누적, 최근 순)
{attempts_txt}

## 이번 런에서 이미 배정한 가설
{used}

## 할 일
- 측정 세부에서 비용이 큰 부분과 관련 코드를 찾아, **서로 다른 접근**의 가설을 3~5개 제안한다.
- 이미 시도해 효과가 없던 방향과 겹치면 overlaps에 그 시도를 적고, 순위를 낮춘다.
- 각 가설은 반복 1회(변경 1건)로 구현할 수 있는 크기여야 한다.
- 마지막에 아래 형식의 ```json 코드 블록 하나만 출력한다(순위 순).
```json
[{{"id": "h1", "title": "한 줄 제목", "rationale": "근거(파일·측정 세부)", "expected_gain": "예상 효과", "risk": "low|medium|high", "files": ["경로"], "overlaps": "겹치는 과거 시도 또는 빈 문자열"}}]
```"""


def parse_hypotheses(text):
    blocks = re.findall(r"```json\s*\n(.*?)```", text or "", re.S)
    for body in reversed(blocks):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            items = [h for h in data if isinstance(h, dict) and h.get("title")]
            for i, h in enumerate(items):
                h.setdefault("id", f"h{i + 1}")
            return items[:5]
    return None


# ---------------------------------------------------------------- reviewer

def reviewer_prompt(spec, diff_text, summary, hypothesis, gain, metric):
    return f"""너는 RSI Loop의 **검토(reviewer)** 역할이다. 코드를 고치지 않는다.
아래 변경은 자동 평가에서 지표가 {gain}{metric.get('unit', '')} 좋아져 **채택 후보**가 됐다. 이 개선이 진짜인지 검사한다.

목표: {spec['target']['goal']}

## 지켜야 할 규칙 (Spec)
{spec.get('agent', {}).get('instructions', '').rstrip()}

## 측정 방법
{spec['evaluation'].get('description', '').rstrip()}

## 구현 요약
- 요약: {summary}
- 가설: {hypothesis}

## 변경 (git diff)
```diff
{diff_text}
```

## 검사 항목
1. **지표 조작**: 측정 조건·입력·시나리오에만 맞춘 처리, 측정 코드 우회, 캐시로 측정을 속이는 등 실제 사용에서는 효과가 없는 변경인가?
2. **불변 조건 위반**: 규칙에 적힌 결과·동작·UI가 바뀔 가능성이 있는가? (자동 gate가 못 잡는 경우 포함: 특정 입력에서만 달라지는 등)
3. **목표와의 관련성**: 목표와 무관한 변경이 섞였는가?
4. **명백한 버그**: 경쟁 상태, 메모리 누수, 갱신이 안 되는 캐시 등.
필요하면 저장소 파일을 읽어 확인한다.

## 출력 (마지막에 정확히 이 형식)
VERDICT: pass 또는 reject
RISK: low, medium, high 중 하나
REASONS:
- 근거 1
- 근거 2"""


def parse_review(text):
    t = text or ""
    verdict = re.search(r"(?im)^\s*VERDICT:\s*(pass|reject)\b", t)
    risk = re.search(r"(?im)^\s*RISK:\s*(low|medium|high)\b", t)
    reasons_block = re.search(r"(?is)REASONS:\s*(.*)$", t)
    reasons = []
    if reasons_block:
        reasons = [l.strip(" -•\t") for l in reasons_block.group(1).splitlines() if l.strip(" -•\t")][:8]
    if not verdict:
        return None
    return {"verdict": verdict.group(1).lower(), "risk": risk.group(1).lower() if risk else "medium", "reasons": reasons}
