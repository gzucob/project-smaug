import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  distDir: process.env.SMAUG_FRONTEND_MODE === "local" ? ".next-local" : ".next",
  experimental: { staleTimes: { dynamic: 60 } },
};

export default nextConfig;
