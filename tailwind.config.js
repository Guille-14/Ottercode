/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: 'var(--oc-bg)',
        panel: 'var(--oc-surface)',
        panel2: 'var(--oc-surface2)',
        line: 'var(--oc-border)',
        line2: 'var(--oc-border2)',
        ink: 'var(--oc-text1)',
        ink2: 'var(--oc-text2)',
        muted: 'var(--oc-text3)',
        accent: 'var(--oc-accent)',
        accentink: 'var(--oc-accent-ink)',
        success: 'var(--oc-green)',
        danger: 'var(--oc-red)',
        warning: 'var(--oc-orange)',
        codebg: 'var(--oc-code-bg)',
        codefg: 'var(--oc-code-fg)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SF Mono', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        card: '10px',
        pill: '999px',
      },
    },
  },
  plugins: [],
};