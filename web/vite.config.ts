import path from "node:path"
import { fileURLToPath, URL } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const dir = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    // `npm run dev` talks to a running tune daemon on the default port
    proxy: { "/api": "http://localhost:8765" },
  },
  build: {
    outDir: path.join(dir, "dist"),
    emptyOutDir: true,
  },
})
