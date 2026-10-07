// Imported without an extension from report.js.
export function pad(text, width) {
  if (text.length >= width) {
    return text;
  }
  return ' '.repeat(width - text.length) + text;
}

export function truncate(text, width) {
  return text.length > width ? `${text.slice(0, width - 1)}…` : text;
}
