export default {
  test: {
    exclude: [
      "**/node_modules/**",
      "**/dist/**",
      "**/.next/**",
      "apps/web/e2e/**",
    ],
    passWithNoTests: true,
  },
};
