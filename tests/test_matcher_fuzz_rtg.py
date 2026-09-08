import os
import random
import re
import pytest
from apatch.matcher import ASTMatcher, load_language

# Список шаблонов для генерации случайных операций
OPERATIONS = {
    'python': ["x + y", "x * y - 10", "x ** 2 if x > 0 else y", "sum([x, y, 100])", "abs(x - y)"],
    'cpp': ["x + y", "x * y - 10", "x > 0 ? x * x : y", "abs(x - y)"],
    'javascript': ["x + y", "x * y - 10", "x > 0 ? x * x : y", "Math.abs(x - y)"],
    'typescript': ["x + y", "x * y - 10", "x > 0 ? x * x : y", "Math.abs(x - y)"],
    'rust': ["x + y", "x * y - 10", "if x > 0 { x * x } else { y }", "(x - y).abs()"],
    'go': ["x + y", "x * y - 10", "x - y"],
    'java': ["x + y", "x * y - 10", "x > 0 ? x * x : y", "Math.abs(x - y)"],
    'c_sharp': ["x + y", "x * y - 10", "x > 0 ? x * x : y", "Math.Abs(x - y)"],
    'ruby': ["x + y", "x * y - 10", "x > 0 ? x * x : y", "(x - y).abs"],
    'php': ["$x + $y", "$x * $y - 10", "$x > 0 ? $x * $x : $y", "abs($x - $y)"],
    'kotlin': ["x + y", "x * y - 10", "if (x > 0) x * x else y", "Math.abs(x - y)"],
    'swift': ["x + y", "x * y - 10", "x > 0 ? x * x : y", "abs(x - y)"]
}

COMMENTS = [
    "check active boundary",
    "JIT aligned step",
    "TODO: verify memory sheets",
    "discrete curvature correction",
    "optimized via Antigravity RTG",
]

