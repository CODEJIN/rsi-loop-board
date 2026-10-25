// Spec YAML 텍스트를 주석을 지키면서 일부 값만 바꾸는 도우미 — js-yaml로 다시 직렬화하면 주석이 사라지므로,
// 최상위 섹션(예: metric:) 블록 안의 "  key: 값" 한 줄만 고치거나 없으면 섹션 바로 아래에 추가합니다.
export function setSectionField(text, section, key, value) {
  const lines = text.split('\n');
  const start = lines.findIndex((l) => l.startsWith(`${section}:`));
  const rendered = `  ${key}: ${value}`;
  if (start === -1) return `${text.replace(/\n*$/, '\n')}\n${section}:\n${rendered}\n`;
  let end = start + 1;
  while (end < lines.length && (lines[end].startsWith(' ') || lines[end].trim() === '')) end++;
  const idx = lines.slice(start + 1, end).findIndex((l) => new RegExp(`^\\s+${key}:`).test(l));
  if (idx >= 0) {
    const line = lines[start + 1 + idx];
    const comment = line.match(/\s+#.*$/)?.[0] || '';
    lines[start + 1 + idx] = rendered + comment;
  } else {
    lines.splice(start + 1, 0, rendered);
  }
  return lines.join('\n');
}

// 줄 단위 차이(LCS) — 버전 비교 화면용. 결과: [{type: 'same'|'add'|'del', text}]
export function lineDiff(a, b) {
  const x = a.split('\n'), y = b.split('\n');
  const m = x.length, n = y.length;
  const dp = Array.from({ length: m + 1 }, () => new Int32Array(n + 1));
  for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) {
    dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  }
  const out = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (x[i] === y[j]) { out.push({ type: 'same', text: x[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ type: 'del', text: x[i++] });
    else out.push({ type: 'add', text: y[j++] });
  }
  while (i < m) out.push({ type: 'del', text: x[i++] });
  while (j < n) out.push({ type: 'add', text: y[j++] });
  return out;
}
