import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const devPort = Number(process.env.LORECHAT_DEV_PORT || 5173);
if (!Number.isInteger(devPort) || devPort < 1 || devPort > 65535) {
  throw new Error('LORECHAT_DEV_PORT must be an integer between 1 and 65535');
}
const devHost = process.env.LORECHAT_DEV_HOST || '0.0.0.0';
const devApiTarget = process.env.LORECHAT_DEV_API_TARGET || 'http://127.0.0.1:8123';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom')) return 'react';
          if (id.includes('node_modules/@tanstack')) return 'tanstack';
          if (id.includes('node_modules/sweetalert2')) return 'ui';
          if (id.includes('node_modules')) return 'vendor';
        },
      },
    },
  },
  server: {
    host: devHost,
    port: devPort,
    strictPort: true,
    proxy: {
      '/api': {
        target: devApiTarget,
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/uploads': {
        target: devApiTarget,
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: devHost,
    port: devPort,
    strictPort: true,
  },
});
