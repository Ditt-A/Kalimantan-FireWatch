import type { NextConfig } from "next";

const internalApiBase = process.env.FIREWATCH_INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: { cpus: 2 },
  async rewrites() {
    return [{
      source: "/api/v1/:path*",
      destination: `${internalApiBase}/api/v1/:path*`,
    }];
  },
};

export default nextConfig;
