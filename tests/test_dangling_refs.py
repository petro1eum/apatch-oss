from apatch.dangling_refs import find_dangling_references


def test_find_dangling():
    removed = "const handleOpenDrawer = () => {};\n"
    parent = "export function Page() {\n  return <button onClick={handleOpenDrawer} />;\n}\n"
    dangling = find_dangling_references(removed, parent)
    assert any(d["name"] == "handleOpenDrawer" for d in dangling)
