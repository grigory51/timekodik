import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist/addon",
    emptyOutDir: false,
    lib: {
      entry: "src/index.ts",
      name: "Timekodik",
      formats: ["iife"],
      fileName: () => "timekodik.js",
    },
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        assetFileNames: (assetInfo) =>
          assetInfo.name?.endsWith(".css")
            ? "timekodik.css"
            : "assets/[name]-[hash][extname]",
      },
    },
  },
});
