import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// 홈 화면 앱(PWA) — 서비스 워커는 네트워크 우선이라 배포 후에도 최신 화면을 받습니다.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
