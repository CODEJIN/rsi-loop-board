import React, { useEffect, useState } from 'react';
import { api } from '../api.js';

// 로그인 3단계: 패스워드 → (TOTP 미등록이면) QR 등록 → 복구 코드 확인 / (등록됨이면) 6자리 코드
export default function Login({ status, onDone }) {
  const [step, setStep] = useState(status?.stage === 'enroll' ? 'enroll' : 'password');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [trust, setTrust] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState(null);
  const [recovery, setRecovery] = useState(null);

  useEffect(() => {
    if (step !== 'enroll' || setup) return;
    api('/auth/totp/setup').then(setSetup).catch((e) => { setError(e.message); setStep('password'); });
  }, [step, setup]);

  const submitLogin = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const r = await api('/auth/login', { method: 'POST', body: { password, code: code || null, trust_device: trust } });
      if (r.next === 'enroll') setStep('enroll');
      else if (r.next === 'code') setStep('code');
      else onDone();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  const submitEnroll = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const r = await api('/auth/totp/verify', { method: 'POST', body: { code } });
      setRecovery(r.recovery_codes);
    } catch (err) { setError(err.message); }
    setBusy(false);
  };

  return (
    <div className="center-screen">
      <div className="card login-card">
        <h1 className="login-title">RSI Loop</h1>

        {recovery ? (
          <>
            <p>복구 코드입니다. 휴대폰을 잃어버렸을 때 OTP 대신 한 번씩 쓸 수 있습니다. <b>지금 안전한 곳에 보관하세요.</b> 다시 볼 수 없습니다.</p>
            <ul className="recovery-codes">{recovery.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
            <button className="primary block" onClick={onDone}>보관했습니다 — 시작하기</button>
          </>
        ) : step === 'enroll' ? (
          <form onSubmit={submitEnroll}>
            <p>처음 로그인입니다. 휴대폰 OTP 앱(Google Authenticator, 1Password 등)으로 QR을 스캔하세요.</p>
            {setup ? (
              <>
                <div className="qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
                <details className="muted small"><summary>QR을 못 찍으면 키 직접 입력</summary><code className="secret">{setup.secret}</code></details>
              </>
            ) : <p className="muted">QR 생성 중…</p>}
            <label className="field">
              <span>앱에 표시된 6자리 코드</span>
              <input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
            </label>
            {error && <p className="error">{error}</p>}
            <button className="primary block" disabled={busy || code.length < 6}>등록 완료</button>
          </form>
        ) : (
          <form onSubmit={submitLogin}>
            <label className="field">
              <span>비밀번호</span>
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus={step === 'password'} />
            </label>
            {(step === 'code' || status?.totp_enabled) && (
              <label className="field">
                <span>OTP 코드 (또는 복구 코드)</span>
                <input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus={step === 'code'} />
              </label>
            )}
            <label className="check">
              <input type="checkbox" checked={trust} onChange={(e) => setTrust(e.target.checked)} />
              이 기기 신뢰 (30일 유지)
            </label>
            {error && <p className="error">{error}</p>}
            <button className="primary block" disabled={busy || !password}>로그인</button>
            {status && !status.configured && <p className="error small">서버에 비밀번호가 설정되지 않았습니다 (.env의 ADMIN_PASSWORD_HASH).</p>}
          </form>
        )}
      </div>
    </div>
  );
}
