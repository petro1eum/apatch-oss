"""Strip + convert tabItems entries into components."""

import json

from apatch.converters.ts_module import convert_component
from apatch.strip import apply_strip, StripSpec


PAGE = """import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { GreenSheet } from '@/types/sales';

export function Page() {
  const [sheet, setSheet] = useState<GreenSheet | null>(null);
  if (!sheet) return null;
  const updateField = (field: keyof GreenSheet, value: unknown) => {
    setSheet((prev) => (prev ? { ...prev, [field]: value } : null));
  };
  const tabItems = [
    {
      key: 'concept',
      label: '1. Objective',
      children: (
        <div className="wrap">
          <Card><CardContent><textarea value={sheet.title} onChange={(e) => updateField('title', e.target.value)} /></CardContent></Card>
        </div>
      )
    },
    {
      key: 'questions',
      label: '2. Questions',
      children: <div>other</div>
    },
  ];
  return <div>{tabItems[0].children}</div>;
}
"""

REPLACE = """    {
      key: 'concept',
      label: '1. Objective',
      children: (
        <GreenConceptTab sheet={sheet} updateField={updateField} />
      )
    },
    {
"""


def test_strip_and_convert_tab_entry(tmp_path):
    page = tmp_path / "Page.tsx"
    page.write_text(PAGE, encoding="utf-8")
    spec = StripSpec(
        start="      key: 'concept',",
        until="      key: 'questions',",
        replace=REPLACE,
        label="green_concept_tab",
    )
    lines = PAGE.splitlines(keepends=True)
    result = apply_strip(lines, spec)
    assert result.ok, result.error
    joined = "".join(lines)
    assert "GreenConceptTab" in joined
    assert "key: 'questions'" in joined
    assert "sheet.title" not in joined

    out = tmp_path / "GreenConceptTab.tsx"
    convert_component(
        removed_content=result.removed_content,
        label="green_concept_tab",
        parent_imports=[
            "import React, { useState } from 'react';",
            "import { Card, CardContent } from '@/components/ui/card';",
            "import { GreenSheet } from '@/types/sales';",
        ],
        out_path=str(out),
        parent_content=PAGE,
        file_path=str(page),
        manifest_replace="<GreenConceptTab sheet={sheet} updateField={updateField} />",
    )
    text = out.read_text(encoding="utf-8")
    assert "export const GreenConceptTab" in text
    assert "key: 'concept'" not in text
    assert "sheet.title" in text
    assert "GreenSheet" in text
