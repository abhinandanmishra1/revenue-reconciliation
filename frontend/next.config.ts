import type { NextConfig } from "next";

const apiBase = (process.env.API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // Same-origin proxy: the browser only ever talks to this app, so the session
  // cookie stays first-party and no CORS setup is needed.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }];
  },
};

export default nextConfig;
