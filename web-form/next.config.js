/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // No /webhooks rewrite: its destination was evaluated at build time
  // (NEXT_PUBLIC_API_URL baked into the standalone config). The web form's
  // only webhook goes through src/app/webhooks/webform/route.ts, which reads
  // API_INTERNAL_URL at runtime.
};

module.exports = nextConfig;
