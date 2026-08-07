/** @type {import('next').NextConfig} */
const nextConfig = {
  // 开发模式 (npm run dev :3000) 通过 rewrites 把 /api/* 代理到 FastAPI (:8765)。
  // 生产托管：FastAPI (mbridge web) 直接挂载 webui/.next 或 webui/out。
  images: { unoptimized: true },
  async rewrites() {
    const api = process.env.MBRIDGE_API || "http://127.0.0.1:8765";
    return [
      {
        source: "/api/:path*",
        destination: `${api}/api/:path*`,
      },
    ];
  },
};

// 可选：静态导出（MBRIDGE_STATIC=1 next build）让 FastAPI 单端口托管。
if (process.env.MBRIDGE_STATIC === "1") {
  nextConfig.output = "export";
}

module.exports = nextConfig;
