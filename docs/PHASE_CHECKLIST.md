# Чеклист завершения фазы декомпозиции монолита (PHASE_CHECKLIST) 🏁✂️

Этот чеклист предназначен для инженеров и ИИ-агентов для систематической проверки и фиксации результатов в конце каждой фазы рефакторинга (Phase 10, 11, 12+).

---

## 1. Валидация вырезанного кода (Stubs & Manifests)
- [ ] **Отсутствие синтаксических ошибок в монолите**: Убедиться, что стыковочные конструкции (такие как `} else if`) не сломали ветвление родительского файла.
- [ ] **Проверка stubs-комментариев**: Проверить, что на месте вырезанных блоков вставлены правильные комментарии-заглушки со ссылкой на новые файлы:
  ```cpp
  // Stubbed: native_eval_omega_head.cpp
  ```
- [ ] **Резервные копии**: Перед strip apatch создаёт backup в `.apatch/backups/apatch_strip_*` и (если активен) TrustChain checkpoint. После успеха — `TrustChain strip audit committed`. При сбое: `apatch rollback --target-dir <repo>` или повтор strip после `git checkout -- eval_visitor.cpp`.
- [ ] **Манифест `register`**: Для `phase run` / `--to-native auto` в каждом блоке указать `"register": "register_<stem>_builtins"` (не полагаться на auto-имя из `label`).

---

## 2. Проверка генерации Native модулей
- [ ] **Авто-конвертация через `--to-native`**: Если использовался флаг авто-конвертации:
  ```bash
  apatch strip --file path/to/mono.cpp --manifest manifest.json --out-dir src/extracted --to-native register_my_ops
  ```
  Проверить, что файлы в `src/extracted/` содержат корректный синтаксис `register_native("callee", [this](...) { ... })`.
- [ ] **Разрешение импортов (`parent_imports`)**: Проверить в `extraction_report.json` минимальный набор инклудов. Если список пустой, убедиться, что отработал автоматический fallback к полному набору `all_parent_imports`.
- [ ] **Замена `interp_.` и C++20 keywords**: Проверить, что переменные `concept` были автоматически переименованы в `concept_name`, а вложенные типы (`Interpreter::Record`) очищены от внешних областей видимости.

---

## 3. Регистрация и Wiring (Сборка)
- [ ] **Интеграция в CMake**: Добавить новые файлы модулей в `CMakeLists.txt` (например, в список источников целевой библиотеки или исполняемого файла):
  ```cmake
  set(INTERPRETER_SOURCES
      src/frontend/interpreter/eval_visitor.cpp
      extracted/native_eval_omega_head.cpp
      extracted/native_eval_performance.cpp
  )
  ```
- [ ] **Объявление метода регистрации**: Проверить, что в заголовочном файле класса `Interpreter` (`interpreter.h` / `eval_visitor.h`) объявлен соответствующий метод:
  ```cpp
  void register_my_ops();
  ```
- [ ] **Вызов инициализации**: Убедиться, что в конструкторе `Interpreter` вызывается метод регистрации новых нативных функций.

---

## 4. Верификация компиляции и тестов
- [ ] **Чистая сборка**: Запустить полную пересборку проекта:
  ```bash
  cmake --build build --clean-first
  ```
- [ ] **Запуск тестов**: Запустить тестовый набор для проверки функциональности:
  ```bash
  ./build/omega_tests
  ```
- [ ] **Подпись TrustChain**: Проверить состояние цепочки аудита:
  ```bash
  tc status
  ```

---

## 5. Frontend (TypeScript / React) — дополнительный чеклист

- [ ] **`--strict-overlap` на dry-run**: `apatch strip -n --strict-overlap` без ошибок overlap.
- [ ] **Raw fragments**: Файлы `.fragment.txt` в `extracted/` не должны оставаться в `tsconfig` include после wiring.
- [ ] **`--to-module hook`**: Сгенерированный hook в `--module-out-dir` компилируется (`tsc` / `npm run build`).
- [ ] **`integration_hints`**: Проверить `target_module`, `parent_import`, `post_strip_checklist` в `extraction_report.json`.
- [ ] **`dangling_references`**: Пустой список или все символы подключены в родителе через hook.
- [ ] **Verify**: `apatch phase run --profile frontend --verify "npm run build"` прошёл без rollback.
- [ ] **Удаление stubs**: После wiring удалить временные stub-комментарии в монолите.
- [ ] **Тесты**: `npm test` / Vitest smoke после интеграции.

---

*Разработано Ed Cherednik, 2026.*
