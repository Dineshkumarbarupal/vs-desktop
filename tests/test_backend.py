"""Backend regression tests: python -m unittest discover -s tests -v"""
import importlib.util
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path


class EelStub:
    def expose(self, function):
        return function
    def init(self, *args, **kwargs):
        pass
    def start(self, *args, **kwargs):
        pass

sys.modules.setdefault('eel', EelStub())
MAIN = Path(__file__).resolve().parents[1] / 'main.py'
spec = importlib.util.spec_from_file_location('flowcode_test_main', MAIN)
app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = app
assert spec.loader
spec.loader.exec_module(app)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        result = app.set_workspace(str(self.root))
        self.assertNotIn('error', result)

    def tearDown(self):
        self.temp.cleanup()

    def test_editor_session_round_trips_open_tabs_and_unsaved_draft(self):
        from unittest.mock import patch
        import hashlib
        with tempfile.TemporaryDirectory() as state_dir:
            path = self.root / "draft.py"
            path.write_text('value = "disk"\n', encoding="utf-8")
            baseline = app.read_file("draft.py")
            state = {"version": 1, "workspacePath": str(self.root.resolve()),
                     "openPaths": ["draft.py"], "activePath": "draft.py",
                     "drafts": {"draft.py": {"content": 'value = "unsaved"\n',
                                               "saved": baseline["content"],
                                               "savedHash": baseline["sha256"], "openedAt": 123}},
                     "breakpoints": {"draft.py": [3, 1, 3]}, "breakpointConditions": {"draft.py": {"3": "value > 1"}},
                     "debugWatches": ["value", "len(items)"], "updatedAt": 456}
            with patch.object(app, "_user_state_dir", return_value=Path(state_dir)):
                saved = app.save_editor_session(state)
                loaded = app.load_editor_session()
            self.assertTrue(saved.get("ok"), saved)
            self.assertEqual(loaded["session"]["drafts"]["draft.py"]["content"], 'value = "unsaved"\n')
            self.assertEqual(loaded["session"]["drafts"]["draft.py"]["savedHash"], baseline["sha256"])
            self.assertEqual(loaded["session"]["activePath"], "draft.py")
            self.assertEqual(loaded["session"]["breakpoints"]["draft.py"], [1, 3])
            self.assertEqual(loaded["session"]["breakpointConditions"]["draft.py"], {"3": "value > 1"})
            self.assertEqual(loaded["session"]["debugWatches"], ["value", "len(items)"])
            self.assertEqual(path.read_text(encoding="utf-8"), 'value = "disk"\n')
            path.write_text('value = "changed externally"\n', encoding="utf-8")
            draft = loaded["session"]["drafts"]["draft.py"]
            blocked = app.save_file("draft.py", draft["content"], draft["savedHash"])
            self.assertIn("changed on disk", blocked["error"])
            self.assertEqual(path.read_text(encoding="utf-8"), 'value = "changed externally"\n')

    def test_editor_session_rejects_workspace_mismatch_and_traversal(self):
        state = {"version": 1, "workspacePath": str(self.root.resolve()),
                 "openPaths": ["../secret.py"], "activePath": "../secret.py", "drafts": {}}
        self.assertIn("error", app.save_editor_session(state))
        state["openPaths"] = ["draft.py"]
        state["activePath"] = "draft.py"
        state["workspacePath"] = str(self.root / "different")
        self.assertIn("error", app.save_editor_session(state))

    def test_editor_session_rejects_invalid_conditional_breakpoint_expressions(self):
        base = {"version": 1, "workspacePath": str(self.root.resolve()), "openPaths": ["draft.py"],
                "activePath": "draft.py", "drafts": {}, "breakpoints": {"draft.py": [3]},
                "breakpointConditions": {"draft.py": {"3": "value =="}}, "debugWatches": []}
        result = app.save_editor_session(base)
        self.assertIn("Invalid conditional breakpoint expression", result.get("error", ""))
        base["breakpointConditions"] = {"draft.py": {"4": "value > 1"}}
        result = app.save_editor_session(base)
        self.assertIn("enabled breakpoint line", result.get("error", ""))

    def test_editor_session_payload_has_a_five_megabyte_cap(self):
        huge = "x" * (3 * 1024 * 1024)
        state = {"version": 1, "workspacePath": str(self.root.resolve()), "openPaths": ["draft.py"],
                 "activePath": "draft.py", "drafts": {"draft.py": {"content": huge, "saved": huge, "savedHash": None}}}
        self.assertIn("error", app.save_editor_session(state))

    def test_editor_sessions_are_isolated_per_workspace(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as other_workspace:
            state = {"version": 1, "workspacePath": str(self.root.resolve()),
                     "openPaths": ["draft.py"], "activePath": "draft.py", "drafts": {}}
            with patch.object(app, "_user_state_dir", return_value=Path(state_dir)):
                self.assertTrue(app.save_editor_session(state).get("ok"))
                app.set_workspace(other_workspace)
                self.assertIsNone(app.load_editor_session().get("session"))

    def test_discover_workspace_tests_finds_supported_patterns_and_ignores_dependencies(self):
        import json
        (self.root / "tests").mkdir()
        (self.root / "src").mkdir()
        (self.root / "tests" / "test_alpha.py").write_text("def test_alpha(): pass\n", encoding="utf-8")
        (self.root / "src" / "math_test.py").write_text("def test_math(): pass\n", encoding="utf-8")
        (self.root / "tests" / "alpha.test.cjs").write_text("require('node:test')('alpha',()=>{});\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "ignored.test.js").write_text("throw new Error('should not scan');", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "test_ignored.py").write_text("raise RuntimeError('should not scan')", encoding="utf-8")
        (self.root / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}), encoding="utf-8")
        result = app.discover_workspace_tests()
        self.assertEqual(result["python_count"], 2, result)
        self.assertEqual(result["node_count"], 1, result)
        self.assertEqual(result["python_files"], ["src/math_test.py", "tests/test_alpha.py"])
        self.assertEqual(result["node_files"], ["tests/alpha.test.cjs"])
        self.assertTrue(result["npm_test"])
        self.assertEqual(result["npm_test_script"], "node --test")

    def test_run_workspace_python_tests_executes_real_suite(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_sample.py").write_text(
            "import unittest\nclass Sample(unittest.TestCase):\n def test_answer(self): self.assertEqual(40 + 2, 42)\n", encoding="utf-8")
        result = app.run_workspace_tests("python")
        self.assertEqual(result.get("returncode"), 0, result)
        self.assertEqual(result.get("kind"), "python")
        self.assertTrue(result.get("test_files"))
        self.assertFalse(result.get("timed_out"))

    def test_run_workspace_node_tests_executes_real_suite(self):
        if not shutil.which("node"):
            self.skipTest("Node.js is not installed on this test host.")
        (self.root / "test").mkdir()
        (self.root / "test" / "answer.test.cjs").write_text(
            "const test=require('node:test'); const assert=require('node:assert/strict'); test('answer',()=>assert.equal(40+2,42));\n", encoding="utf-8")
        result = app.run_workspace_tests("node")
        self.assertEqual(result.get("returncode"), 0, result)
        self.assertEqual(result.get("kind"), "node")
        self.assertTrue(result.get("test_files"))

    def test_run_workspace_npm_tests_uses_package_script_only(self):
        if not shutil.which("npm") or not shutil.which("node"):
            self.skipTest("Node.js/npm are not installed on this test host.")
        import json
        (self.root / "test").mkdir()
        (self.root / "test" / "answer.test.cjs").write_text(
            "const test=require('node:test'); const assert=require('node:assert/strict'); test('npm answer',()=>assert.equal(6*7,42));\n", encoding="utf-8")
        (self.root / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}), encoding="utf-8")
        result = app.run_workspace_tests("npm")
        self.assertEqual(result.get("returncode"), 0, result)
        self.assertEqual(result.get("runner"), "npm test")

    def test_workspace_test_runner_rejects_unknown_runner_and_missing_suite(self):
        self.assertIn("error", app.run_workspace_tests("powershell"))
        self.assertIn("error", app.run_workspace_tests("python"))
        self.assertIn("error", app.run_workspace_tests("node"))
        self.assertIn("error", app.run_workspace_tests("npm"))

    def test_workspace_settings_round_trip_and_preserve_unknown_settings(self):
        import json
        settings_path = self.root / ".vscode" / "settings.json"
        settings_path.parent.mkdir()
        settings_path.write_text(json.dumps({"editor.rulers": [80, 120], "python.analysis.typeCheckingMode": "basic"}), encoding="utf-8")
        requested = {
            "editor.fontSize": 16,
            "editor.wordWrap": "on",
            "editor.minimap.enabled": False,
            "files.autoSave": "afterDelay",
            "files.autoSaveDelay": 2000,
            "editor.insertSpaces": False,
            "editor.tabSize": 8,
        }
        saved = app.save_workspace_settings(requested)
        self.assertTrue(saved.get("ok"), saved)
        loaded = app.load_workspace_settings()
        self.assertEqual(loaded["settings"], requested)
        self.assertTrue(loaded["file_exists"])
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["editor.rulers"], [80, 120])
        self.assertEqual(raw["python.analysis.typeCheckingMode"], "basic")
        removed = app.save_workspace_settings({key: None for key in requested})
        self.assertTrue(removed.get("ok"), removed)
        self.assertEqual(app.load_workspace_settings()["settings"], {})
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(raw, {"editor.rulers": [80, 120], "python.analysis.typeCheckingMode": "basic"})

    def test_workspace_settings_validate_each_supported_value(self):
        invalid = [
            {"editor.fontSize": True}, {"editor.fontSize": 99},
            {"editor.wordWrap": "sometimes"}, {"editor.wordWrap": []},
            {"editor.minimap.enabled": "yes"}, {"files.autoSave": "onFocusChange"},
            {"files.autoSaveDelay": 10}, {"files.autoSaveDelay": True},
            {"editor.insertSpaces": 1}, {"editor.tabSize": 3},
            {"unexpected.setting": True},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                result = app.save_workspace_settings(payload)
                self.assertIn("error", result, (payload, result))

    def test_workspace_settings_missing_file_returns_empty_overrides(self):
        result = app.load_workspace_settings()
        self.assertEqual(result["settings"], {})
        self.assertFalse(result["file_exists"])
        self.assertEqual(result["settings_path"], ".vscode/settings.json")

    def test_workspace_settings_malformed_json_is_not_overwritten(self):
        path = self.root / ".vscode" / "settings.json"
        path.parent.mkdir()
        path.write_text('{not json', encoding="utf-8")
        loaded = app.load_workspace_settings()
        self.assertIn("error", loaded)
        saved = app.save_workspace_settings({"editor.tabSize": 2})
        self.assertIn("error", saved)
        self.assertEqual(path.read_text(encoding="utf-8"), '{not json')

    def test_workspace_settings_reject_symbolic_linked_vscode_folder(self):
        import os
        external = self.root.parent / (self.root.name + "-external-vscode")
        external.mkdir(exist_ok=True)
        try:
            (self.root / ".vscode").symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are unavailable on this platform/user.")
        self.assertIn("error", app.load_workspace_settings())
        self.assertIn("error", app.save_workspace_settings({"editor.tabSize": 4}))
        self.assertFalse((external / "settings.json").exists())
        shutil.rmtree(external, ignore_errors=True)

    def test_workspace_settings_ignore_only_invalid_supported_values_on_load(self):
        import json
        path = self.root / ".vscode" / "settings.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"editor.fontSize": "huge", "editor.tabSize": 4,
                                    "files.autoSave": "onFocusChange", "editor.rulers": [80]}), encoding="utf-8")
        result = app.load_workspace_settings()
        self.assertEqual(result["settings"], {"editor.tabSize": 4})
        self.assertEqual(len(result["warnings"]), 2)

    def test_workspace_settings_size_limit_blocks_read_and_write_without_clobbering(self):
        path = self.root / ".vscode" / "settings.json"
        path.parent.mkdir()
        original = " " * (256 * 1024 + 1)
        path.write_text(original, encoding="utf-8")
        self.assertIn("error", app.load_workspace_settings())
        self.assertIn("error", app.save_workspace_settings({"editor.tabSize": 2}))
        self.assertEqual(path.stat().st_size, len(original.encode("utf-8")))

    def test_workspace_settings_workspace_scoped(self):
        first = self.root
        self.assertTrue(app.save_workspace_settings({"editor.tabSize": 2}).get("ok"))
        second = self.root / "other"
        second.mkdir()
        app.set_workspace(str(second))
        self.assertEqual(app.load_workspace_settings()["settings"], {})
        app.set_workspace(str(first))
        self.assertEqual(app.load_workspace_settings()["settings"]["editor.tabSize"], 2)

    def test_user_settings_round_trip_and_last_workspace(self):
        from unittest.mock import patch
        settings = {"theme": "light", "fontSize": 14, "wordWrap": True,
                    "autoSave": False, "autoSaveDelay": 1000, "minimap": False,
                    "lastWorkspacePath": str(self.root.resolve())}
        with tempfile.TemporaryDirectory() as state_dir, patch.object(app, "_user_state_dir", return_value=Path(state_dir)):
            saved = app.save_user_settings(settings)
            loaded = app.load_user_settings()
        self.assertTrue(saved.get("ok"), saved)
        self.assertEqual(loaded["settings"]["theme"], "light")
        self.assertEqual(loaded["settings"]["fontSize"], 14)
        self.assertEqual(loaded["settings"]["lastWorkspacePath"], str(self.root.resolve()))

    def test_user_settings_reject_invalid_values(self):
        self.assertIn("error", app.save_user_settings({"theme": "neon"}))
        self.assertIn("error", app.save_user_settings({"fontSize": 100}))
        self.assertIn("error", app.save_user_settings({"wordWrap": "yes"}))
        self.assertIn("error", app.save_user_settings({"autoSaveDelay": 20}))

    def test_user_settings_load_ignores_malformed_theme_value(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as state_dir, patch.object(app, "_user_state_dir", return_value=Path(state_dir)):
            settings_path = Path(state_dir) / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text('{"theme": ["invalid"], "wordWrap": true}', encoding="utf-8")
            loaded = app.load_user_settings()
        self.assertEqual(loaded["settings"], {"wordWrap": True})

    def test_user_settings_reject_missing_workspace(self):
        self.assertIn("error", app.save_user_settings({"lastWorkspacePath": str(self.root / "missing")}))

    def test_file_lifecycle(self):
        self.assertTrue(app.create_entry('', 'hello.py', False)['ok'])
        self.assertTrue(app.save_file('hello.py', 'print("hello")\n')['ok'])
        self.assertEqual(app.read_file('hello.py')['content'], 'print("hello")\n')
        self.assertTrue(app.rename_entry('hello.py', 'renamed.py')['ok'])
        self.assertTrue(app.delete_entry('renamed.py')['ok'])
        self.assertFalse((self.root / 'renamed.py').exists())

    def test_read_file_hash_matches_original_bytes(self):
        content = 'print("hash me")\n'
        path = self.root / 'hash.py'
        path.write_text(content, encoding='utf-8')
        result = app.read_file('hash.py')
        import hashlib
        self.assertEqual(result['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_save_rejects_external_file_changes(self):
        path = self.root / 'external.py'
        path.write_text('value = 1\n', encoding='utf-8')
        opened = app.read_file('external.py')
        path.write_text('value = 2  # external edit\n', encoding='utf-8')
        result = app.save_file('external.py', 'value = 3\n', opened['sha256'])
        self.assertIn('error', result)
        self.assertIn('changed on disk', result['error'])
        self.assertEqual(path.read_text(encoding='utf-8'), 'value = 2  # external edit\n')

    def test_explicit_force_save_overwrites_stale_file_after_confirmation_flow(self):
        path = self.root / 'force.py'
        path.write_text('disk = "old"\n', encoding='utf-8')
        opened = app.read_file('force.py')
        path.write_text('disk = "external"\n', encoding='utf-8')
        blocked = app.save_file('force.py', 'disk = "editor"\n', opened['sha256'])
        self.assertIn('error', blocked)
        forced = app.save_file('force.py', 'disk = "editor"\n', None, True)
        self.assertTrue(forced.get('ok'), forced)
        self.assertEqual(path.read_text(encoding='utf-8'), 'disk = "editor"\n')
        self.assertEqual(forced['sha256'], app.read_file('force.py')['sha256'])

    def test_atomic_save_preserves_original_if_replace_fails(self):
        from unittest.mock import patch
        path = self.root / 'atomic.py'
        path.write_text('original = True\n', encoding='utf-8')
        opened = app.read_file('atomic.py')
        with patch('os.replace', side_effect=OSError('simulated atomic replace error')):
            result = app.save_file('atomic.py', 'replacement = True\n', opened['sha256'])
        self.assertIn('error', result)
        self.assertEqual(path.read_text(encoding='utf-8'), 'original = True\n')
        self.assertFalse(list(self.root.glob('.flowcode-save-*')))

    def test_nested_file_search(self):
        self.assertTrue(app.create_entry('', 'src', True)['ok'])
        self.assertTrue(app.create_entry('src', 'app.js', False)['ok'])
        self.assertTrue(app.save_file('src/app.js', 'const needle = "found";\n')['ok'])
        matches = app.search_workspace('needle', True)['results']
        self.assertTrue(any(x['path'] == 'src/app.js' and x['kind'] == 'match' for x in matches))

    def test_path_traversal_rejected(self):
        self.assertIn('error', app.read_file('../outside.py'))
        self.assertIn('error', app.save_file('../outside.py', 'bad'))
        self.assertIn('error', app.create_entry('..', 'outside.py', False))

    def test_invalid_names_rejected(self):
        self.assertIn('error', app.create_entry('', '../bad.py', False))
        self.assertIn('error', app.create_entry('', '', False))

    def test_symlinks_are_not_followed(self):
        outside = self.root.parent / (self.root.name + '-outside.py')
        outside.write_text('outside', encoding='utf-8')
        link = self.root / 'linked.py'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            outside.unlink(missing_ok=True)
            self.skipTest('Environment does not allow creating symlinks.')
        try:
            self.assertIn('error', app.read_file('linked.py'))
        finally:
            link.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)

    def test_python_formatting_api_is_read_only_and_black_aware(self):
        source = 'def greet( name ):\n  return  \"hello\",name\n'
        (self.root / 'format.py').write_text(source, encoding='utf-8')
        result = app.format_python_document('format.py', source)
        try:
            import black  # noqa: F401
        except ImportError:
            self.assertIn('error', result)
        else:
            self.assertNotIn('error', result, result)
            self.assertIn('def greet(name):', result['formatted'])
            self.assertTrue(result['changed'])
        self.assertEqual((self.root / 'format.py').read_text(encoding='utf-8'), source,
                         'Formatting previews must never write files directly.')

    def test_python_formatter_uses_black_api_when_available(self):
        import types
        from unittest.mock import patch
        fake = types.ModuleType('black')
        class NothingChanged(Exception): pass
        class InvalidInput(Exception): pass
        class FileMode:
            def __init__(self, line_length=88): self.line_length = line_length
        fake.NothingChanged = NothingChanged
        fake.InvalidInput = InvalidInput
        fake.FileMode = FileMode
        fake.format_file_contents = lambda source, fast, mode: 'def greet(name):\n    return "hello", name\n'
        source = 'def greet( name ):\n  return  "hello",name\n'
        (self.root / 'fake_black.py').write_text(source, encoding='utf-8')
        with patch.dict(sys.modules, {'black': fake}):
            result = app.format_python_document('fake_black.py', source)
        self.assertEqual(result['engine'], 'Black')
        self.assertTrue(result['changed'])
        self.assertIn('def greet(name):', result['formatted'])
        self.assertEqual((self.root / 'fake_black.py').read_text(encoding='utf-8'), source)

    def test_python_formatter_rejects_non_python(self):
        (self.root / 'page.html').write_text('<h1>Title</h1>', encoding='utf-8')
        result = app.format_python_document('page.html', '<h1>Title</h1>')
        self.assertIn('error', result)

    def test_workspace_diagnostics_report_relative_paths_and_skip_dependencies(self):
        (self.root / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
        (self.root / "bad.json").write_text('{"name": }', encoding="utf-8")
        dependency = self.root / "node_modules" / "vendor"
        dependency.mkdir(parents=True)
        (dependency / "broken.py").write_text("def vendor_bad(:\n", encoding="utf-8")
        result = app.validate_workspace()
        self.assertNotIn("error", result, result)
        self.assertGreaterEqual(result["checked_files"], 2, result)
        self.assertTrue(any(d["path"] == "broken.py" and d["severity"] == "error" for d in result["diagnostics"]), result)
        self.assertTrue(any(d["path"] == "bad.json" and d["source"] == "JSON" for d in result["diagnostics"]), result)
        self.assertFalse(any("node_modules" in d["path"] for d in result["diagnostics"]), result)

    def test_workspace_diagnostics_validate_unsaved_overrides_without_writing(self):
        path = self.root / "draft.py"
        disk_source = "def valid():\n    return 1\n"
        path.write_text(disk_source, encoding="utf-8")
        result = app.validate_workspace({"draft.py": "def broken(:\n    return 1\n"})
        self.assertNotIn("error", result, result)
        self.assertTrue(any(d["path"] == "draft.py" and d["severity"] == "error" for d in result["diagnostics"]), result)
        self.assertEqual(path.read_text(encoding="utf-8"), disk_source)

    def test_workspace_diagnostics_respect_file_count_limit(self):
        for index in range(app.MAX_WORKSPACE_DIAGNOSTIC_FILES + 7):
            (self.root / f"module_{index:03}.py").write_text("value = 1\n", encoding="utf-8")
        result = app.validate_workspace()
        self.assertNotIn("error", result, result)
        self.assertLessEqual(result["candidate_files"], app.MAX_WORKSPACE_DIAGNOSTIC_FILES)
        self.assertLessEqual(result["checked_files"], app.MAX_WORKSPACE_DIAGNOSTIC_FILES)
        self.assertTrue(result["partial"], result)

    def test_workspace_diagnostics_reject_path_traversal_override(self):
        result = app.validate_workspace({"../outside.py": "def broken(:\n"})
        self.assertIn("error", result)

    def test_python_syntax_diagnostics(self):
        result = app.validate_code('bad.py', 'def broken(:\n    pass\n')
        self.assertTrue(result['checked'])
        self.assertEqual(result['diagnostics'][0]['severity'], 'error')
        self.assertGreaterEqual(result['diagnostics'][0]['line'], 1)

    def test_json_syntax_diagnostics(self):
        result = app.validate_code('bad.json', '{"name": }')
        self.assertTrue(result['checked'])
        self.assertEqual(result['diagnostics'][0]['source'], 'JSON')
        self.assertGreaterEqual(result['diagnostics'][0]['column'], 1)

    def test_css_structure_diagnostics_ignore_comments_and_strings(self):
        broken = app.validate_code('styles.css', '/* } comment */\n.a { content: \"not } a block\";\n')
        self.assertTrue(broken['checked'])
        self.assertTrue(any(d['severity'] == 'warning' and 'not closed' in d['message'] for d in broken['diagnostics']), broken)
        valid = app.validate_code('valid.css', '.a { content: "}"; } /* { ignored */')
        self.assertEqual(valid['diagnostics'], [], valid)

    def test_html_tag_balance_diagnostics(self):
        invalid = app.validate_code('page.html', '<html><body><div>hello</body></html>')
        self.assertTrue(invalid['checked'])
        self.assertTrue(any('not closed' in d['message'] for d in invalid['diagnostics']), invalid)
        valid = app.validate_code('page.html', '<!doctype html><html><body><img src="x"><p>Hello</p></body></html>')
        self.assertEqual(valid['diagnostics'], [], valid)

    def test_xml_well_formedness_diagnostics(self):
        invalid = app.validate_code('page.xml', '<root><node></root>')
        self.assertTrue(invalid['checked'])
        self.assertEqual(invalid['diagnostics'][0]['source'], 'XML')
        self.assertEqual(invalid['diagnostics'][0]['severity'], 'error')
        valid = app.validate_code('page.xml', '<root><node /></root>')
        self.assertEqual(valid['diagnostics'], [])

    def test_javascript_syntax_when_node_available(self):
        if not shutil.which('node'):
            self.skipTest('Node.js is not available.')
        result = app.validate_code('bad.js', 'function ( {')
        self.assertTrue(result['checked'])
        self.assertTrue(result['diagnostics'])

    def test_python_runner(self):
        (self.root / 'run.py').write_text('print("runner-ok")\n', encoding='utf-8')
        result = app.run_file('run.py')
        self.assertEqual(result.get('returncode'), 0, result)
        self.assertIn('runner-ok', result.get('stdout', ''))

    def test_javascript_runner_when_node_available(self):
        if not shutil.which('node'):
            self.skipTest('Node.js is not available.')
        (self.root / 'run.js').write_text('console.log("node-ok")\n', encoding='utf-8')
        result = app.run_file('run.js')
        self.assertEqual(result.get('returncode'), 0, result)
        self.assertIn('node-ok', result.get('stdout', ''))

    def test_python_jedi_completions_include_local_symbols(self):
        (self.root / 'analysis.py').write_text('def greet(name):\n    return name\n\ngre\n', encoding='utf-8')
        source = (self.root / 'analysis.py').read_text(encoding='utf-8')
        result = app.python_completions('analysis.py', source, 4, 3)
        self.assertNotIn('error', result, result)
        self.assertTrue(any(item['name'] == 'greet' for item in result['items']), result)
        self.assertEqual(result['engine'], 'Jedi')

    def test_python_definition_hover_and_references(self):
        source = 'def greet(name):\n    """Greeting documentation."""\n    return name\n\nmessage = greet("FlowCode")\n'
        (self.root / 'analysis.py').write_text(source, encoding='utf-8')
        definition = app.python_goto_definition('analysis.py', source, 5, 12)
        self.assertNotIn('error', definition, definition)
        self.assertEqual(definition['definitions'][0]['path'], 'analysis.py')
        self.assertEqual(definition['definitions'][0]['line'], 1)
        hover = app.python_hover('analysis.py', source, 5, 12)
        self.assertNotIn('error', hover, hover)
        self.assertTrue(any('greet' in item.get('signature', '') for item in hover['items']), hover)
        self.assertTrue(any('Greeting documentation' in item.get('docstring', '') for item in hover['items']), hover)
        refs = app.python_references('analysis.py', source, 5, 12)
        self.assertNotIn('error', refs, refs)
        self.assertTrue(any(ref['path'] == 'analysis.py' and ref['line'] == 5 for ref in refs['references']), refs)

    def test_typescript_quick_fix_previews_missing_import_and_applies_atomically(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        util_source = 'export function greet(name: string): string { return name; }\n'
        source = 'const message = greet("FlowCode");\nconsole.log(message);\n'
        (self.root / 'util.ts').write_text(util_source, encoding='utf-8')
        (self.root / 'app.ts').write_text(source, encoding='utf-8')
        preview = app.typescript_code_fixes('app.ts', source, 1, 18)
        self.assertNotIn('error', preview, preview)
        self.assertTrue(preview['actions'], preview)
        candidate = next((action for action in preview['actions'] if action['changes']), preview['actions'][0])
        self.assertTrue(any(change['path'] == 'app.ts' for change in candidate['changes']), candidate)
        self.assertEqual((self.root / 'app.ts').read_text(encoding='utf-8'), source,
                         'Quick Fix preview must not write source files.')
        result = app.apply_typescript_code_fix(candidate['changes'])
        self.assertTrue(result.get('ok'), result)
        self.assertIn('import { greet } from "./util";', (self.root / 'app.ts').read_text(encoding='utf-8'))

    def test_typescript_quick_fix_requires_saved_active_buffer_and_rejects_stale_preview(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        source = 'const answer: number = "wrong";\n'
        (self.root / 'app.ts').write_text(source, encoding='utf-8')
        unsaved = app.typescript_code_fixes('app.ts', source + '// unsaved\n', 1, 7)
        self.assertIn('error', unsaved, unsaved)
        self.assertIn('Save the active file', unsaved['error'])
        (self.root / 'util.ts').write_text('export const value = 1;\n', encoding='utf-8')
        (self.root / 'app.ts').write_text('const value = values;\n', encoding='utf-8')
        preview = app.typescript_code_fixes('app.ts', 'const value = values;\n', 1, 18)
        self.assertTrue(preview.get('actions'), preview)
        change = preview['actions'][0]['changes'][0]
        target = self.root / change['path']
        original = target.read_text(encoding='utf-8')
        target.write_text('// changed after preview\n' + original, encoding='utf-8')
        blocked = app.apply_typescript_code_fix(preview['actions'][0]['changes'])
        self.assertIn('error', blocked, blocked)
        self.assertIn('changed on disk after the preview', blocked['error'])

    def test_typescript_language_service_completion_navigation_and_hover(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        (self.root / 'util.ts').write_text('export function greet(name: string): string { return "Hello " + name; }\n', encoding='utf-8')
        completion_source = 'import { greet } from "./util";\nconst result = gre\n'
        (self.root / 'app.ts').write_text(completion_source, encoding='utf-8')
        completions = app.typescript_intelligence('app.ts', completion_source, 2, len('const result = gre'), 'completions')
        self.assertNotIn('error', completions, completions)
        self.assertTrue(any(item['name'] == 'greet' for item in completions['items']), completions)
        source = 'import { greet } from "./util";\nconst result = greet("FlowCode");\nconsole.log(result);\n'
        definition = app.typescript_intelligence('app.ts', source, 2, 17, 'definition')
        self.assertNotIn('error', definition, definition)
        self.assertTrue(any(item['path'] == 'util.ts' and item['line'] == 1 and item['name'] == 'greet' for item in definition['definitions']), definition)
        references = app.typescript_intelligence('app.ts', source, 2, 17, 'references')
        self.assertNotIn('error', references, references)
        self.assertGreaterEqual(references['count'], 3, references)
        self.assertTrue(any(item['path'] == 'app.ts' and item['line'] == 2 for item in references['references']), references)
        hover = app.typescript_intelligence('app.ts', source, 2, 17, 'hover')
        self.assertNotIn('error', hover, hover)
        self.assertTrue(any('greet' in item.get('signature', '') and 'string' in item.get('signature', '') for item in hover['items']), hover)

    def test_typescript_symbol_rename_previews_and_applies_across_files(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        util_source = 'export function greet(name: string): string { return name; }\n'
        app_source = 'import { greet } from "./util";\nconst value = greet("x");\n'
        (self.root / 'util.ts').write_text(util_source, encoding='utf-8')
        (self.root / 'app.ts').write_text(app_source, encoding='utf-8')
        preview = app.javascript_rename_symbol('util.ts', util_source, 1, 20, 'welcome')
        self.assertNotIn('error', preview, preview)
        self.assertEqual(preview['count'], 2)
        self.assertEqual((self.root / 'util.ts').read_text(encoding='utf-8'), util_source, 'Preview must remain read-only.')
        changes = {item['path']: item['new_content'] for item in preview['changes']}
        self.assertIn('function welcome', changes['util.ts'])
        self.assertIn('import { welcome }', changes['app.ts'])
        self.assertIn('welcome("x")', changes['app.ts'])
        applied = app.apply_javascript_rename(preview['changes'])
        self.assertTrue(applied.get('ok'), applied)
        self.assertIn('function welcome', (self.root / 'util.ts').read_text(encoding='utf-8'))
        self.assertIn('welcome("x")', (self.root / 'app.ts').read_text(encoding='utf-8'))

    def test_typescript_rename_rejects_reserved_identifiers_and_stale_files(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        util_source = 'export function greet(name: string): string { return name; }\n'
        app_source = 'import { greet } from "./util";\nconst value = greet("x");\n'
        (self.root / 'util.ts').write_text(util_source, encoding='utf-8')
        (self.root / 'app.ts').write_text(app_source, encoding='utf-8')
        self.assertIn('error', app.javascript_rename_symbol('util.ts', util_source, 1, 20, 'class'))
        preview = app.javascript_rename_symbol('util.ts', util_source, 1, 20, 'welcome')
        self.assertNotIn('error', preview, preview)
        (self.root / 'app.ts').write_text('// external change\n' + app_source, encoding='utf-8')
        blocked = app.apply_javascript_rename(preview['changes'])
        self.assertIn('error', blocked)
        self.assertIn('external change', (self.root / 'app.ts').read_text(encoding='utf-8'))
        self.assertEqual((self.root / 'util.ts').read_text(encoding='utf-8'), util_source)

    def test_typescript_document_formatting_is_read_only_and_formats_source(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        source = 'export function greet(name:string){return name+"!";}\n'
        (self.root / 'format.ts').write_text(source, encoding='utf-8')
        result = app.format_javascript_document('format.ts', source)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['changed'], result)
        self.assertIn('function greet(name: string)', result['formatted'])
        self.assertIn('return name + "!";', result['formatted'])
        self.assertEqual((self.root / 'format.ts').read_text(encoding='utf-8'), source, 'Formatter must be read-only.')

    def test_typescript_language_service_honors_tsconfig_paths(self):
        import json
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        (self.root / 'tsconfig.json').write_text(json.dumps({
            'compilerOptions': {'baseUrl': '.', 'paths': {'@/*': ['src/*']},
                                'moduleResolution': 'node', 'module': 'ESNext', 'target': 'ES2020'},
            'include': ['src/**/*.ts']
        }), encoding='utf-8')
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'util.ts').write_text('export function greet(name: string): string { return name; }\n', encoding='utf-8')
        source = 'import { greet } from "@/util";\nconst result = greet("x");\n'
        (self.root / 'src' / 'app.ts').write_text(source, encoding='utf-8')
        result = app.typescript_intelligence('src/app.ts', source, 2, 18, 'definition')
        self.assertNotIn('error', result, result)
        self.assertTrue(any(x['path'] == 'src/util.ts' and x['name'] == 'greet' for x in result['definitions']), result)

    def test_typescript_diagnostics_report_invalid_tsconfig(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        (self.root / 'tsconfig.json').write_text('{ "compilerOptions": { "target": "not-a-target" }', encoding='utf-8')
        (self.root / 'app.ts').write_text('const result = 1;\n', encoding='utf-8')
        result = app.validate_code('app.ts', 'const result = 1;\n')
        self.assertTrue(result['checked'])
        self.assertTrue(any(x.get('source') == 'tsconfig.json' and x.get('severity') == 'error' for x in result['diagnostics']), result)

    def test_typescript_provider_gives_clear_optional_dependency_error(self):
        from unittest.mock import patch
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is not installed.')
        (self.root / 'app.ts').write_text('const result = 1;\n', encoding='utf-8')
        with patch.object(app, '_resolve_typescript_module', return_value=None):
            result = app.typescript_intelligence('app.ts', 'const result = 1;\n', 1, 6, 'completions')
        self.assertIn('error', result)
        self.assertIn('npm install', result['error'])

    def test_typescript_syntax_diagnostics_use_compiler_when_available(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        (self.root / 'broken.ts').write_text('const answer: = 42;\n', encoding='utf-8')
        result = app.validate_code('broken.ts', 'const answer: = 42;\n')
        self.assertTrue(result['checked'])
        self.assertTrue(any(item['source'] == 'TypeScript' and item['severity'] == 'error' for item in result['diagnostics']), result)

    def test_typescript_semantic_diagnostics_report_type_mismatches(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        source = 'const answer: number = "not a number";\n'
        (self.root / 'app.ts').write_text(source, encoding='utf-8')
        result = app.validate_code('app.ts', source)
        self.assertTrue(result['checked'], result)
        semantic = [item for item in result['diagnostics'] if item.get('source') == 'TypeScript']
        self.assertTrue(any('not assignable' in item['message'] for item in semantic), result)
        self.assertEqual(semantic[0]['line'], 1)
        self.assertGreaterEqual(semantic[0]['column'], 1)

    def test_javascript_ts_check_reports_jsdoc_type_errors(self):
        node = shutil.which('node')
        if not node or not app._resolve_typescript_module(str(self.root), node):
            self.skipTest('Node.js and the TypeScript compiler are required for this optional test.')
        source = '// @ts-check\n/** @type {number} */\nconst answer = "not a number";\n'
        (self.root / 'app.js').write_text(source, encoding='utf-8')
        result = app.validate_code('app.js', source)
        self.assertTrue(result['checked'], result)
        self.assertTrue(any(item.get('source') == 'TypeScript' and 'not assignable' in item.get('message', '')
                            for item in result['diagnostics']), result)

    def test_python_symbol_rename_previews_multiple_workspace_files(self):
        (self.root / 'lib.py').write_text('def greet(name):\n    return name\n', encoding='utf-8')
        (self.root / 'main.py').write_text('from lib import greet\nprint(greet("x"))\n', encoding='utf-8')
        source = (self.root / 'lib.py').read_text(encoding='utf-8')
        result = app.python_rename_symbol('lib.py', source, 1, 5, 'welcome')
        self.assertNotIn('error', result, result)
        self.assertGreaterEqual(result['count'], 2)
        self.assertEqual((self.root / 'lib.py').read_text(encoding='utf-8'), source, 'Preview must not write files.')
        main_change = next(change for change in result['changes'] if change['path'] == 'main.py')
        self.assertIn('from lib import welcome', main_change['new_content'])
        self.assertIn('welcome("x")', main_change['new_content'])

    def test_python_symbol_rename_apply_and_concurrency_guard(self):
        (self.root / 'lib.py').write_text('def greet(name):\n    return name\n', encoding='utf-8')
        (self.root / 'main.py').write_text('from lib import greet\nprint(greet("x"))\n', encoding='utf-8')
        source = (self.root / 'lib.py').read_text(encoding='utf-8')
        preview = app.python_rename_symbol('lib.py', source, 1, 5, 'welcome')
        self.assertNotIn('error', preview, preview)
        main_path = self.root / 'main.py'
        main_path.write_text('# external change\n' + main_path.read_text(encoding='utf-8'), encoding='utf-8')
        blocked = app.apply_python_rename(preview['changes'])
        self.assertIn('error', blocked)
        self.assertIn('external change', main_path.read_text(encoding='utf-8'))
        fresh_source = (self.root / 'lib.py').read_text(encoding='utf-8')
        fresh = app.python_rename_symbol('lib.py', fresh_source, 1, 5, 'welcome')
        self.assertNotIn('error', fresh, fresh)
        applied = app.apply_python_rename(fresh['changes'])
        self.assertTrue(applied.get('ok'), applied)
        self.assertIn('def welcome', (self.root / 'lib.py').read_text(encoding='utf-8'))
        self.assertIn('welcome("x")', (self.root / 'main.py').read_text(encoding='utf-8'))

    def test_python_rename_rolls_back_if_a_file_replace_fails(self):
        from unittest.mock import patch
        import os
        (self.root / 'lib.py').write_text('def greet(name):\n    return name\n', encoding='utf-8')
        (self.root / 'main.py').write_text('from lib import greet\nprint(greet("x"))\n', encoding='utf-8')
        originals = {name: (self.root / name).read_text(encoding='utf-8') for name in ('lib.py', 'main.py')}
        preview = app.python_rename_symbol('lib.py', originals['lib.py'], 1, 5, 'welcome')
        self.assertNotIn('error', preview, preview)
        real_replace = os.replace
        calls = {'n': 0}
        def fail_second(src, dst):
            calls['n'] += 1
            if calls['n'] == 2:
                raise OSError('simulated disk replace failure')
            return real_replace(src, dst)
        with patch('os.replace', side_effect=fail_second):
            result = app.apply_python_rename(preview['changes'])
        self.assertIn('error', result)
        for name, source in originals.items():
            self.assertEqual((self.root / name).read_text(encoding='utf-8'), source)

    def test_python_symbol_rename_rejects_invalid_identifier(self):
        (self.root / 'code.py').write_text('value = 1\n', encoding='utf-8')
        result = app.python_rename_symbol('code.py', 'value = 1\n', 1, 1, 'class')
        self.assertIn('error', result)

    def test_python_intelligence_rejects_other_languages_and_large_buffers(self):
        (self.root / 'module.js').write_text('const name = 1;', encoding='utf-8')
        self.assertIn('error', app.python_completions('module.js', 'const name=1', 1, 5))
        (self.root / 'large.py').write_text('', encoding='utf-8')
        result = app.python_completions('large.py', 'x' * (app.PYTHON_INTELLIGENCE_MAX_BYTES + 1), 1, 1)
        self.assertIn('error', result)

    def test_binary_file_refused(self):
        (self.root / 'image.bin').write_bytes(b'\xff\x00\x81')
        self.assertIn('error', app.read_file('image.bin'))

    def test_html_preview_inlines_local_css_and_js(self):
        (self.root / 'site.html').write_text('<html><head><link rel="stylesheet" href="styles.css"></head><body><h1>Preview</h1><script src="app.js"></script></body></html>', encoding='utf-8')
        (self.root / 'styles.css').write_text('h1 { color: red; }', encoding='utf-8')
        (self.root / 'app.js').write_text('console.log("preview");', encoding='utf-8')
        result = app.preview_html('site.html')
        self.assertNotIn('error', result, result)
        self.assertIn('<style>', result['html'])
        self.assertIn('h1 { color: red; }', result['html'])
        self.assertIn('console.log("preview");', result['html'])
        self.assertEqual(len(result['assets']), 2)

    def test_html_preview_rejects_non_html(self):
        (self.root / 'app.py').write_text('print(1)', encoding='utf-8')
        self.assertIn('error', app.preview_html('app.py'))

    def test_markdown_preview_escapes_raw_html(self):
        (self.root / 'guide.md').write_text('# Getting Started\n\nUse **bold** and `code`.\n\n<script>alert(1)</script>', encoding='utf-8')
        result = app.preview_html('guide.md')
        self.assertNotIn('error', result, result)
        self.assertEqual(result['kind'], 'markdown')
        self.assertIn('<h1>Getting Started</h1>', result['html'])
        self.assertIn('<strong>bold</strong>', result['html'])
        self.assertIn('<code>code</code>', result['html'])
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', result['html'])
        self.assertNotIn('<script>alert(1)</script>', result['html'])

    def test_launch_configuration_discovery_filters_current_file_type(self):
        import json
        (self.root / 'main.py').write_text('print("python")\n', encoding='utf-8')
        (self.root / 'app.js').write_text('console.log("node")\n', encoding='utf-8')
        config_dir = self.root / '.vscode'
        config_dir.mkdir()
        (config_dir / 'launch.json').write_text(json.dumps({"version": "0.2.0", "configurations": [
            {"name": "Python current", "type": "python", "request": "launch", "program": "${file}", "cwd": "${workspaceFolder}"},
            {"name": "Node current", "type": "node", "request": "launch", "program": "${file}", "cwd": "${workspaceFolder}"}
        ]}), encoding='utf-8')
        python_configs = app.list_debug_configurations('main.py')
        self.assertEqual([row['name'] for row in python_configs['configurations']], ['Python current'])
        self.assertEqual(python_configs['configurations'][0]['program'], 'main.py')
        self.assertEqual(python_configs['configurations'][0]['cwd'], '.')
        self.assertEqual(python_configs['configurations'][0]['env'], {})
        node_configs = app.list_debug_configurations('app.js')
        self.assertEqual([row['name'] for row in node_configs['configurations']], ['Node current'])
        self.assertEqual(node_configs['configurations'][0]['program'], 'app.js')

    def test_launch_configuration_rejects_unsafe_paths_and_attach_mode(self):
        import json
        (self.root / 'main.py').write_text('print("python")\n', encoding='utf-8')
        config_dir = self.root / '.vscode'
        config_dir.mkdir()
        (config_dir / 'launch.json').write_text(json.dumps({"configurations": [
            {"name": "Outside", "type": "python", "program": "../outside.py"},
            {"name": "Attach", "type": "python", "request": "attach", "program": "${file}"}
        ]}), encoding='utf-8')
        result = app.list_debug_configurations('main.py')
        self.assertEqual(result['configurations'], [])
        self.assertEqual(len(result['errors']), 2)
        self.assertIn('inside the opened workspace', result['errors'][0])
        self.assertIn('attach configurations are not implemented', result['errors'][1])

    def test_python_launch_configuration_passes_args_cwd_and_environment(self):
        import json
        subdir = self.root / 'subdir'
        subdir.mkdir()
        source = 'import os, sys\nprint("ARGS", sys.argv[1:])\nprint("ENV", os.getenv("FLOWCODE_LAUNCH_TEST"))\nprint("CWD", os.getcwd())\n'
        (subdir / 'args.py').write_text(source, encoding='utf-8')
        config = {"name": "Args test", "type": "python", "request": "launch", "program": "${workspaceFolder}/subdir/args.py", "cwd": "${workspaceFolder}/subdir", "args": ["hello", "two words"], "env": {"FLOWCODE_LAUNCH_TEST": "configured"}}
        start = app.debug_start('main.py', [], config) if (self.root / 'main.py').exists() else app.debug_start('subdir/args.py', [], config)
        self.assertTrue(start.get('ok'), start)
        try:
            events = []
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and not any(e.get('type') == 'stopped' for e in events):
                events.extend(app.debug_poll().get('events', [])); time.sleep(0.03)
            self.assertTrue(any(e.get('type') == 'stopped' for e in events), events)
            self.assertTrue(app.debug_command('continue').get('ok'))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(e.get('type') == 'terminated' for e in events):
                events.extend(app.debug_poll().get('events', [])); time.sleep(0.03)
            output = ''.join(e.get('text', '') for e in events if e.get('type') == 'console')
            self.assertIn("ARGS ['hello', 'two words']", output)
            self.assertIn('ENV configured', output)
            self.assertIn(str(subdir), output)
            self.assertTrue(any(e.get('type') == 'terminated' and e.get('returncode') == 0 for e in events), events)
        finally:
            app.debug_stop()

    @unittest.skipUnless(shutil.which('node') and importlib.util.find_spec('websocket'), 'Node.js and websocket-client are required.')
    def test_node_launch_configuration_passes_args_cwd_and_environment(self):
        import json
        subdir = self.root / 'subdir'
        subdir.mkdir()
        source = 'console.log("ARGS", JSON.stringify(process.argv.slice(2)));\nconsole.log("ENV", process.env.FLOWCODE_LAUNCH_TEST);\nconsole.log("CWD", process.cwd());\n'
        (subdir / 'args.js').write_text(source, encoding='utf-8')
        config = {"name": "Args test", "type": "node", "request": "launch", "program": "${workspaceFolder}/subdir/args.js", "cwd": "${workspaceFolder}/subdir", "args": ["hello", "two words"], "env": {"FLOWCODE_LAUNCH_TEST": "configured"}, "runtimeArgs": ["--no-warnings"]}
        start = app.node_debug_start('subdir/args.js', [], config)
        self.assertTrue(start.get('ok'), start)
        try:
            events = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(e.get('type') == 'stopped' for e in events):
                events.extend(app.node_debug_poll().get('events', [])); time.sleep(0.03)
            self.assertTrue(any(e.get('type') == 'stopped' for e in events), events)
            self.assertTrue(app.node_debug_command('continue').get('ok'))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not any(e.get('type') == 'terminated' for e in events):
                events.extend(app.node_debug_poll().get('events', [])); time.sleep(0.04)
            output = ''.join(e.get('text', '') for e in events if e.get('type') == 'console')
            self.assertIn('ARGS ["hello","two words"]', output)
            self.assertIn('ENV configured', output)
            self.assertIn(str(subdir), output)
            self.assertTrue(any(e.get('type') == 'terminated' and e.get('returncode') == 0 for e in events), events)
        finally:
            app.node_debug_stop()

    @unittest.skipIf(sys.platform.startswith('win'), 'POSIX cwd persistence test.')
    def test_debugger_stops_at_breakpoint_and_exposes_locals(self):
        source = 'total = 0\nfor value in range(3):\n    total += value\nprint("result", total)\n'
        (self.root / 'debug_me.py').write_text(source, encoding='utf-8')
        start = app.debug_start('debug_me.py', [4])
        self.assertTrue(start.get('ok'), start)
        try:
            events = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(e.get('type') == 'stopped' for e in events):
                events.extend(app.debug_poll().get('events', []))
                time.sleep(0.04)
            self.assertTrue(any(e.get('type') == 'stopped' and e.get('line') == 1 for e in events), events)
            self.assertTrue(app.debug_command('step').get('ok'))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(e.get('type') == 'stopped' and e.get('line') == 2 for e in events):
                events.extend(app.debug_poll().get('events', []))
                time.sleep(0.04)
            self.assertTrue(any(e.get('type') == 'stopped' and e.get('line') == 2 for e in events), events)
            self.assertTrue(app.debug_command('continue').get('ok'))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(e.get('type') == 'stopped' and e.get('line') == 4 for e in events):
                events.extend(app.debug_poll().get('events', []))
                time.sleep(0.04)
            breakpoint_event = next((e for e in events if e.get('type') == 'stopped' and e.get('line') == 4), None)
            self.assertIsNotNone(breakpoint_event, events)
            self.assertEqual(breakpoint_event.get('reason'), 'breakpoint')
            self.assertEqual(breakpoint_event.get('locals', {}).get('total'), '3')
            self.assertTrue(app.debug_command('evaluate total * 10').get('ok'))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not any(e.get('type') == 'watch' and e.get('expression') == 'total * 10' for e in events):
                events.extend(app.debug_poll().get('events', []))
                time.sleep(0.04)
            watch = next((e for e in events if e.get('type') == 'watch' and e.get('expression') == 'total * 10'), None)
            self.assertIsNotNone(watch, events)
            self.assertEqual(watch.get('value'), '30')
            self.assertTrue(app.debug_command('continue').get('ok'))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(e.get('type') == 'terminated' for e in events):
                events.extend(app.debug_poll().get('events', []))
                time.sleep(0.04)
            self.assertTrue(any(e.get('type') == 'terminated' and e.get('returncode') == 0 for e in events), events)
            self.assertTrue(any(e.get('type') == 'console' and 'result 3' in e.get('text', '') for e in events), events)
        finally:
            app.debug_stop()

    def test_python_conditional_breakpoint_only_stops_when_expression_is_true(self):
        source = 'hits = []\nfor item in range(5):\n    hits.append(item)\nprint("values", hits)\n'
        (self.root / 'condition.py').write_text(source, encoding='utf-8')
        started = app.debug_start('condition.py', [3], None, {"3": "item == 3"})
        self.assertTrue(started.get('ok'), started)
        self.assertEqual(started.get('breakpoint_conditions'), {"3": "item == 3"})
        try:
            events = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(e.get('type') == 'stopped' for e in events):
                events.extend(app.debug_poll().get('events', [])); time.sleep(0.03)
            first = next((e for e in events if e.get('type') == 'stopped'), None)
            self.assertIsNotNone(first, events)
            self.assertEqual(first.get('line'), 1)  # deterministic initial pause
            self.assertTrue(app.debug_command('continue').get('ok'))
            stop = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and stop is None:
                events.extend(app.debug_poll().get('events', []))
                stop = next((e for e in events if e.get('type') == 'stopped' and e.get('line') == 3), None)
                time.sleep(0.025)
            self.assertIsNotNone(stop, events)
            self.assertEqual(stop.get('locals', {}).get('item'), '3')
            self.assertEqual(stop.get('reason'), 'breakpoint')
            self.assertTrue(app.debug_command('continue').get('ok'))
            terminated = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not terminated:
                events.extend(app.debug_poll().get('events', []))
                terminated = any(e.get('type') == 'terminated' for e in events)
                time.sleep(0.025)
            self.assertTrue(terminated, events)
        finally:
            app.debug_stop()

    def test_python_conditional_breakpoint_rejects_bad_conditions(self):
        (self.root / 'condition.py').write_text('value = 1\nprint(value)\n', encoding='utf-8')
        self.assertIn('Invalid condition', app.debug_start('condition.py', [1], None, {"1": "value =="}).get('error', ''))
        self.assertIn('enabled breakpoint line', app.debug_start('condition.py', [1], None, {"2": "value == 1"}).get('error', ''))
        self.assertIn('one line', app.debug_start('condition.py', [1], None, {"1": "value == 1\nvalue == 2"}).get('error', ''))

    def test_debugger_rejects_non_python_and_unsafe_paths(self):
        (self.root / 'notes.txt').write_text('hello', encoding='utf-8')
        self.assertIn('error', app.debug_start('notes.txt', []))
        self.assertIn('error', app.debug_start('../outside.py', []))
        self.assertIn('error', app.debug_command('run arbitrary shell'))

    def test_workspace_task_discovery_and_process_task_run(self):
        import json
        vscode = self.root / '.vscode'
        vscode.mkdir()
        (vscode / 'tasks.json').write_text(json.dumps({"version": "2.0.0", "tasks": [{
            "label": "Unit smoke", "type": "process", "command": sys.executable,
            "args": ["-c", "print('task-ok')"], "group": "test"
        }]}), encoding='utf-8')
        listed = app.list_tasks()
        self.assertIsNone(listed.get('error'))
        self.assertEqual([task['label'] for task in listed['tasks']], ['Unit smoke'])
        result = app.run_task('Unit smoke')
        self.assertEqual(result.get('returncode'), 0, result)
        self.assertIn('task-ok', result.get('stdout', ''))

    def test_shell_task_executes_and_task_environment_expands(self):
        import json
        subdir = self.root / 'subdir'
        subdir.mkdir()
        vscode = self.root / '.vscode'
        vscode.mkdir()
        command = json.dumps({"version": "2.0.0", "tasks": [
            {"label": "Shell smoke", "type": "shell", "command": sys.executable,
             "args": ["-c", "import os; print(os.environ['FLOWCODE_FLAG']); print(os.getcwd())"],
             "options": {"cwd": "subdir", "env": {"FLOWCODE_FLAG": "${workspaceFolderBasename}-ok"}}}
        ]})
        (vscode / 'tasks.json').write_text(command, encoding='utf-8')
        result = app.run_task('Shell smoke')
        self.assertEqual(result.get('returncode'), 0, result)
        self.assertIn(self.root.name + '-ok', result.get('stdout', ''))
        self.assertIn(str(subdir), result.get('stdout', ''))

    def test_workspace_replace_preview_is_case_insensitive_and_read_only(self):
        (self.root / 'one.py').write_text('Hello hello HELLO\n', encoding='utf-8')
        (self.root / 'notes.md').write_text('Say hello again.\n', encoding='utf-8')
        result = app.preview_workspace_replace('hello', 'welcome', False)
        self.assertNotIn('error', result, result)
        self.assertEqual(result['matches'], 4)
        self.assertEqual(result['files_changed'], 2)
        by_path = {item['path']: item for item in result['changes']}
        self.assertIn('welcome welcome welcome', by_path['one.py']['new_content'])
        self.assertEqual((self.root / 'one.py').read_text(encoding='utf-8'), 'Hello hello HELLO\n')
        self.assertEqual((self.root / 'notes.md').read_text(encoding='utf-8'), 'Say hello again.\n')

    def test_workspace_replace_apply_and_stale_file_guard(self):
        (self.root / 'one.py').write_text('value = "hello"\n', encoding='utf-8')
        (self.root / 'two.js').write_text('const value = "hello";\n', encoding='utf-8')
        result = app.preview_workspace_replace('hello', 'welcome', True)
        self.assertNotIn('error', result, result)
        (self.root / 'two.js').write_text('// external edit\n' + (self.root / 'two.js').read_text(encoding='utf-8'), encoding='utf-8')
        blocked = app.apply_workspace_replace(result['changes'])
        self.assertIn('error', blocked)
        self.assertEqual((self.root / 'one.py').read_text(encoding='utf-8'), 'value = "hello"\n')
        self.assertIn('external edit', (self.root / 'two.js').read_text(encoding='utf-8'))
        fresh = app.preview_workspace_replace('hello', 'welcome', True)
        self.assertNotIn('error', fresh, fresh)
        applied = app.apply_workspace_replace(fresh['changes'])
        self.assertTrue(applied.get('ok'), applied)
        self.assertIn('welcome', (self.root / 'one.py').read_text(encoding='utf-8'))
        self.assertIn('welcome', (self.root / 'two.js').read_text(encoding='utf-8'))

    def test_workspace_replace_rolls_back_on_replace_failure(self):
        from unittest.mock import patch
        import os
        (self.root / 'one.py').write_text('hello one\n', encoding='utf-8')
        (self.root / 'two.py').write_text('hello two\n', encoding='utf-8')
        originals = {name: (self.root / name).read_text(encoding='utf-8') for name in ('one.py', 'two.py')}
        preview = app.preview_workspace_replace('hello', 'welcome', True)
        self.assertNotIn('error', preview, preview)
        real_replace = os.replace
        calls = {'n': 0}
        def fail_second(src, dst):
            calls['n'] += 1
            if calls['n'] == 2:
                raise OSError('simulated replacement failure')
            return real_replace(src, dst)
        with patch('os.replace', side_effect=fail_second):
            result = app.apply_workspace_replace(preview['changes'])
        self.assertIn('error', result)
        for name, content in originals.items():
            self.assertEqual((self.root / name).read_text(encoding='utf-8'), content)

    def test_task_configuration_reports_invalid_json(self):
        vscode = self.root / '.vscode'
        vscode.mkdir()
        (vscode / 'tasks.json').write_text('{"tasks": [}', encoding='utf-8')
        result = app.list_tasks()
        self.assertIn('error', result)
        self.assertIn('Invalid .vscode/tasks.json', result['error'])

    def test_run_task_rejects_unknown_label(self):
        import json
        vscode = self.root / '.vscode'
        vscode.mkdir()
        (vscode / 'tasks.json').write_text(json.dumps({'tasks': []}), encoding='utf-8')
        result = app.run_task('missing')
        self.assertIn('error', result)
        self.assertIn('Task not found', result['error'])

    def test_task_working_directory_cannot_escape_workspace(self):
        import json
        vscode = self.root / '.vscode'
        vscode.mkdir()
        (vscode / 'tasks.json').write_text(json.dumps({"tasks": [{
            "label": "Unsafe cwd", "type": "process", "command": sys.executable,
            "args": ["-c", "print('bad')"], "options": {"cwd": "../"}
        }]}), encoding='utf-8')
        self.assertIn('error', app.run_task('Unsafe cwd'))

    @unittest.skipUnless(shutil.which('node') and __import__('importlib').util.find_spec('websocket'),
                         'Node.js and websocket-client are required for Node Inspector integration tests.')
    def test_node_debugger_breakpoint_locals_and_exit(self):
        source = 'let value = 1;\nvalue += 2;\nconsole.log("value", value);\n'
        (self.root / 'debug.js').write_text(source, encoding='utf-8')
        started = app.node_debug_start('debug.js', [])
        self.assertTrue(started.get('ok'), started)
        events = []
        def collect_until(predicate, timeout=8):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                poll = app.node_debug_poll()
                events.extend(poll.get('events', []))
                if predicate(events, poll):
                    return poll
                time.sleep(0.04)
            self.fail(f'Timed out waiting for Node debugger event. Events: {events!r}')
        try:
            collect_until(lambda es, _p: any(e.get('type') == 'stopped' and e.get('line') == 1 for e in es))
            self.assertTrue(app.node_debug_command('breakpoint 3').get('ok'))
            self.assertTrue(app.node_debug_command('continue').get('ok'))
            collect_until(lambda es, _p: any(e.get('type') == 'stopped' and e.get('line') == 3 for e in es))
            stopped = next(e for e in events if e.get('type') == 'stopped' and e.get('line') == 3)
            self.assertEqual(stopped.get('reason'), 'breakpoint')
            self.assertEqual(stopped.get('locals', {}).get('value'), '3')
            watch = app.node_debug_command('evaluate value * 10')
            self.assertTrue(watch.get('ok'), watch)
            self.assertEqual(watch.get('value'), '30', watch)
            watch_error = app.node_debug_command('evaluate missingName')
            self.assertIn('error', watch_error)
            self.assertTrue(app.node_debug_command('locals').get('ok'))
            collect_until(lambda es, _p: any(e.get('type') == 'locals' and e.get('locals', {}).get('value') == '3' for e in es))
            self.assertTrue(app.node_debug_command('continue').get('ok'))
            collect_until(lambda es, _p: any(e.get('type') == 'terminated' for e in es), timeout=10)
            self.assertTrue(any(e.get('type') == 'terminated' and e.get('returncode') == 0 for e in events), events)
            output = ''.join(e.get('text', '') for e in events if e.get('type') == 'console')
            self.assertEqual(output.count('value 3'), 1, output)
        finally:
            app.node_debug_stop()

    @unittest.skipUnless(shutil.which('node') and __import__('importlib').util.find_spec('websocket'),
                         'Node.js and websocket-client are required for Node Inspector integration tests.')
    def test_node_debugger_step_into_and_step_over(self):
        source = 'let value = 1;\nvalue += 1;\nvalue += 1;\nconsole.log(value);\n'
        (self.root / 'step.js').write_text(source, encoding='utf-8')
        started = app.node_debug_start('step.js', [])
        self.assertTrue(started.get('ok'), started)
        events = []
        def wait_for_line(line, timeout=6):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = app.node_debug_poll()
                events.extend(result.get('events', []))
                found = next((event for event in events if event.get('type') == 'stopped' and event.get('line') == line), None)
                if found:
                    return found
                time.sleep(0.04)
            self.fail(f'Did not pause at line {line}; events: {events!r}')
        try:
            wait_for_line(1)
            self.assertTrue(app.node_debug_command('step').get('ok'))
            second = wait_for_line(2)
            self.assertEqual(second.get('locals', {}).get('value'), '1')
            self.assertTrue(app.node_debug_command('next').get('ok'))
            third = wait_for_line(3)
            self.assertEqual(third.get('locals', {}).get('value'), '2')
            self.assertTrue(app.node_debug_command('continue').get('ok'))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                result = app.node_debug_poll()
                events.extend(result.get('events', []))
                if any(event.get('type') == 'terminated' for event in events):
                    break
                time.sleep(0.04)
            self.assertTrue(any(event.get('type') == 'terminated' and event.get('returncode') == 0 for event in events), events)
        finally:
            app.node_debug_stop()

    @unittest.skipUnless(shutil.which('node') and __import__('importlib').util.find_spec('websocket'),
                         'Node.js and websocket-client are required for Node Inspector integration tests.')
    def test_node_debugger_stop_terminates_child_process(self):
        (self.root / 'long.js').write_text('setInterval(() => {}, 10000);\n', encoding='utf-8')
        started = app.node_debug_start('long.js', [])
        self.assertTrue(started.get('ok'), started)
        try:
            self.assertTrue(app.node_debug_command('stop').get('ok'))
            deadline = time.monotonic() + 6
            observed = []
            while time.monotonic() < deadline:
                result = app.node_debug_poll()
                observed.extend(result.get('events', []))
                if result.get('returncode') is not None or app._node_debug_session is None:
                    break
                time.sleep(0.04)
            self.assertIsNone(app._node_debug_session)
            self.assertTrue(any(event.get('type') == 'terminated' and event.get('message') == 'Node.js debug session stopped.' for event in observed), observed)
        finally:
            app.node_debug_stop()

    def test_debugger_can_set_and_clear_breakpoints_while_paused(self):
        (self.root / 'simple.py').write_text('a = 1\nb = a + 1\nprint(b)\n', encoding='utf-8')
        result = app.debug_start('simple.py', [])
        self.assertTrue(result.get('ok'), result)
        try:
            self.assertTrue(app.debug_command('breakpoint 3 count == 2').get('ok'))
            self.assertTrue(app.debug_command('breakpoint 3 Count == 2').get('ok'))
            self.assertIn('Invalid conditional breakpoint expression', app.debug_command('breakpoint 3 count ==').get('error', ''))
            self.assertTrue(app.debug_command('breakpoint 3').get('ok'))
            self.assertTrue(app.debug_command('clear 3').get('ok'))
        finally:
            app.debug_stop()

    @unittest.skipIf(sys.platform.startswith('win'), 'POSIX PTY behavior is tested on POSIX; Windows uses optional WinPTY or pipe fallback.')
    def test_persistent_terminal_uses_a_real_tty_for_interactive_programs(self):
        import shlex
        import time
        start = app.terminal_start()
        self.assertTrue(start.get('ok'), start)
        self.assertTrue(start.get('pty'), start)
        try:
            executable = shlex.quote(sys.executable)
            command = f"{executable} -c 'import os; print(\"FLOWCODE_TTY_OK\", os.isatty(0))'"
            self.assertTrue(app.terminal_write(command).get('ok'))
            collected = ''
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and 'FLOWCODE_TTY_OK True' not in collected:
                collected += app.terminal_poll().get('output', '')
                time.sleep(0.04)
            self.assertIn('FLOWCODE_TTY_OK True', collected, collected)
            status = app.terminal_poll()
            self.assertTrue(status.get('running'), status)
        finally:
            app.terminal_stop()

    def test_persistent_terminal_keeps_working_directory(self):
        (self.root / 'subdir').mkdir()
        start = app.terminal_start()
        self.assertTrue(start.get('ok'), start)
        try:
            self.assertTrue(app.terminal_write('cd subdir').get('ok'))
            self.assertTrue(app.terminal_write('pwd').get('ok'))
            import time
            collected = ''
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and str(self.root / 'subdir') not in collected:
                collected += app.terminal_poll().get('output', '')
                time.sleep(0.05)
            self.assertIn(str(self.root / 'subdir'), collected)
        finally:
            app.terminal_stop()


class GitWorkflowTests(unittest.TestCase):
    """Real local Git integration tests; never contact a remote."""
    @classmethod
    def setUpClass(cls):
        if not shutil.which('git'):
            raise unittest.SkipTest('Git executable is not installed')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assertNotIn('error', app.set_workspace(str(self.root)))
        initialized = app.git_action('init')
        self.assertEqual(initialized.get('returncode'), 0, initialized)
        self.assertEqual(app._execute(['git', 'config', 'user.name', 'FlowCode Test']).get('returncode'), 0)
        self.assertEqual(app._execute(['git', 'config', 'user.email', 'flowcode-tests@example.invalid']).get('returncode'), 0)

    def tearDown(self):
        self.temp.cleanup()

    def test_stage_unstage_before_first_commit_preserves_worktree_file(self):
        file = self.root / 'new file.txt'
        file.write_text('keep this file\n', encoding='utf-8')
        staged = app.git_action('add', 'new file.txt')
        self.assertEqual(staged.get('returncode'), 0, staged)
        unstaged = app.git_action('unstage', 'new file.txt')
        self.assertEqual(unstaged.get('returncode'), 0, unstaged)
        self.assertTrue(file.exists(), 'unstaging must not delete the worktree file')
        self.assertEqual(file.read_text(encoding='utf-8'), 'keep this file\n')
        status = app.git_action('status')
        self.assertIn('?? ', status.get('stdout', ''), status)
        self.assertIn('new file.txt', status.get('stdout', ''), status)
        self.assertTrue(any(item['path'] == 'new file.txt' and item['untracked'] for item in status.get('files', [])), status)

    def test_status_snapshot_preserves_renamed_and_spaced_paths(self):
        old = self.root / 'old file.txt'
        old.write_text('initial\n', encoding='utf-8')
        self.assertEqual(app.git_action('add', 'old file.txt').get('returncode'), 0)
        self.assertEqual(app.git_action('commit', 'initial commit').get('returncode'), 0)
        self.assertEqual(app._execute(['git', 'mv', 'old file.txt', 'renamed file.txt']).get('returncode'), 0)
        (self.root / 'new file.txt').write_text('untracked\n', encoding='utf-8')
        status = app.git_action('status')
        self.assertEqual(status.get('returncode'), 0, status)
        renamed = next((item for item in status.get('files', []) if item['path'] == 'renamed file.txt'), None)
        self.assertIsNotNone(renamed, status)
        self.assertTrue(renamed['renamed'], renamed)
        self.assertEqual(renamed.get('original_path'), 'old file.txt', renamed)
        self.assertTrue(any(item['path'] == 'new file.txt' and item['untracked'] for item in status['files']), status)

    def test_branch_create_list_and_switch(self):
        (self.root / 'tracked.txt').write_text('initial\n', encoding='utf-8')
        self.assertEqual(app.git_action('add', 'tracked.txt').get('returncode'), 0)
        commit = app.git_action('commit', 'initial commit')
        self.assertEqual(commit.get('returncode'), 0, commit)
        default_branch = app._execute(['git', 'branch', '--show-current']).get('stdout', '').strip()
        self.assertTrue(default_branch)
        created = app.git_action('branch-create', 'feature/git-panel')
        self.assertEqual(created.get('returncode'), 0, created)
        status = app.git_action('status')
        self.assertIn('## feature/git-panel', status.get('stdout', ''), status)
        branches = app.git_action('branch-list')
        self.assertEqual(branches.get('returncode'), 0, branches)
        self.assertIn('feature/git-panel', branches.get('stdout', ''))
        switched = app.git_action('branch-switch', default_branch)
        self.assertEqual(switched.get('returncode'), 0, switched)
        self.assertIn(f'## {default_branch}', app.git_action('status').get('stdout', ''))

    def test_branch_name_validation_rejects_option_and_shell_payloads(self):
        bad_names = ['--help', 'bad name', 'x; touch PWNED', '../escape', 'name..suffix']
        for branch in bad_names:
            with self.subTest(branch=branch):
                result = app.git_action('branch-create', branch)
                self.assertTrue(result.get('error') or result.get('returncode') != 0, result)
        self.assertFalse((self.root / 'PWNED').exists())

    def test_diff_and_staged_diff_support_one_workspace_relative_file(self):
        (self.root / 'tracked.txt').write_text('line one\n', encoding='utf-8')
        self.assertEqual(app.git_action('add', 'tracked.txt').get('returncode'), 0)
        self.assertEqual(app.git_action('commit', 'initial commit').get('returncode'), 0)
        (self.root / 'tracked.txt').write_text('line two\n', encoding='utf-8')
        unstaged = app.git_action('diff', 'tracked.txt')
        self.assertEqual(unstaged.get('returncode'), 0, unstaged)
        self.assertIn('-line one', unstaged.get('stdout', ''))
        self.assertIn('+line two', unstaged.get('stdout', ''))
        self.assertEqual(app.git_action('add', 'tracked.txt').get('returncode'), 0)
        staged = app.git_action('diff-cached', 'tracked.txt')
        self.assertEqual(staged.get('returncode'), 0, staged)
        self.assertIn('+line two', staged.get('stdout', ''))

    def test_stage_and_unstage_individual_git_hunks(self):
        import json
        file = self.root / "tracked.txt"
        original = "".join(f"line {number}\n" for number in range(1, 41))
        file.write_text(original, encoding="utf-8")
        self.assertEqual(app.git_action("add", "tracked.txt").get("returncode"), 0)
        self.assertEqual(app.git_action("commit", "hunk baseline").get("returncode"), 0)
        updated = original.replace("line 2\n", "changed two\n").replace("line 35\n", "changed thirty-five\n")
        file.write_text(updated, encoding="utf-8")

        listed = app.git_action("hunks", "tracked.txt")
        self.assertEqual(listed.get("returncode"), 0, listed)
        self.assertEqual(len(listed["unstaged_hunks"]), 2, listed)
        chosen = listed["unstaged_hunks"][0]
        staged = app.git_action("stage-hunk", json.dumps({"path": "tracked.txt", "index": chosen["index"], "signature": chosen["signature"]}))
        self.assertEqual(staged.get("returncode"), 0, staged)
        cached = app.git_action("diff-cached", "tracked.txt").get("stdout", "")
        self.assertIn("+changed two", cached)
        self.assertNotIn("+changed thirty-five", cached)
        refreshed = app.git_action("hunks", "tracked.txt")
        self.assertEqual(len(refreshed["staged_hunks"]), 1, refreshed)
        self.assertEqual(len(refreshed["unstaged_hunks"]), 1, refreshed)

        stage_item = refreshed["staged_hunks"][0]
        unstaged = app.git_action("unstage-hunk", json.dumps({"path": "tracked.txt", "index": stage_item["index"], "signature": stage_item["signature"]}))
        self.assertEqual(unstaged.get("returncode"), 0, unstaged)
        self.assertEqual(app.git_action("diff-cached", "tracked.txt").get("stdout", "").strip(), "")
        self.assertIn("+changed two", app.git_action("diff", "tracked.txt").get("stdout", ""))
        self.assertIn("+changed thirty-five", app.git_action("diff", "tracked.txt").get("stdout", ""))

    def test_discard_git_hunk_reverts_only_the_selected_unstaged_change(self):
        import json
        file = self.root / "tracked.txt"
        original = "".join(f"line {number}\n" for number in range(1, 41))
        file.write_text(original, encoding="utf-8")
        self.assertEqual(app.git_action("add", "tracked.txt").get("returncode"), 0)
        self.assertEqual(app.git_action("commit", "discard baseline").get("returncode"), 0)
        updated = original.replace("line 2\n", "changed two\n").replace("line 35\n", "changed thirty-five\n")
        file.write_text(updated, encoding="utf-8")
        listed = app.git_action("hunks", "tracked.txt")
        self.assertEqual(len(listed["unstaged_hunks"]), 2, listed)
        chosen = listed["unstaged_hunks"][0]
        result = app.git_action("discard-hunk", json.dumps({"path": "tracked.txt", "index": chosen["index"], "signature": chosen["signature"]}))
        self.assertEqual(result.get("returncode"), 0, result)
        after = file.read_text(encoding="utf-8")
        self.assertIn("line 2\n", after)
        self.assertNotIn("changed two\n", after)
        self.assertIn("changed thirty-five\n", after)
        remaining = app.git_action("hunks", "tracked.txt")
        self.assertEqual(len(remaining["unstaged_hunks"]), 1, remaining)
        self.assertIn("+changed thirty-five", app.git_action("diff", "tracked.txt").get("stdout", ""))

    def test_git_hunk_actions_reject_bad_paths_untracked_and_stale_signatures(self):
        import json
        file = self.root / "tracked.txt"
        file.write_text("base\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\n", encoding="utf-8")
        self.assertEqual(app.git_action("add", "tracked.txt").get("returncode"), 0)
        self.assertEqual(app.git_action("commit", "baseline").get("returncode"), 0)
        file.write_text("changed\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\n", encoding="utf-8")
        hunks = app.git_action("hunks", "tracked.txt")["unstaged_hunks"]
        chosen = hunks[0]
        file.write_text("different external edit\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\n", encoding="utf-8")
        stale = app.git_action("stage-hunk", json.dumps({"path": "tracked.txt", "index": 0, "signature": chosen["signature"]}))
        self.assertIn("error", stale, stale)
        bad_index = app.git_action("stage-hunk", json.dumps({"path": "tracked.txt", "index": 999, "signature": chosen["signature"]}))
        self.assertIn("error", bad_index, bad_index)
        escaped = app.git_action("hunks", "../outside.txt")
        self.assertIn("error", escaped, escaped)
        (self.root / "new.txt").write_text("untracked\n", encoding="utf-8")
        untracked = app.git_action("hunks", "new.txt")
        self.assertIn("error", untracked, untracked)

    def test_git_path_actions_reject_workspace_escape(self):
        outside = self.root.parent / 'outside-flowcode.txt'
        try:
            for action in ('add', 'unstage', 'diff', 'diff-cached', 'log'):
                with self.subTest(action=action):
                    result = app.git_action(action, '../outside-flowcode.txt')
                    self.assertIn('error', result, result)
        finally:
            if outside.exists():
                outside.unlink()

    def test_remote_fetch_fast_forward_pull_and_push_against_local_bare_repository(self):
        import json
        import subprocess

        with tempfile.TemporaryDirectory() as external:
            base = Path(external)
            bare = base / 'origin.git'
            peer = base / 'peer'
            subprocess.run(['git', 'init', '--bare', str(bare)], check=True, capture_output=True, text=True)

            tracked = self.root / 'base.txt'
            tracked.write_text('base\n', encoding='utf-8')
            self.assertEqual(app.git_action('add', 'base.txt').get('returncode'), 0)
            self.assertEqual(app.git_action('commit', 'initial').get('returncode'), 0)
            branch = app._execute(['git', 'branch', '--show-current']).get('stdout', '').strip()
            self.assertTrue(branch)
            self.assertEqual(app._execute(['git', 'remote', 'add', 'origin', str(bare)]).get('returncode'), 0)
            push_initial = app.git_action('push', json.dumps({'remote': 'origin', 'branch': branch}))
            self.assertEqual(push_initial.get('returncode'), 0, push_initial)
            remotes = app.git_action('remote-list')
            self.assertEqual(remotes.get('returncode'), 0, remotes)
            self.assertEqual(remotes.get('stdout', '').strip(), 'origin')

            cloned = subprocess.run(['git', 'clone', '--branch', branch, str(bare), str(peer)],
                                    check=True, capture_output=True, text=True)
            subprocess.run(['git', 'config', 'user.name', 'FlowCode Peer'], cwd=peer, check=True)
            subprocess.run(['git', 'config', 'user.email', 'peer@example.invalid'], cwd=peer, check=True)
            (peer / 'from-peer.txt').write_text('remote update\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'from-peer.txt'], cwd=peer, check=True)
            subprocess.run(['git', 'commit', '-m', 'peer update'], cwd=peer, check=True, capture_output=True, text=True)
            subprocess.run(['git', 'push', 'origin', branch], cwd=peer, check=True, capture_output=True, text=True)

            fetched = app.git_action('fetch', 'origin')
            self.assertEqual(fetched.get('returncode'), 0, fetched)
            remote_branches = app.git_action('remote-branch-list')
            self.assertEqual(remote_branches.get('returncode'), 0, remote_branches)
            self.assertIn(f'origin/{branch}', remote_branches.get('stdout', ''))
            pulled = app.git_action('pull', json.dumps({'remote': 'origin', 'branch': branch}))
            self.assertEqual(pulled.get('returncode'), 0, pulled)
            self.assertEqual((self.root / 'from-peer.txt').read_text(encoding='utf-8'), 'remote update\n')

            local_file = self.root / 'local-push.txt'
            local_file.write_text('local update\n', encoding='utf-8')
            self.assertEqual(app.git_action('add', 'local-push.txt').get('returncode'), 0)
            self.assertEqual(app.git_action('commit', 'local update').get('returncode'), 0)
            pushed = app.git_action('push', json.dumps({'remote': 'origin', 'branch': branch}))
            self.assertEqual(pushed.get('returncode'), 0, pushed)
            remote_head = subprocess.run(['git', 'rev-parse', f'refs/heads/{branch}'], cwd=bare,
                                         check=True, capture_output=True, text=True).stdout.strip()
            local_head = app._execute(['git', 'rev-parse', 'HEAD']).get('stdout', '').strip()
            self.assertEqual(remote_head, local_head)

    def test_remote_actions_reject_unknown_remote_and_malformed_branch(self):
        import json
        invalid_fetch = app.git_action('fetch', '--all')
        self.assertIn('error', invalid_fetch)
        unknown_pull = app.git_action('pull', json.dumps({'remote': 'not-configured', 'branch': 'main'}))
        self.assertIn('error', unknown_pull)
        with tempfile.TemporaryDirectory() as remote_dir:
            remote_path = str(Path(remote_dir) / 'origin.git')
            self.assertEqual(app._execute(['git', 'remote', 'add', 'origin', remote_path]).get('returncode'), 0)
            bad_branch = app.git_action('push', json.dumps({'remote': 'origin', 'branch': '--force'}))
            self.assertIn('error', bad_branch)
            self.assertIn('valid branch name', bad_branch['error'])
        self.assertNotEqual(app._execute(['git', 'status']).get('returncode'), 127)

    def test_merge_conflict_list_and_abort(self):
        tracked = self.root / 'shared.txt'
        tracked.write_text('base line\n', encoding='utf-8')
        self.assertEqual(app.git_action('add', 'shared.txt').get('returncode'), 0)
        self.assertEqual(app.git_action('commit', 'base commit').get('returncode'), 0)
        main_branch = app._execute(['git', 'branch', '--show-current']).get('stdout', '').strip()
        self.assertEqual(app.git_action('branch-create', 'feature/conflict-test').get('returncode'), 0)
        tracked.write_text('feature version\n', encoding='utf-8')
        self.assertEqual(app.git_action('add', 'shared.txt').get('returncode'), 0)
        self.assertEqual(app.git_action('commit', 'feature edit').get('returncode'), 0)
        self.assertEqual(app.git_action('branch-switch', main_branch).get('returncode'), 0)
        tracked.write_text('main version\n', encoding='utf-8')
        self.assertEqual(app.git_action('add', 'shared.txt').get('returncode'), 0)
        self.assertEqual(app.git_action('commit', 'main edit').get('returncode'), 0)

        merged = app.git_action('merge', 'feature/conflict-test')
        self.assertNotEqual(merged.get('returncode'), 0, merged)
        conflicts = app.git_action('conflicts')
        self.assertEqual(conflicts.get('returncode'), 0, conflicts)
        self.assertIn('shared.txt', [item['path'] for item in conflicts.get('conflicts', [])])
        self.assertIn('<<<<<<<', tracked.read_text(encoding='utf-8'))
        aborted = app.git_action('merge-abort')
        self.assertEqual(aborted.get('returncode'), 0, aborted)
        self.assertEqual(tracked.read_text(encoding='utf-8'), 'main version\n')
        self.assertEqual(app.git_action('conflicts').get('conflicts'), [])

    def test_merge_abort_rejects_when_no_merge_is_in_progress(self):
        result = app.git_action('merge-abort')
        self.assertIn('error', result)

    def test_pull_fast_forward_only_refuses_divergence_without_overwriting_local_work(self):
        import json
        import subprocess

        with tempfile.TemporaryDirectory() as external:
            base = Path(external)
            bare = base / 'origin.git'
            peer = base / 'peer'
            subprocess.run(['git', 'init', '--bare', str(bare)], check=True, capture_output=True, text=True)
            (self.root / 'base.txt').write_text('base\n', encoding='utf-8')
            self.assertEqual(app.git_action('add', 'base.txt').get('returncode'), 0)
            self.assertEqual(app.git_action('commit', 'base').get('returncode'), 0)
            branch = app._execute(['git', 'branch', '--show-current']).get('stdout', '').strip()
            self.assertEqual(app._execute(['git', 'remote', 'add', 'origin', str(bare)]).get('returncode'), 0)
            self.assertEqual(app.git_action('push', json.dumps({'remote': 'origin', 'branch': branch})).get('returncode'), 0)
            subprocess.run(['git', 'clone', '--branch', branch, str(bare), str(peer)], check=True, capture_output=True, text=True)
            subprocess.run(['git', 'config', 'user.name', 'FlowCode Peer'], cwd=peer, check=True)
            subprocess.run(['git', 'config', 'user.email', 'peer@example.invalid'], cwd=peer, check=True)
            (peer / 'remote-only.txt').write_text('remote commit\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'remote-only.txt'], cwd=peer, check=True)
            subprocess.run(['git', 'commit', '-m', 'remote commit'], cwd=peer, check=True, capture_output=True, text=True)
            subprocess.run(['git', 'push', 'origin', branch], cwd=peer, check=True, capture_output=True, text=True)
            (self.root / 'local-only.txt').write_text('local commit\n', encoding='utf-8')
            self.assertEqual(app.git_action('add', 'local-only.txt').get('returncode'), 0)
            self.assertEqual(app.git_action('commit', 'local commit').get('returncode'), 0)
            self.assertEqual(app.git_action('fetch', 'origin').get('returncode'), 0)
            pulled = app.git_action('pull', json.dumps({'remote': 'origin', 'branch': branch}))
            self.assertNotEqual(pulled.get('returncode'), 0, pulled)
            self.assertIn('fast-forward', (pulled.get('stderr', '') + pulled.get('stdout', '')).lower())
            self.assertTrue((self.root / 'local-only.txt').exists())
            self.assertFalse((self.root / 'remote-only.txt').exists())
            self.assertEqual(app.git_action('conflicts').get('conflicts'), [])


    def test_side_by_side_compare_aligns_changes_and_supports_untracked_file(self):
        import json
        tracked = self.root / 'tracked.txt'
        tracked.write_text('stable line\nold line\n', encoding='utf-8')
        self.assertEqual(app.git_action('add', 'tracked.txt').get('returncode'), 0)
        self.assertEqual(app.git_action('commit', 'tracked baseline').get('returncode'), 0)
        tracked.write_text('stable line\nnew line\n', encoding='utf-8')
        compared = app.git_action('compare', json.dumps({'path': 'tracked.txt'}))
        self.assertEqual(compared.get('returncode'), 0, compared)
        self.assertEqual(compared.get('path'), 'tracked.txt')
        self.assertTrue(any(row.get('left') == 'old line' and row.get('kind') == 'replace' for row in compared.get('rows', [])), compared)
        self.assertTrue(any(row.get('right') == 'new line' and row.get('kind') == 'replace' for row in compared.get('rows', [])), compared)
        self.assertTrue(any(row.get('kind') == 'same' and row.get('left') == 'stable line' for row in compared['rows']), compared)

        (self.root / 'untracked file.txt').write_text('brand new\n', encoding='utf-8')
        untracked = app.git_action('compare', json.dumps({'path': 'untracked file.txt'}))
        self.assertEqual(untracked.get('returncode'), 0, untracked)
        self.assertTrue(any(row.get('right') == 'brand new' and row.get('kind') == 'insert' for row in untracked.get('rows', [])), untracked)

    def test_side_by_side_compare_rejects_workspace_escape_and_bad_request(self):
        import json
        outside = self.root.parent / 'outside-flowcode-compare.txt'
        try:
            self.assertIn('error', app.git_action('compare', json.dumps({'path': '../outside-flowcode-compare.txt'})))
            self.assertIn('error', app.git_action('compare', '{broken-json'))
        finally:
            if outside.exists():
                outside.unlink()

    def test_side_by_side_compare_rejects_binary_and_oversized_files(self):
        (self.root / 'binary.bin').write_bytes(b'head\x00tail')
        binary = app.git_action('compare', 'binary.bin')
        self.assertIn('error', binary)
        self.assertIn('Binary files', binary['error'])
        (self.root / 'large.txt').write_text('x' * (1024 * 1024 + 1), encoding='utf-8')
        large = app.git_action('compare', 'large.txt')
        self.assertIn('error', large)
        self.assertIn('1 MB', large['error'])


class WorkspaceSymbolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        result = app.set_workspace(str(self.root))
        self.assertNotIn("error", result)

    def tearDown(self):
        self.temp.cleanup()

    def test_workspace_symbols_discovers_python_nested_and_javascript_declarations(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "module.py").write_text(
            "class Engine:\n    async def start(self):\n        return True\n\ndef helper(value):\n    return value\n\nCOUNT: int = 2\n", encoding="utf-8")
        (self.root / "src" / "ui.ts").write_text(
            "export interface User { name: string }\nexport type UserId = string;\nexport class Panel {}\nexport const makePanel = () => new Panel();\nconst answer = 42;\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "fake.js").write_text("function shouldNotAppear() {}\n", encoding="utf-8")
        result = app.workspace_symbols()
        self.assertNotIn("error", result, result)
        names = {(item["name"], item["path"], item["kind"]) for item in result["symbols"]}
        self.assertIn(("Engine", "src/module.py", "class"), names)
        self.assertIn(("start", "src/module.py", "async function"), names)
        self.assertIn(("helper", "src/module.py", "function"), names)
        self.assertIn(("COUNT", "src/module.py", "variable"), names)
        self.assertIn(("User", "src/ui.ts", "interface"), names)
        self.assertIn(("UserId", "src/ui.ts", "type"), names)
        self.assertIn(("Panel", "src/ui.ts", "class"), names)
        self.assertIn(("makePanel", "src/ui.ts", "function"), names)
        self.assertNotIn("shouldNotAppear", {item["name"] for item in result["symbols"]})

    def test_workspace_symbols_use_unsaved_buffers_and_include_new_files(self):
        (self.root / "module.py").write_text("def on_disk(): pass\n", encoding="utf-8")
        result = app.workspace_symbols({
            "module.py": "def unsaved_version():\n    return 1\n",
            "new_file.ts": "export function notYetSaved() { return 1; }\n",
        })
        self.assertNotIn("error", result, result)
        found = {(item["name"], item["path"]) for item in result["symbols"]}
        self.assertIn(("unsaved_version", "module.py"), found)
        self.assertNotIn(("on_disk", "module.py"), found)
        self.assertIn(("notYetSaved", "new_file.ts"), found)

    def test_workspace_symbols_reject_unsafe_paths_and_large_buffer_payloads(self):
        traversal = app.workspace_symbols({"../outside.py": "def nope(): pass"})
        self.assertIn("error", traversal)
        windows_traversal = app.workspace_symbols({r"..\outside.py": "def nope(): pass"})
        self.assertIn("error", windows_traversal)
        too_many = app.workspace_symbols({f"file{i}.py": "" for i in range(41)})
        self.assertIn("40 open editor buffers", too_many["error"])
        too_large = app.workspace_symbols({"big.py": "x" * (1024 * 1024 + 1)})
        self.assertIn("5 MB limit", too_large["error"])

    def test_workspace_symbols_skip_invalid_python_and_symbolic_linked_source(self):
        (self.root / "broken.py").write_text("def (broken\n", encoding="utf-8")
        outside = self.root.parent / (self.root.name + "-secret.py")
        outside.write_text("def leaked_symbol(): pass\n", encoding="utf-8")
        try:
            try:
                (self.root / "secret.py").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks are unavailable in this test environment")
            result = app.workspace_symbols()
            names = {item["name"] for item in result["symbols"]}
            self.assertNotIn("leaked_symbol", names)
            self.assertNotIn("broken", names)
        finally:
            outside.unlink(missing_ok=True)


class LaunchConfigurationEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        result = app.set_workspace(str(self.root))
        self.assertNotIn("error", result)
        (self.root / "main.py").write_text("print('ready')\n", encoding="utf-8")
        (self.root / "app.js").write_text("console.log('ready');\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _python_config(self, name="Python: Main"):
        return {"name": name, "type": "python", "request": "launch", "program": "main.py",
                "cwd": "${workspaceFolder}", "args": ["hello", "two words"], "env": {"MODE": "dev"}, "runtimeArgs": []}

    def test_launch_configuration_create_update_delete_are_atomic_and_preserve_unknown_fields(self):
        created = app.save_debug_configuration("create", self._python_config(), "main.py")
        self.assertTrue(created.get("ok"), created)
        self.assertEqual(created["id"], "launch-0")
        config_path = self.root / ".vscode" / "launch.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], "0.2.0")
        self.assertEqual(data["configurations"][0]["program"], "main.py")
        self.assertEqual(data["configurations"][0]["args"], ["hello", "two words"])

        data["configurations"][0]["presentation"] = {"hidden": True, "group": "test"}
        config_path.write_text(json.dumps(data), encoding="utf-8")
        updated_config = self._python_config("Python: Edited")
        updated_config["args"] = ["next"]
        updated = app.save_debug_configuration("update", updated_config, "main.py", "launch-0")
        self.assertTrue(updated.get("ok"), updated)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(data["configurations"][0]["name"], "Python: Edited")
        self.assertEqual(data["configurations"][0]["args"], ["next"])
        self.assertEqual(data["configurations"][0]["presentation"], {"hidden": True, "group": "test"})

        removed = app.save_debug_configuration("delete", {}, "main.py", "launch-0")
        self.assertTrue(removed.get("ok"), removed)
        self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["configurations"], [])

    def test_launch_configuration_create_validates_duplicates_paths_and_types(self):
        first = app.save_debug_configuration("create", self._python_config(), "main.py")
        self.assertTrue(first.get("ok"), first)
        duplicate = app.save_debug_configuration("create", self._python_config(), "main.py")
        self.assertIn("already exists", duplicate["error"])
        unsafe = self._python_config("Unsafe")
        unsafe["program"] = "../outside.py"
        self.assertIn("Invalid launch configuration", app.save_debug_configuration("create", unsafe, "main.py")["error"])
        unsupported = self._python_config("Unsupported")
        unsupported["type"] = "ruby"
        self.assertIn("Unsupported launch type", app.save_debug_configuration("create", unsupported, "main.py")["error"])
        self.assertEqual(len(json.loads((self.root / ".vscode" / "launch.json").read_text(encoding="utf-8"))["configurations"]), 1)

    def test_launch_configuration_editor_rejects_corrupt_json_and_symlinked_directory(self):
        config_dir = self.root / ".vscode"
        config_dir.mkdir()
        config_file = config_dir / "launch.json"
        config_file.write_text("{broken", encoding="utf-8")
        corrupt = app.save_debug_configuration("create", self._python_config(), "main.py")
        self.assertIn("Invalid launch.json", corrupt["error"])
        config_file.unlink()
        config_dir.rmdir()
        outside = self.root.parent / (self.root.name + "-vscode-outside")
        outside.mkdir()
        try:
            config_dir.symlink_to(outside, target_is_directory=True)
            blocked = app.save_debug_configuration("create", self._python_config(), "main.py")
            self.assertIn("Symbolic links", blocked["error"])
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable in this test environment")
        finally:
            try: config_dir.unlink(missing_ok=True)
            except OSError: pass
            outside.rmdir()


class WorkspaceTaskEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assertNotIn("error", app.set_workspace(str(self.root)))
        (self.root / "src").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def _task(self, label="Build app"):
        return {"label": label, "type": "process", "command": "python", "args": ["-c", "print('task-ok')"],
                "cwd": "${workspaceFolder}", "env": {"FLOWCODE_MODE": "test"}, "group": "build"}

    def test_create_update_delete_preserves_unknown_root_and_task_fields(self):
        config = self.root / ".vscode" / "tasks.json"
        created = app.save_workspace_task("create", self._task())
        self.assertTrue(created.get("ok"), created)
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], "2.0.0")
        self.assertEqual(data["tasks"][0]["options"]["env"], {"FLOWCODE_MODE": "test"})
        self.assertEqual(data["tasks"][0]["args"], ["-c", "print('task-ok')"])

        data["inputs"] = [{"id": "preserved", "type": "promptString", "description": "keep me"}]
        data["tasks"][0]["presentation"] = {"reveal": "silent", "panel": "shared"}
        data["tasks"][0]["options"]["shell"] = {"executable": "bash", "args": ["-l"]}
        config.write_text(json.dumps(data), encoding="utf-8")
        updated = self._task("Build updated")
        updated["args"] = ["--version"]
        self.assertTrue(app.save_workspace_task("update", updated, "Build app").get("ok"))
        data = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(data["inputs"][0]["id"], "preserved")
        self.assertEqual(data["tasks"][0]["label"], "Build updated")
        self.assertEqual(data["tasks"][0]["args"], ["--version"])
        self.assertEqual(data["tasks"][0]["presentation"], {"reveal": "silent", "panel": "shared"})
        self.assertEqual(data["tasks"][0]["options"]["shell"], {"executable": "bash", "args": ["-l"]})
        self.assertTrue(app.save_workspace_task("delete", {}, "Build updated").get("ok"))
        self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["tasks"], [])

    def test_task_editor_rejects_duplicate_labels_bad_env_and_unsafe_cwd(self):
        self.assertTrue(app.save_workspace_task("create", self._task()).get("ok"))
        duplicate = app.save_workspace_task("create", self._task())
        self.assertIn("already exists", duplicate["error"])
        bad_env = self._task("Bad env")
        bad_env["env"] = {"NOT VALID": "x"}
        self.assertIn("environment", app.save_workspace_task("create", bad_env)["error"].lower())
        unsafe = self._task("Unsafe cwd")
        unsafe["cwd"] = "../outside"
        self.assertIn("inside the opened workspace", app.save_workspace_task("create", unsafe)["error"])
        not_dir = self._task("Missing cwd")
        not_dir["cwd"] = "missing"
        self.assertIn("existing directory", app.save_workspace_task("create", not_dir)["error"])

    def test_task_editor_refuses_corrupt_json_and_symlinked_vscode_directory(self):
        config_dir = self.root / ".vscode"
        config_dir.mkdir()
        target = config_dir / "tasks.json"
        target.write_text('{"tasks":[}', encoding="utf-8")
        self.assertIn("Invalid tasks.json", app.save_workspace_task("create", self._task())["error"])
        target.unlink()
        config_dir.rmdir()
        outside = self.root.parent / (self.root.name + "-task-outside")
        outside.mkdir()
        try:
            config_dir.symlink_to(outside, target_is_directory=True)
            result = app.save_workspace_task("create", self._task())
            self.assertIn("Symbolic links", result["error"])
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable in this test environment")
        finally:
            try: config_dir.unlink(missing_ok=True)
            except OSError: pass
            outside.rmdir()

    def test_task_editor_refuses_invalid_operations_and_invalid_task_types(self):
        self.assertIn("Choose create, update or delete", app.save_workspace_task("explode", self._task())["error"])
        bad = self._task("Unsupported")
        bad["type"] = "custom"
        self.assertIn("Supported task types", app.save_workspace_task("create", bad)["error"])
        self.assertIn("selected task no longer exists", app.save_workspace_task("delete", {}, "missing")["error"])

    def test_list_tasks_includes_editor_options(self):
        self.assertTrue(app.save_workspace_task("create", self._task()).get("ok"))
        tasks = app.list_tasks()["tasks"]
        self.assertEqual(tasks[0]["cwd"], "${workspaceFolder}")
        self.assertEqual(tasks[0]["env"], {"FLOWCODE_MODE": "test"})

    def test_run_task_refuses_ambiguous_legacy_duplicate_labels(self):
        config = self.root / ".vscode" / "tasks.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"tasks": [
            {"label": "Duplicate", "type": "process", "command": "python", "args": ["-V"]},
            {"label": "Duplicate", "type": "process", "command": "python", "args": ["-VV"]},
        ]}), encoding="utf-8")
        result = app.run_task("Duplicate")
        self.assertIn("duplicated", result["error"])


if __name__ == '__main__':
    unittest.main(verbosity=2)
