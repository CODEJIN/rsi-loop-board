(2026-10-25, 사람이 사전 분석한 내용)

- 무거운 조작은 "시야 필터(방위·고도)가 처음 켜지는 순간"이다. 전체 목록(약 600행)에서 방위 하나를 끄거나,
  450행 상태에서 고도 상한을 처음 바꿀 때 각각 0.45~0.65초 동안 메인 스레드가 막힌다.
  카테고리 토글은 450행이어도 30ms 수준으로 빠르다.
- CPU 프로파일(전체 목록에서 방위 N 토글, 약 825ms): 대부분이 Recharts 내부
  (updateStateOfAxisMapsOffsetAndStackGroups, formatAxisMap, getTicksOfScale, Decimal 연산, Curve/getPath,
  filterProps 등)이다. 즉 각 행(TonightPickRow)의 미니 고도 차트 수백 개가 다시 렌더링되는 비용이다.
  천문 계산(altitudeAtTime, TonightPickRow 본문)은 합쳐서 50ms 안팎이다.
- 원인으로 보이는 구조: 렌더 시점에 계산되는 `skyWindowMinutes` prop이 시야 모드에 들어가는 순간 모든 행에서
  null에서 숫자로 바뀐다. 그러면 React.memo가 무력화되어 행 전체와 차트가 다시 렌더링된다. 행 안의 차트 데이터
  (고도 곡선, 박명 구간)는 이 prop과 무관한데도 매번 다시 만들어진다.
- 참고: 태양 고도(sunAltitudeAtTime)는 모든 행에 공통인데 행마다 시간 격자 전체에 대해 다시 계산한다.
