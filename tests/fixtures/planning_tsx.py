"""Shared Planning.tsx-style fixture for frontend strip E2E tests."""

PLANNING_TSX = """import React, { useState } from 'react';
import { doWork } from '@/lib/work';

export function Planning() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  // --- HANDLERS START ---
  const handleOpenDrawer = () => {
    setDrawerOpen(true);
    doWork();
  };
  const handleCloseDrawer = () => {
    setDrawerOpen(false);
  };
  // --- HANDLERS END ---
  return (
    <button onClick={handleOpenDrawer}>Open</button>
  );
}
"""

PLANNING_MANIFEST = {
    "strips": [
        {
            "label": "planning_handlers",
            "start": "// --- HANDLERS START ---",
            "until": "// --- HANDLERS END ---",
            "replace": "  // Stubbed: usePlanningHandlers hook\n",
            "export": "planning_handlers.fragment.txt",
            "target_module": "src/hooks/usePlanningHandlers.ts",
            "module_kind": "hook",
            "parent_import": "import { usePlanningHandlers } from '@/hooks/usePlanningHandlers';",
            "route_from": "/legacy/planning",
            "route_to": "/account-planning",
            "verify_command": "npm run build",
        }
    ]
}

TSCONFIG = {
    "compilerOptions": {
        "baseUrl": ".",
        "paths": {"@/*": ["src/*"]},
        "jsx": "react",
        "strict": True,
    }
}
