import { defineConfig } from 'vite';
// /api 프록시: Spring(8080)으로 중계하고, 모델 응답이 긴 채팅 스트림을 위해 시간 제한을 10분으로 둠
// - xfwd: 중계 요청에 X-Forwarded-For 등을 붙여 Spring이 프록시 경유 요청임을 알게 함
//   실험 기록 내보내기(/api/experiments/export)는 이 헤더가 있으면 403으로 막아, 5173으로 접속한 다른 컴퓨터가 답 원문을 받지 못하게 함
export default defineConfig({ server: { port: 5173, strictPort: true, proxy: { '/api': { target: 'http://127.0.0.1:8080', xfwd: true, timeout: 600000, proxyTimeout: 600000 } } } });
