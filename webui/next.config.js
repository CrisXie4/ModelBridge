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

// 静态导出（供 FastAPI 单端口托管）：`MBRIDGE_STATIC=1 next build`，或直接
// `npm run build:static`（跨平台 —— npm 会把脚本名放进 npm_lifecycle_event，
// 免去 Windows 下 set/$env: 设环境变量的麻烦）。产物写入 webui/out。
if (
  process.env.MBRIDGE_STATIC === "1" ||
  process.env.npm_lifecycle_event === "build:static"
) {
  nextConfig.output = "export";
}

module.exports = nextConfig;
