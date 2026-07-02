/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        status: {
          open: '#3b82f6',
          in_progress: '#eab308',
          resolved: '#22c55e',
          escalated: '#ef4444',
          closed: '#9ca3af',
        },
      },
    },
  },
  plugins: [],
};
