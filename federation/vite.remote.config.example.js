// Ví dụ cấu hình expose PhotoSeries qua Vite Module Federation (@originjs/vite-plugin-federation)
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import federation from '@originjs/vite-plugin-federation';
import fs from 'node:fs';
import path from 'node:path';

function proxyRootRemoteEntry() {
  return {
    name: 'proxy-root-remote-entry',
    closeBundle() {
      const src = path.resolve('dist/assets/remoteEntry.js');
      const dest = path.resolve('dist/remoteEntry.js');
      if (fs.existsSync(src)) {
        fs.writeFileSync(dest, "export { dynamicLoadingCss, get, init } from './assets/remoteEntry.js';\n", 'utf8');
      }
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    federation({
      name: 'printagentx',
      filename: 'remoteEntry.js',
      exposes: {
        './PhotoSeries': './photo_series/PhotoSeriesSection.tsx',
        './PhotoSeriesSection': './photo_series/PhotoSeriesSection.tsx',
      },
      shared: ['react', 'react-dom'],
    }),
    proxyRootRemoteEntry(),
  ],
  build: {
    target: 'esnext',
    cssCodeSplit: true,
    assetsInlineLimit: 32768,
  },
});
