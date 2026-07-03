/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  rewrites: async () => {
    return {
      beforeFiles: [
        {
          source: '/webhooks/:path*',
          destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/webhooks/:path*`,
        },
      ],
    };
  },
};

module.exports = nextConfig;
