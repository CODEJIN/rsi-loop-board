import React from 'react';

// 인라인 SVG 아이콘 — 외부 폰트·라이브러리 없이 currentColor로 그립니다(24px 격자, 2px 선).
const P = {
  home: 'M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  inbox: 'M4 13l2-8h12l2 8M4 13v6h16v-6M4 13h5l1 3h4l1-3h5',
  gauge: 'M12 21a9 9 0 1 1 9-9M12 12l5-3M3 12h2M12 3v2M19 12h2',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  file: 'M6 3h9l5 5v13H6zM14 3v6h6M9 13h6M9 17h6',
  chat: 'M4 5h16v11H9l-5 4z',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-1-3-2 .2-1.3-1.3.2-2-3-1-1 2h-1.8l-1-2-3 1 .2 2L6.3 7.2 4.3 7l-1 3 2 1v1.8l-2 1 1 3 2-.2 1.3 1.3-.2 2 3 1 1-2h1.8l1 2 3-1-.2-2 1.3-1.3 2 .2 1-3-2-1z',
  check: 'M5 12l5 5 9-10',
  x: 'M6 6l12 12M18 6L6 18',
  pause: 'M8 5v14M16 5v14',
  play: 'M7 4l13 8-13 8z',
  stop: 'M6 6h12v12H6z',
  skip: 'M5 5l10 7-10 7zM18 5v14',
  plus: 'M12 5v14M5 12h14',
  bulb: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.8.8 1 1.5 1 2.5h6c0-1 .2-1.7 1-2.5A6 6 0 0 0 12 3z',
  code: 'M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16',
  beaker: 'M9 3h6M10 3v6L4 19a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2l-6-10V3M7 14h10',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  gavel: 'M14 4l6 6M11 7l6 6M12.5 5.5l-6 6M4 20l6-6M3 21h8',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  alert: 'M12 3l10 18H2zM12 10v5M12 18v.5',
  chevron: 'M9 6l6 6-6 6',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11',
  trend: 'M3 17l6-6 4 4 8-8M15 7h6v6',
  plug: 'M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0zM12 16v5',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 12h.01',
  folder: 'M3 6h6l2 2h10v11H3z',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7v.5M12 17v.5',
  circle: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  half: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 3v18',
  save: 'M5 3h11l3 3v15H5zM8 3v5h7V3M8 21v-7h8v7',
};

export default function Icon({ name, size = 18, className = '', title }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} className={`icon ${className}`} fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden={title ? undefined : 'true'} role={title ? 'img' : undefined}>
      {title && <title>{title}</title>}
      <path d={P[name] || P.more} />
    </svg>
  );
}