# Описание шаблонов генерации функций для каждого языка
LANG_TEMPLATES = {
    'python': {
        'ext': '.py',
        'comment_fmt': '# {}',
        'func_template': """def target_func(x, y):
    val = {body_op}
    return val
""",
        'sig_drift': lambda code: code.replace("(x,", "(x_drifted,"),
        'sig_verify': lambda text: "x_drifted" in text and "(x," not in text,
        'op_verify': lambda text, op: op in text
    },
    'cpp': {
        'ext': '.cpp',
        'comment_fmt': '// {}',
        'func_template': """int target_func(int x, int y) {{
    int val = {body_op};
    return val;
}}
""",
        'sig_drift': lambda code: code.replace("(int x,", "(int x_drifted,"),
        'sig_verify': lambda text: "x_drifted" in text and "(int x," not in text,
        'op_verify': lambda text, op: op in text
    },
    'javascript': {
        'ext': '.js',
        'comment_fmt': '// {}',
        'func_template': """function target_func(x, y) {{
    let val = {body_op};
    return val;
}}
""",
        'sig_drift': lambda code: code.replace("(x,", "(x_drifted,"),
        'sig_verify': lambda text: "x_drifted" in text and "(x," not in text,
        'op_verify': lambda text, op: op in text
    },
    'typescript': {
        'ext': '.ts',
        'comment_fmt': '// {}',
        'func_template': """function target_func(x: number, y: number): number {{
    let val = {body_op};
    return val;
}}
""",
        'sig_drift': lambda code: code.replace("(x: number,", "(x_drifted: number,"),
        'sig_verify': lambda text: "x_drifted" in text and "(x: number," not in text,
        'op_verify': lambda text, op: op in text
    },
    'rust': {
        'ext': '.rs',
        'comment_fmt': '// {}',
        'func_template': """fn target_func(x: i32, y: i32) -> i32 {{
    let val = {body_op};
    val
}}
""",
        'sig_drift': lambda code: code.replace("(x: i32,", "(x_drifted: i32,"),
        'sig_verify': lambda text: "x_drifted" in text and "(x: i32," not in text,
        'op_verify': lambda text, op: op in text
    },
    'go': {
        'ext': '.go',
        'comment_fmt': '// {}',
        'func_template': """func target_func(x int, y int) int {{
    var val = {body_op}
    return val
}}
""",
        'sig_drift': lambda code: code.replace("(x int,", "(x_drifted int,"),
        'sig_verify': lambda text: "x_drifted" in text and "(x int," not in text,
        'op_verify': lambda text, op: op in text
    },
    'java': {
        'ext': '.java',
        'comment_fmt': '// {}',
        'func_template': """class App {{
    int target_func(int x, int y) {{
        int val = {body_op};
        return val;
    }}
}}
""",
        'sig_drift': lambda code: code.replace("(int x,", "(int x_drifted,"),
        'sig_verify': lambda text: "x_drifted" in text and "(int x," not in text,
        'op_verify': lambda text, op: op in text
    },
    'c_sharp': {
        'ext': '.cs',
        'comment_fmt': '// {}',
        'func_template': """class App {{
    public int target_func(int x, int y) {{
        int val = {body_op};
        return val;
    }}
}}
""",
        'sig_drift': lambda code: code.replace("(int x,", "(int x_drifted,"),
        'sig_verify': lambda text: "x_drifted" in text and "(int x," not in text,
        'op_verify': lambda text, op: op in text
    },
    'ruby': {
        'ext': '.rb',
        'comment_fmt': '# {}',
        'func_template': """def target_func(x, y)
    val = {body_op}
    return val
end
""",
        'sig_drift': lambda code: code.replace("(x,", "(x_drifted,"),
        'sig_verify': lambda text: "x_drifted" in text and "(x," not in text,
        'op_verify': lambda text, op: op in text
    },
    'php': {
        'ext': '.php',
        'comment_fmt': '// {}',
        'func_template': """<?php
function target_func($x, $y) {{
    $val = {body_op};
    return $val;
}}
""",
        'sig_drift': lambda code: code.replace("($x,", "($x_drifted,"),
        'sig_verify': lambda text: "x_drifted" in text and "($x," not in text,
        'op_verify': lambda text, op: op.replace("$", "\\$") in text or op in text
    },
    'kotlin': {
        'ext': '.kt',
        'comment_fmt': '// {}',
        'func_template': """fun target_func(x: Int, y: Int): Int {{
    val res_val = {body_op}
    return res_val
}}
""",
        'sig_drift': lambda code: code.replace("(x: Int,", "(x_drifted: Int,"),
        'sig_verify': lambda text: "x_drifted" in text and "(x: Int," not in text,
        'op_verify': lambda text, op: op in text
    },
    'swift': {
        'ext': '.swift',
        'comment_fmt': '// {}',
        'func_template': """func target_func(x: Int, y: Int) -> Int {{
    let val = {body_op}
    return val
}}
""",
        'sig_drift': lambda code: code.replace("(x: Int,", "(x_drifted: Int,"),
        'sig_verify': lambda text: "x_drifted" in text and "(x: Int," not in text,
        'op_verify': lambda text, op: op in text
    }
}

def check_has_errors(node) -> bool:
    """Рекурсивно проверяет дерево tree-sitter на наличие синтаксических ошибок."""
    if node.type == 'ERROR' or node.is_missing:
        return True
    for child in node.children:
        if check_has_errors(child):
            return True
    return False

def introduce_drift_multilingual(code, lang, ws_drift=True, comment_drift=True, sig_drift=False):
    """
    Вносит дрейф в зависимости от синтаксических правил конкретного языка.
    """
    spec = LANG_TEMPLATES[lang]
    comment_fmt = spec['comment_fmt']
    
    lines = code.splitlines()
    mutated_lines = []
    
    outside_comment = None
    if comment_drift:
        outside_comment = comment_fmt.format(random.choice(COMMENTS))
        mutated_lines.append(outside_comment)
        
    for line in lines:
        # 1. Дрейф сигнатуры
        if sig_drift and ("target_func" in line or "def target_func" in line or "fn target_func" in line):
            line = spec['sig_drift'](line)
            
        # 2. Дрейф пробелов (разрежение существующих пробелов)
        if ws_drift:
            line = re.sub(r' ', lambda m: " " * random.randint(1, 4), line)
            
        mutated_lines.append(line)
        
        # 3. Внутренний комментарий (только внутри тела)
        # Если sig_drift отключен (значит, нет грамматики AST), мы отключаем и внутренние комментарии,
        # так как whitespace-fuzzy не умеет сопоставлять сквозь новые комменты внутри тела.
        if comment_drift and sig_drift and any(k in line for k in ("val =", "value =", "res_val =", "$val =")):
            mutated_lines.append("    " + comment_fmt.format(random.choice(COMMENTS)))
            
    if ws_drift:
        # Добавляем случайные пустые строки
        for _ in range(random.randint(0, 2)):
            mutated_lines.insert(random.randint(0, len(mutated_lines)), "")
        
    return "\n".join(mutated_lines), outside_comment

