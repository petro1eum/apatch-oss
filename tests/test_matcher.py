import os
import pytest
from apatch.matcher import ASTMatcher

def test_exact_match(tmp_path):
    target_file = tmp_path / "main.cpp"
    content = """#include <iostream>
void greet() {
    std::cout << "Hello World!" << std::endl;
}
"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    
    old_str = 'std::cout << "Hello World!" << std::endl;'
    new_str = 'std::cout << "Greetings!" << std::endl;'
    
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    
    assert success
    assert strategy == "exact"
    assert 'std::cout << "Greetings!"' in replaced

def test_whitespace_fuzzy_match(tmp_path):
    target_file = tmp_path / "main.cpp"
    content = """#include <iostream>
void greet() {
    std::cout   <<   "Hello World!" 
        << std::endl;
}
"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    
    # Notice whitespace formatting differs from target_file content
    old_str = 'std::cout << "Hello World!" << std::endl;'
    new_str = 'std::cout << "Greetings!" << std::endl;'
    
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    
    assert success
    assert strategy == "whitespace-fuzzy"
    assert 'std::cout << "Greetings!"' in replaced

def test_ast_fuzzy_match_cpp(tmp_path):
    target_file = tmp_path / "evaluator.cpp"
    # Target file has signature with const reference:
    content = """#include "evaluator.h"
void OlangEvaluator::visit(const MatchExpr& node) {
    print("Executing match evaluation logic");
    resolve_types();
}
"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    
    # AI proposed change had pointer signature, but identical body structures:
    old_str = """void OlangEvaluator::visit(MatchExpr* node) {
    print("Executing match evaluation logic");
    resolve_types();
}"""
    
    new_str = """void OlangEvaluator::visit(MatchExpr* node) {
    // AI proposed fix:
    print("Enhanced evaluation!");
    resolve_types();
    run_surgery();
}"""
    
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    
    assert success
    assert strategy == "ast-fuzzy"
    # Verify that the signature of the target file remains 'const MatchExpr& node'
    assert "void OlangEvaluator::visit(const MatchExpr& node)" in replaced
    # Verify that the new body has been successfully applied
    assert "run_surgery();" in replaced
    assert "print(\"Enhanced evaluation!\");" in replaced

def test_ast_fuzzy_match_js(tmp_path):
    target_file = tmp_path / "component.js"
    # Original target file using standard arrow function
    content = """const renderHeader = (user) => {
    console.log("Rendering header for " + user.name);
    setupTheme();
}"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    
    # AI proposed a standard function declaration with slight parameter drift
    old_str = """function renderHeader(userProfile) {
    console.log("Rendering header for " + user.name);
    setupTheme();
}"""
    
    new_str = """function renderHeader(userProfile) {
    console.log("Rendering header for " + user.name);
    setupTheme();
    injectAnalytics();
}"""
    
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    
    assert success
    assert strategy == "ast-fuzzy"
    # Target signature must be preserved
    assert "const renderHeader = (user) => {" in replaced
    # New logic in body must be applied
    assert "injectAnalytics();" in replaced

def test_ast_fuzzy_match_olang(tmp_path):
    target_file = tmp_path / "ricci_evolution.omega"
    # Target content has an additional comment, triggering context drift
    content = """// O-Lang v5.0 Discrete Ricci Flow Dynamic
main {
    print("--- NEIGHBOR CHECK ---")
    // Target active channel count
    let c = neighbor_count("square_a1")
    flush_epoch_buffers()
}"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    
    # AI proposed change without the target comment
    old_str = """main {
    print("--- NEIGHBOR CHECK ---")
    let c = neighbor_count("square_a1")
    flush_epoch_buffers()
}"""
    
    new_str = """main {
    print("--- NEIGHBOR CHECK ---")
    let c = neighbor_count("square_a1")
    print("Flushing buffers...");
    flush_epoch_buffers()
}"""
    
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    
    assert success
    assert strategy == "ast-fuzzy"
    assert 'print("Flushing buffers...");' in replaced

def test_ast_fuzzy_match_class(tmp_path):
    target_file = tmp_path / "models.py"
    content = """class UserSession:
    # Some class fields
    session_id: str
    
    def get_details(self):
        return "active_user"
"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    matcher = ASTMatcher(str(target_file))
    
    # AI proposes change inside the class body, but signature of class drifted / comments different
    old_str = """class UserSession:
    session_id: str
    def get_details(self):
        return "active_user"
"""
    new_str = """class UserSession:
    session_id: str
    def get_details(self):
        return "active_user_updated"
    def get_role(self):
        return "admin"
"""
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    assert success
    assert strategy == "ast-fuzzy"
    assert "return \"active_user_updated\"" in replaced
    assert "def get_role(self):" in replaced
    assert "# Some class fields" in replaced

