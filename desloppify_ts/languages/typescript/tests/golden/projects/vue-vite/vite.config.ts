import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'

export default {
  plugins: [vue(), AutoImport({ dirs: ['src/stores'], dts: 'src/auto-imports.d.ts' })],
}
