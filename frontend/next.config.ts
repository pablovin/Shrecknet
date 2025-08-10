import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  typescript: {
    // Disable type errors failing the build (for dev ONLY)
    ignoreBuildErrors: true,
  },
  eslint: {
    // Disable eslint errors failing the build (for dev ONLY)
    ignoreDuringBuilds: true,
  },
  images: {
    /**
     * Uploaded assets are stored on a shared volume. When running inside
     * Docker the Next.js image optimizer fails to access these files and
     * returns a 400 error. Disabling optimisation avoids the extra API layer
     * and serves files directly from the `public` folder which works with the
     * shared volume.
     */
    unoptimized: true,
  },
};

export default nextConfig;
