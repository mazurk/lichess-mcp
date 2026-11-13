import js from "@eslint/js";

export default [
  js.configs.recommended,
  {
    ignores: ["node_modules/**", "web/pieces.js"],
  },
  {
    files: ["web/util.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "script",
      globals: {
        module: "readonly",
        require: "readonly",
      },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["web/app.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "script",
      globals: {
        document: "readonly",
        window: "readonly",
        localStorage: "readonly",
        fetch: "readonly",
        prompt: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        encodeURIComponent: "readonly",
        // fonctions chargées par les autres balises <script> (util.js, pieces.js)
        escapeHtml: "readonly",
        isStaleResponse: "readonly",
        pieceGlyph: "readonly",
        pieceImgSrc: "readonly",
        PIECE_SVG: "readonly",
      },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["tests/web/**/*.mjs", "eslint.config.js"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: {
        // Navigateur
        document: "readonly",
        window: "readonly",
        localStorage: "readonly",
        fetch: "readonly",
        prompt: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        encodeURIComponent: "readonly",
      },
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
];