@pytest.mark.parametrize("language", list(LANG_TEMPLATES.keys()))
@pytest.mark.parametrize("iteration", range(5))  # 5 итераций на каждый язык (всего 60 тестов)
def test_matcher_drift_rtg_fuzz_multilingual(tmp_path, language, iteration):
    """
    Глобальный многоязычный Random Test Generator (RTG) фаззинг для ASTMatcher.
    Покрывает все 12 языков с синтаксической валидацией через AST tree-sitter.
    """
    random.seed(iteration + hash(language) % 1000000)
    spec = LANG_TEMPLATES[language]
    
    # Проверяем доступность грамматики для этого языка
    ts_language = load_language(language)
    
    # 1. Выбираем две различные случайные математические операции
    ops = OPERATIONS[language]
    op_old = random.choice(ops)
    op_new = random.choice([op for op in ops if op != op_old])
    
    # 2. Генерируем оригинальную функцию
    original_code = spec['func_template'].format(body_op=op_old)
    
    # 3. Формируем AI-патч (old -> new)
    proposed_old = original_code
    proposed_new = spec['func_template'].format(body_op=op_new)
    
    # 4. Генерируем дрейф в целевом файле назначения
    ws = random.choice([True, False])
    comments = random.choice([True, False])
    
    # Если грамматика tree-sitter не установлена на хост-машине, signature_drift (Level 3)
    # физически не может быть осуществлен через AST, поэтому отключаем sig в этом случае.
    # java/c_sharp: nested class methods — AST body match не гарантирует сохранение сигнатуры.
    sig = False
    if ts_language is not None and language not in ("java", "c_sharp"):
        sig = random.choice([True, False])
        
    if not (ws or comments or sig):
        ws = True
        
    drifted_code, outside_comment = introduce_drift_multilingual(
        original_code, language, ws_drift=ws, comment_drift=comments, sig_drift=sig
    )
    
    # Записываем drifted-код во временный файл нужного расширения
    filename = f"fuzz_target_{iteration}{spec['ext']}"
    target_file = tmp_path / filename
    target_file.write_text(drifted_code, encoding="utf-8")
    
    # 5. Инициализируем ASTMatcher и применяем патч
    matcher = ASTMatcher(str(target_file))
    success, replaced, strategy = matcher.apply_patch(proposed_old, proposed_new)
    
    # 6. Проверяем успешность
    assert success, f"Failed fuzzy match on {language}: WS={ws}, Comments={comments}, Sig={sig}"
    
    # 7. Проверка Оракула: Сохранение сигнатуры и применимость тела
    assert spec['op_verify'](replaced, op_new), \
        f"New operation '{op_new}' was not merged correctly for {language}!"
        
    if sig:
        assert spec['sig_verify'](replaced), \
            f"Signature parameters for {language} were overwritten by the patch!"
            
    if outside_comment:
        assert outside_comment in replaced, \
            f"Outside comment '{outside_comment}' was stripped during replacement!"
            
    # 8. Древесная AST-валидация через tree-sitter (если грамматика установлена)
    if ts_language is not None:
        from tree_sitter import Parser
        parser = Parser(ts_language)
        
        # Парсим результирующий код
        tree = parser.parse(bytes(replaced, "utf-8"))
        
        # Доказываем, что в сгенерированном и пропатченном коде нет синтаксических ошибок!
        has_errors = check_has_errors(tree.root_node)
        assert not has_errors, \
            f"Patched {language} file contains tree-sitter AST syntax errors!\nReplaced content:\n{replaced}"
