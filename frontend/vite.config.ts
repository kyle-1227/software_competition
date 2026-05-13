import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const frontendPort = Number(env.VITE_FRONTEND_PORT || "8001");
  const apiBasePath = env.VITE_API_BASE_PATH || "/api";
  const apiProxyTarget = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      port: frontendPort,
      proxy: {
        [apiBasePath]: {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
