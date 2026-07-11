// A file under src/routes/ maps to a URL by mirroring its own path there:
// src/routes/investments/allocation.tsx -> /investments/allocation, and
// an `index.tsx` serves its parent directory's own path. See App.tsx's
// `import.meta.glob('./routes/**/*.tsx')` for where this is applied.
export function routePathFromFile(file: string): string {
  const withoutExtension = file.replace(/^\.\/routes/, '').replace(/\.tsx$/, '')
  return withoutExtension.replace(/\/index$/, '') || '/'
}
