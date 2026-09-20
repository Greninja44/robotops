# RobotOps dashboard

React 19 + TypeScript + Vite. The graph is drawn with `@xyflow/react`. The backend serves the production build from
`frontend/dist` on port 8000, so normally you do not run this directly (`./run_demo.sh` builds it).

```bash
npm install
npm run build      # tsc -b && vite build   (type-check + bundle)
npm run lint       # oxlint
npm run dev        # Vite dev server with HMR; expects the backend on :8000
```

Source layout: `src/App.tsx` (state and layout), `src/api.ts` (REST + WebSocket client), `src/components/` (panels), `src/lib/` (pure helpers).
The panels expose `data-testid` / `data-state` attributes that the UI harnesses in `scripts/ui_*.py` rely on.
