import React from 'react';

// 설계 대화용 최소 마크다운 렌더러 — 코드 블록, 인라인 코드, 굵게, 제목만 처리하고 나머지는 줄바꿈을 그대로 보여줍니다.
// (외부 라이브러리 없이, dangerouslySetInnerHTML도 쓰지 않습니다.)
const inline = (text, keyBase) => {
  const out = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0, m, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    out.push(tok.startsWith('`') ? <code key={`${keyBase}-${i++}`} className="inline-code">{tok.slice(1, -1)}</code>
      : <b key={`${keyBase}-${i++}`}>{tok.slice(2, -2)}</b>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
};

export default function Markdown({ text }) {
  const parts = text.split(/```(\w+)?\n([\s\S]*?)```/g);
  const nodes = [];
  for (let i = 0; i < parts.length; i += 3) {
    const prose = parts[i];
    if (prose) {
      prose.split('\n').forEach((line, j) => {
        const h = line.match(/^(#{1,4})\s+(.*)$/);
        if (h) nodes.push(<div key={`h${i}-${j}`} className="md-heading">{inline(h[2], `h${i}-${j}`)}</div>);
        else nodes.push(<div key={`p${i}-${j}`} className="md-line">{line ? inline(line, `p${i}-${j}`) : ' '}</div>);
      });
    }
    if (i + 2 < parts.length) {
      nodes.push(<pre key={`c${i}`} className="md-code"><span className="md-lang">{parts[i + 1] || ''}</span>{parts[i + 2]}</pre>);
    }
  }
  return <div className="md">{nodes}</div>;
}
