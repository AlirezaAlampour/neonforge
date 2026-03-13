/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  async rewrites() {
    const gateway = process.env.GATEWAY_INTERNAL_URL || 'http://gateway:8000'
    return [
      { source: '/api/v1/:path*', destination: `${gateway}/api/v1/:path*` },
      { source: '/jobs/:path*', destination: `${gateway}/jobs/:path*` },
      { source: '/memory', destination: `${gateway}/memory` },
      { source: '/services/:path*', destination: `${gateway}/services/:path*` },
      { source: '/healthz', destination: `${gateway}/healthz` },
    ]
  },
}

export default nextConfig
