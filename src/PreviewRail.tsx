export default function PreviewRail({ html }: { html?: string }) {
  if (!html) return null
  return (
    <iframe
      title="preview"
      sandbox="allow-same-origin"
      className="h-full w-full border-l border-line bg-canvas"
      srcDoc={html}
    />
  )
}
