import type { NextConfig } from "next";

const controlUrl = process.env.CONTROL_URL ?? "http://localhost:8000";

const config: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${controlUrl}/api/:path*` }];
  },
};

export default config;

