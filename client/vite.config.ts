import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import {defineConfig} from 'vite'

// https://vite.dev/config/
export default defineConfig({
    plugins: [vue(), tailwindcss()],
    server: {
        watch: {
            usePolling: process.env.VITE_USE_POLLING === 'true',
        },
        proxy: {
            '/api': process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000',
        },
    },
})
