/// <reference types="vite/client" />

declare module 'plotly.js-dist-min' {
  const Plotly: {
    react: (root: HTMLElement, data: unknown[], layout?: object, config?: object) => Promise<unknown>
    purge: (root: HTMLElement) => void
    Plots: { resize: (root: HTMLElement) => void }
  }
  export default Plotly
}