def test_exact_replace_all(tmp_path):
    target_file = tmp_path / "kernel.cpp"
    content = """ir += "  %a = getelementptr inbounds float, float* %u, i64 0\\n";
ir += "  %b = load float, float* %a\\n";
ir += "  store float %b, float* %a\\n";
"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    success, replaced, strategy = matcher.apply_patch("float*", "ptr", replace_all=True)

    assert success
    assert strategy == "exact-all"
    assert "float*" not in replaced
    assert replaced.count("ptr %") == 3

def test_exact_first_only_default(tmp_path):
    target_file = tmp_path / "kernel.cpp"
    content = "float* a;\nfloat* b;\n"
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    success, replaced, strategy = matcher.apply_patch("float*", "ptr")

    assert success
    assert strategy == "exact"
    # Only the first occurrence is rewritten when replace_all is not set.
    assert replaced.count("float*") == 1
    assert replaced.count("ptr") == 1

def test_whitespace_fuzzy_replace_all(tmp_path):
    target_file = tmp_path / "kernel.cpp"
    # Two occurrences with drifted internal whitespace.
    content = """load   float,   float* %a
load float,  float*   %b
"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    success, replaced, strategy = matcher.apply_patch(
        "load float, float* %", "load float, ptr %", replace_all=True
    )

    assert success
    assert strategy == "whitespace-fuzzy-all"
    assert "float*" not in replaced
    assert replaced.count("load float, ptr %") == 2

def test_replace_all_fuzzy_preserves_backslashes_in_new_str(tmp_path):
    # Forces the whitespace-fuzzy regex path (drifted spacing) and verifies that a
    # literal backslash-n in new_str is NOT interpreted as a regex group reference.
    target_file = tmp_path / "kernel.cpp"
    content = "call  foo\ncall   foo\n"
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)

    matcher = ASTMatcher(str(target_file))
    success, replaced, strategy = matcher.apply_patch("call foo", "bar\\n", replace_all=True)

    assert success
    assert strategy == "whitespace-fuzzy-all"
    assert replaced.count("bar\\n") == 2

def test_ast_fuzzy_match_control_statement(tmp_path):
    target_file = tmp_path / "logic.py"
    content = """if x == 10:
    print("x is ten")
    log_event()
"""
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(content)
        
    matcher = ASTMatcher(str(target_file))
    
    # Comment drift inside body causes Level 2 whitespace-fuzzy matching to fail
    old_str = """if x == 10:
    # proposed old block has drifted comment
    print("x is ten")
    log_event()
"""
    new_str = """if x == 10:
    # proposed new block
    print("x is ten")
    log_event()
    trigger_alert()
"""
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    assert success
    assert strategy == "ast-fuzzy"
    assert "trigger_alert()" in replaced


def test_evaluate_confidence_exact(tmp_path):
    target = tmp_path / "m.cpp"
    target.write_text("int a = 1;\n", encoding="utf-8")
    matcher = ASTMatcher(str(target))
    result = matcher.evaluate("int a = 1;", "int a = 2;")
    assert result.success
    assert result.strategy == "exact"
    assert result.confidence == 1.0
    assert result.warnings == []


def test_evaluate_signature_change_warning(tmp_path):
    target = tmp_path / "sig.cpp"
    target.write_text(
        "int compute(int x) {\n"
        "    return x + 1;\n"
        "}\n",
        encoding="utf-8",
    )
    matcher = ASTMatcher(str(target))
    # Proposed patch drifts the return type (int -> long) and changes the body.
    old_str = "long compute(int x) {\n    return x + 1;\n}"
    new_str = "long compute(int x) {\n    return x + 5;\n}"
    result = matcher.evaluate(old_str, new_str)
    assert result.success
    assert result.strategy == "ast-fuzzy"
    assert any("signature change ignored" in w for w in result.warnings)
    # Body was swapped; original signature (return type) preserved.
    assert "return x + 5;" in result.content
    assert "int compute(int x)" in result.content


def test_ast_disambiguation_picks_correct_overload(tmp_path):
    target = tmp_path / "dis.cpp"
    target.write_text(
        "struct A {\n"
        "    int run() { return 1; }\n"
        "};\n"
        "struct B {\n"
        "    int run() { return 2; }\n"
        "};\n",
        encoding="utf-8",
    )
    matcher = ASTMatcher(str(target))
    # old body matches B::run (return 2) -> should patch B, not A.
    old_str = "int run() { return 2; }"
    new_str = "int run() { return 22; }"
    success, replaced, strategy = matcher.apply_patch(old_str, new_str)
    assert success
    assert "return 22;" in replaced
    assert "return 1;" in replaced  # A untouched


def test_evaluate_java_body_match(tmp_path):
    from apatch.matcher import load_language
    if load_language("java") is None:
        pytest.skip("tree-sitter-java grammar not installed")
    target = tmp_path / "App.java"
    target.write_text(
        "class App {\n"
        "    int total() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    matcher = ASTMatcher(str(target))
    old_str = "int total() {\n    return 0;\n}"
    new_str = "int total() {\n    return 99;\n}"
    result = matcher.evaluate(old_str, new_str)
    assert result.success
    assert result.strategy == "ast-fuzzy"
    assert "return 99;" in result.content
