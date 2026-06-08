"""
CodeCartographer - Analyseur de code source multi-langage
=========================================================
Parcourt récursivement une arborescence et génère un rapport JSON enrichi
pour chaque fichier source trouvé, plus un index global du dépôt.

Langages supportés : Python, C#, VB.Net, VBScript, VBA, JavaScript, PowerShell

Usage :
    python analyzer.py <chemin_racine> [options]
    python analyzer.py /mon/repo --output ./rapport --format json
    python analyzer.py /mon/repo --output ./rapport --format csv
    python analyzer.py /mon/repo --output ./rapport --format text
"""

import os
import sys
import ast
import re
import json
import csv
import hashlib
import argparse
import textwrap
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------

@dataclass
class VariableInfo:
    name: str
    scope: str               # "global" | "local" | "parameter" | "class"
    assigned_in: list[str]   # noms des fonctions où elle est affectée
    used_in: list[str]       # noms des fonctions où elle est lue
    type_hint: Optional[str] = None
    default_value: Optional[str] = None


@dataclass
class FunctionInfo:
    name: str
    start_line: int
    end_line: int
    parameters: list[str]
    return_type: Optional[str]
    docstring: Optional[str]
    source_code: str
    local_variables: list[str]
    calls: list[str]          # fonctions appelées à l'intérieur
    decorators: list[str]
    is_method: bool
    parent_class: Optional[str]
    cyclomatic_complexity: int


@dataclass
class ClassInfo:
    name: str
    start_line: int
    end_line: int
    base_classes: list[str]
    methods: list[str]
    class_variables: list[str]
    docstring: Optional[str]


@dataclass
class ImportInfo:
    module: str
    alias: Optional[str]
    items: list[str]          # symboles importés (from X import a, b)
    is_external: bool         # True si pas dans le repo
    line: int


@dataclass
class FileReport:
    # --- Métadonnées fichier ---
    file_path: str
    file_name: str
    language: str
    size_bytes: int
    sha256: str
    lines_total: int
    lines_code: int           # sans commentaires ni blancs
    lines_comments: int
    lines_blank: int
    analyzed_at: str

    # --- Contenu analysé ---
    imports: list[ImportInfo]
    functions: list[FunctionInfo]
    classes: list[ClassInfo]
    global_variables: list[VariableInfo]

    # --- Métriques qualité ---
    avg_function_complexity: float
    max_function_complexity: int
    external_dependencies: list[str]   # modules tiers uniques
    internal_dependencies: list[str]   # fichiers du repo référencés

    # --- Résumé ---
    summary: str


@dataclass
class RepoIndex:
    root_path: str
    analyzed_at: str
    total_files: int
    files_by_language: dict
    all_external_dependencies: list[str]
    dependency_graph: dict      # fichier -> liste de fichiers qu'il importe
    file_reports: list[str]     # chemins vers les JSON individuels (mode séparé)
    reports: list[dict]         # rapports inline (mode groupé)


# ---------------------------------------------------------------------------
# Parseurs par langage
# ---------------------------------------------------------------------------

class PythonParser:
    """Analyse un fichier Python via l'AST natif."""

    STDLIB_MODULES = {
        "os", "sys", "re", "ast", "json", "csv", "math", "random", "time",
        "datetime", "pathlib", "collections", "itertools", "functools",
        "typing", "dataclasses", "abc", "io", "struct", "hashlib", "hmac",
        "base64", "urllib", "http", "socket", "threading", "multiprocessing",
        "subprocess", "shutil", "tempfile", "glob", "fnmatch", "logging",
        "unittest", "argparse", "configparser", "textwrap", "string",
        "copy", "pprint", "traceback", "warnings", "contextlib", "enum",
        "decimal", "fractions", "statistics", "array", "queue", "heapq",
        "bisect", "weakref", "gc", "inspect", "importlib", "pkgutil",
        "pickle", "shelve", "sqlite3", "xml", "html", "email", "smtplib",
        "ftplib", "zipfile", "tarfile", "gzip", "bz2", "lzma", "zlib",
        "signal", "mmap", "ctypes", "platform", "sysconfig", "site",
        "builtins", "keyword", "tokenize", "token", "dis", "code",
        "codeop", "compileall", "py_compile", "symtable", "types",
        "operator", "attrs", "__future__",
    }

    def parse(self, source: str, file_path: str) -> dict:
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return {"parse_error": str(e)}

        functions = []
        classes = []
        imports = []
        global_vars = []

        # Collecte des classes
        class_map = {}  # node -> ClassInfo
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in ast.walk(node) if isinstance(n, ast.FunctionDef)]
                cvars = []
                for n in node.body:
                    if isinstance(n, ast.Assign):
                        for t in n.targets:
                            if isinstance(t, ast.Name):
                                cvars.append(t.id)
                ci = ClassInfo(
                    name=node.name,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    base_classes=[self._name(b) for b in node.bases],
                    methods=methods,
                    class_variables=cvars,
                    docstring=ast.get_docstring(node),
                )
                classes.append(ci)
                class_map[node.name] = ci

        # Collecte des fonctions (top-level + méthodes)
        source_lines = source.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent_class = None
                for cls in classes:
                    if cls.start_line <= node.lineno <= cls.end_line:
                        parent_class = cls.name
                        break

                # Corps du code source
                start = node.lineno - 1
                end = getattr(node, "end_lineno", node.lineno)
                func_source = "\n".join(source_lines[start:end])

                # Paramètres
                params = [a.arg for a in node.args.args]
                if node.args.vararg:
                    params.append(f"*{node.args.vararg.arg}")
                if node.args.kwarg:
                    params.append(f"**{node.args.kwarg.arg}")

                # Variables locales
                local_vars = []
                for n in ast.walk(node):
                    if isinstance(n, ast.Assign):
                        for t in n.targets:
                            if isinstance(t, ast.Name) and t.id not in params:
                                if t.id not in local_vars:
                                    local_vars.append(t.id)

                # Appels internes
                calls = []
                for n in ast.walk(node):
                    if isinstance(n, ast.Call):
                        name = self._call_name(n)
                        if name and name not in calls:
                            calls.append(name)

                # Type de retour
                ret_type = None
                if node.returns:
                    ret_type = ast.unparse(node.returns)

                # Complexité cyclomatique (branches)
                complexity = 1
                for n in ast.walk(node):
                    if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                                      ast.With, ast.Assert, ast.comprehension)):
                        complexity += 1
                    elif isinstance(n, ast.BoolOp):
                        complexity += len(n.values) - 1

                fi = FunctionInfo(
                    name=node.name,
                    start_line=node.lineno,
                    end_line=end,
                    parameters=params,
                    return_type=ret_type,
                    docstring=ast.get_docstring(node),
                    source_code=func_source,
                    local_variables=local_vars,
                    calls=calls,
                    decorators=[ast.unparse(d) for d in node.decorator_list],
                    is_method=parent_class is not None,
                    parent_class=parent_class,
                    cyclomatic_complexity=complexity,
                )
                functions.append(fi)

        # Imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    imports.append(ImportInfo(
                        module=alias.name,
                        alias=alias.asname,
                        items=[],
                        is_external=root not in self.STDLIB_MODULES,
                        line=node.lineno,
                    ))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                root = module.split(".")[0]
                items = [alias.name for alias in node.names]
                imports.append(ImportInfo(
                    module=module,
                    alias=None,
                    items=items,
                    is_external=root not in self.STDLIB_MODULES and node.level == 0,
                    line=node.lineno,
                ))

        # Variables globales (assignées au niveau module)
        func_names = {f.name for f in functions}
        assigned_in_funcs: dict[str, list[str]] = {}
        used_in_funcs: dict[str, list[str]] = {}

        # Variables au niveau module
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        if t.id not in func_names:
                            vi = VariableInfo(
                                name=t.id,
                                scope="global",
                                assigned_in=["<module>"],
                                used_in=[],
                                type_hint=ast.unparse(node.annotation) if isinstance(node, ast.AnnAssign) and node.annotation else None,
                            )
                            global_vars.append(vi)

        return {
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "global_variables": global_vars,
        }

    def _name(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{self._name(node.value)}.{node.attr}"
        return "?"

    def _call_name(self, node):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return f"{self._name(func.value)}.{func.attr}"
        return None


class RegexParser:
    """Parseur générique basé sur des regex pour C#, VB, JS, PowerShell."""

    PATTERNS = {
        "csharp": {
            "function": re.compile(
                r'(?:(?:public|private|protected|internal|static|virtual|override|async|abstract|sealed)\s+)*'
                r'(?P<ret>[\w<>\[\]?,\s]+?)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*(?:\{|=>)',
                re.MULTILINE,
            ),
            "class": re.compile(
                r'(?:public|private|protected|internal|static|abstract|sealed)?\s*class\s+(?P<name>\w+)'
                r'(?:\s*:\s*(?P<bases>[\w,\s<>]+))?\s*\{',
                re.MULTILINE,
            ),
            "import": re.compile(r'^using\s+(?P<module>[\w.]+)\s*;', re.MULTILINE),
            "variable": re.compile(
                r'(?:var|int|string|bool|float|double|decimal|long|object|List|Dictionary|IEnumerable)\s+'
                r'(?P<name>\w+)\s*(?:=|;)',
                re.MULTILINE,
            ),
        },
        "vbnet": {
            "function": re.compile(
                r'(?:Public|Private|Protected|Friend|Static|Overrides|Overridable|MustOverride)?\s*'
                r'(?:Sub|Function)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)',
                re.MULTILINE | re.IGNORECASE,
            ),
            "class": re.compile(r'(?:Public|Private)?\s*Class\s+(?P<name>\w+)', re.MULTILINE | re.IGNORECASE),
            "import": re.compile(r'^Imports\s+(?P<module>[\w.]+)', re.MULTILINE | re.IGNORECASE),
            "variable": re.compile(r'Dim\s+(?P<name>\w+)\s+As\s+(?P<type>\w+)', re.MULTILINE | re.IGNORECASE),
        },
        "vbscript": {
            "function": re.compile(
                r'(?:Sub|Function)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)',
                re.MULTILINE | re.IGNORECASE,
            ),
            "class": re.compile(r'Class\s+(?P<name>\w+)', re.MULTILINE | re.IGNORECASE),
            "import": re.compile(r"(?:ExecuteGlobal|Execute)\s+['\"](?P<module>[^'\"]+)", re.MULTILINE | re.IGNORECASE),
            "variable": re.compile(r'Dim\s+(?P<name>\w+)', re.MULTILINE | re.IGNORECASE),
        },
        "javascript": {
            "function": re.compile(
                r'(?:export\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)\s*\((?P<params>[^)]*)\)|'
                r'(?:const|let|var)\s+(?P<name2>\w+)\s*=\s*(?:async\s+)?\((?P<params2>[^)]*)\)\s*=>|'
                r'(?:const|let|var)\s+(?P<name3>\w+)\s*=\s*(?:async\s+)?function\s*\(',
                re.MULTILINE,
            ),
            "class": re.compile(r'class\s+(?P<name>\w+)(?:\s+extends\s+(?P<bases>\w+))?', re.MULTILINE),
            "import": re.compile(
                r"(?:import\s+(?:[\w*{}\s,]+)\s+from\s+['\"](?P<module>[^'\"]+)['\"]|"
                r"require\s*\(['\"](?P<module2>[^'\"]+)['\"]\))",
                re.MULTILINE,
            ),
            "variable": re.compile(r'(?:const|let|var)\s+(?P<name>\w+)\s*=', re.MULTILINE),
        },
        "powershell": {
            "function": re.compile(
                r'function\s+(?P<name>[\w-]+)\s*\{|'
                r'function\s+(?P<name2>[\w-]+)\s*\((?P<params>[^)]*)\)',
                re.MULTILINE | re.IGNORECASE,
            ),
            "class": re.compile(r'class\s+(?P<name>\w+)', re.MULTILINE | re.IGNORECASE),
            "import": re.compile(
                r'(?:Import-Module|using\s+module|#Requires\s+-Module)\s+(?P<module>[\w.]+)',
                re.MULTILINE | re.IGNORECASE,
            ),
            "variable": re.compile(r'\$(?P<name>[A-Za-z_]\w+)\s*=', re.MULTILINE),
        },
    }

    # Extensions connues comme non-externes pour chaque langage
    INTERNAL_PREFIXES = {
        "javascript": {"./", "../", "/"},
        "csharp": set(),
        "vbnet": set(),
        "vbscript": set(),
        "powershell": set(),
    }

    def parse(self, source: str, language: str, file_path: str) -> dict:
        patterns = self.PATTERNS.get(language, {})
        lines = source.splitlines()

        functions = self._extract_functions(source, lines, patterns, language)
        classes = self._extract_classes(source, patterns)
        imports = self._extract_imports(source, patterns, language)
        global_vars = self._extract_variables(source, patterns, language, functions)

        return {
            "functions": functions,
            "classes": classes,
            "imports": imports,
            "global_variables": global_vars,
        }

    def _extract_functions(self, source, lines, patterns, language) -> list[FunctionInfo]:
        functions = []
        if "function" not in patterns:
            return functions

        seen = set()
        for m in patterns["function"].finditer(source):
            # Récupère le nom depuis plusieurs groupes nommés possibles
            name = m.group("name") if "name" in m.groupdict() and m.group("name") else None
            if not name:
                name = m.group("name2") if "name2" in m.groupdict() and m.group("name2") else None
            if not name:
                name = m.group("name3") if "name3" in m.groupdict() and m.group("name3") else None
            if not name or name in seen:
                continue
            seen.add(name)

            # Paramètres
            params_str = ""
            for grp in ("params", "params2"):
                if grp in m.groupdict() and m.group(grp) is not None:
                    params_str = m.group(grp)
                    break
            params = [p.strip() for p in params_str.split(",") if p.strip()] if params_str else []

            start_line = source[:m.start()].count("\n") + 1
            func_source, end_line = self._extract_body(source, lines, m.start(), language)

            # Complexité = nombre de branches
            branch_keywords = re.compile(
                r'\b(?:if|else|elif|for|while|catch|case|switch|foreach|until)\b',
                re.IGNORECASE,
            )
            complexity = 1 + len(branch_keywords.findall(func_source))

            # Appels de fonctions dans le corps
            calls = list(set(re.findall(r'\b([A-Za-z_]\w+)\s*\(', func_source)))

            # Variables locales
            local_pattern = patterns.get("variable")
            local_vars = []
            if local_pattern:
                for vm in local_pattern.finditer(func_source):
                    vname = vm.group("name")
                    if vname and vname not in params and vname not in local_vars:
                        local_vars.append(vname)

            fi = FunctionInfo(
                name=name,
                start_line=start_line,
                end_line=end_line,
                parameters=params,
                return_type=None,
                docstring=self._extract_docstring(func_source, language),
                source_code=func_source,
                local_variables=local_vars,
                calls=calls,
                decorators=[],
                is_method=False,
                parent_class=None,
                cyclomatic_complexity=complexity,
            )
            functions.append(fi)

        return functions

    def _extract_body(self, source, lines, start_pos, language) -> tuple[str, int]:
        """Extrait le corps d'une fonction par comptage d'accolades ou End Sub/Function."""
        start_line = source[:start_pos].count("\n")

        if language in ("vbnet", "vbscript"):
            end_re = re.compile(r'\b(?:End\s+(?:Sub|Function|Property))\b', re.IGNORECASE)
            remaining = source[start_pos:]
            m = end_re.search(remaining)
            if m:
                body = remaining[:m.end()]
                end_line = start_line + body.count("\n") + 1
                return body, end_line
            return source[start_pos:start_pos+500], start_line + 20

        # Accolades pour C#, JS, PS, Python-regex
        depth = 0
        pos = start_pos
        found_open = False
        while pos < len(source):
            c = source[pos]
            if c == "{":
                depth += 1
                found_open = True
            elif c == "}" and found_open:
                depth -= 1
                if depth == 0:
                    body = source[start_pos:pos + 1]
                    end_line = start_line + body.count("\n") + 1
                    return body, end_line
            pos += 1

        # Fallback : 30 lignes
        snippet = "\n".join(lines[start_line:start_line + 30])
        return snippet, start_line + 30

    def _extract_classes(self, source, patterns) -> list[ClassInfo]:
        classes = []
        if "class" not in patterns:
            return classes
        for m in patterns["class"].finditer(source):
            name = m.group("name")
            if not name:
                continue
            bases = []
            if "bases" in m.groupdict() and m.group("bases"):
                bases = [b.strip() for b in m.group("bases").split(",")]
            line = source[:m.start()].count("\n") + 1
            classes.append(ClassInfo(
                name=name,
                start_line=line,
                end_line=line,
                base_classes=bases,
                methods=[],
                class_variables=[],
                docstring=None,
            ))
        return classes

    def _extract_imports(self, source, patterns, language) -> list[ImportInfo]:
        imports = []
        if "import" not in patterns:
            return imports
        internal_prefixes = self.INTERNAL_PREFIXES.get(language, set())
        for m in patterns["import"].finditer(source):
            module = m.group("module") if m.group("module") else (
                m.group("module2") if "module2" in m.groupdict() and m.group("module2") else ""
            )
            if not module:
                continue
            is_external = not any(module.startswith(p) for p in internal_prefixes)
            line = source[:m.start()].count("\n") + 1
            imports.append(ImportInfo(
                module=module,
                alias=None,
                items=[],
                is_external=is_external,
                line=line,
            ))
        return imports

    def _extract_variables(self, source, patterns, language, functions) -> list[VariableInfo]:
        variables = []
        if "variable" not in patterns:
            return variables
        func_sources = {f.name: f.source_code for f in functions}
        seen = set()
        for m in patterns["variable"].finditer(source):
            name = m.group("name")
            if not name or name in seen:
                continue
            seen.add(name)
            type_hint = m.group("type") if "type" in m.groupdict() and m.group("type") else None
            # Détermine dans quelle(s) fonction(s) la variable apparaît
            assigned_in = []
            used_in = []
            for fname, fsource in func_sources.items():
                if re.search(rf'\b{re.escape(name)}\b\s*=', fsource):
                    assigned_in.append(fname)
                elif re.search(rf'\b{re.escape(name)}\b', fsource):
                    used_in.append(fname)
            variables.append(VariableInfo(
                name=name,
                scope="global" if not assigned_in else "local",
                assigned_in=assigned_in,
                used_in=used_in,
                type_hint=type_hint,
            ))
        return variables

    def _extract_docstring(self, func_source: str, language: str) -> Optional[str]:
        """Extrait un commentaire de documentation juste après la signature."""
        if language in ("vbnet", "vbscript"):
            m = re.search(r"'''(.+?)(?:'''|\n)", func_source, re.DOTALL)
            if m:
                return m.group(1).strip()
        elif language in ("csharp",):
            m = re.search(r"///\s*<summary>(.*?)</summary>", func_source, re.DOTALL)
            if m:
                return m.group(1).strip()
        elif language == "javascript":
            m = re.search(r'/\*\*(.*?)\*/', func_source, re.DOTALL)
            if m:
                return m.group(1).strip()
        elif language == "powershell":
            m = re.search(r'<#(.*?)#>', func_source, re.DOTALL)
            if m:
                return m.group(1).strip()
        return None


# ---------------------------------------------------------------------------
# Moteur principal
# ---------------------------------------------------------------------------

class CodeAnalyzer:
    """Orchestrateur : parcourt l'arborescence et produit les rapports."""

    EXTENSION_MAP = {
        ".py":   "python",
        ".cs":   "csharp",
        ".vb":   "vbnet",
        ".vbs":  "vbscript",
        ".vba":  "vbscript",   # même grammaire
        ".js":   "javascript",
        ".mjs":  "javascript",
        ".cjs":  "javascript",
        ".ps1":  "powershell",
        ".psm1": "powershell",
        ".psd1": "powershell",
    }

    def __init__(self, root: str, output_dir: str, fmt: str = "json",
                 separate_files: bool = True, verbose: bool = False,
                 metadata_overrides: dict | None = None):
        self.root = Path(root).resolve()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.fmt = fmt
        self.separate_files = separate_files
        self.verbose = verbose
        self.metadata_overrides = metadata_overrides or {}
        self.python_parser = PythonParser()
        self.regex_parser = RegexParser()

    def run(self) -> RepoIndex:
        files = self._discover_files()
        print(f"[CodeCartographer] {len(files)} fichier(s) source trouvé(s) dans {self.root}")

        reports = []
        for fp in files:
            report = self._analyze_file(fp)
            if report:
                reports.append(report)
                if self.separate_files and self.fmt == "json":
                    self._save_json_report(report)
                if self.verbose:
                    print(f"  ✓ {report.file_path}")

        index = self._build_index(reports)

        if self.fmt == "library":
            self._save_library(reports)
        elif self.fmt == "json":

            self._save_index_json(index, reports)
        elif self.fmt == "csv":
            self._save_csv(reports)
        elif self.fmt == "text":
            self._save_text(reports)

        return index

    # --- Découverte ---

    def _discover_files(self) -> list[Path]:
        found = []
        ignore_dirs = {".git", ".svn", "__pycache__", "node_modules", ".venv",
                       "venv", "env", "dist", "build", "bin", "obj", ".idea",
                       ".vscode", "packages", ".nuget"}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in ignore_dirs]
            for fname in filenames:
                ext = Path(fname).suffix.lower()
                if ext in self.EXTENSION_MAP:
                    found.append(Path(dirpath) / fname)
        return sorted(found)

    # --- Analyse unitaire ---

    def _analyze_file(self, fp: Path) -> Optional[FileReport]:
        ext = fp.suffix.lower()
        language = self.EXTENSION_MAP[ext]

        try:
            source = fp.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"  ✗ Lecture impossible : {fp} — {e}")
            return None

        lines = source.splitlines()
        lines_blank = sum(1 for l in lines if not l.strip())
        lines_comments = self._count_comments(lines, language)
        lines_code = len(lines) - lines_blank - lines_comments

        sha256 = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()

        # Parsing
        if language == "python":
            parsed = self.python_parser.parse(source, str(fp))
        else:
            parsed = self.regex_parser.parse(source, language, str(fp))

        if "parse_error" in parsed:
            print(f"  ⚠ Erreur de parsing : {fp} — {parsed['parse_error']}")

        functions: list[FunctionInfo] = parsed.get("functions", [])
        classes: list[ClassInfo] = parsed.get("classes", [])
        imports: list[ImportInfo] = parsed.get("imports", [])
        global_vars: list[VariableInfo] = parsed.get("global_variables", [])

        # Métriques
        complexities = [f.cyclomatic_complexity for f in functions] or [0]
        avg_complexity = round(sum(complexities) / len(complexities), 2)
        max_complexity = max(complexities)

        ext_deps = sorted({i.module for i in imports if i.is_external})
        int_deps = sorted({i.module for i in imports if not i.is_external})

        summary = self._build_summary(fp, language, functions, classes, imports, ext_deps)

        rel_path = str(fp.relative_to(self.root))

        return FileReport(
            file_path=rel_path,
            file_name=fp.name,
            language=language,
            size_bytes=fp.stat().st_size,
            sha256=sha256,
            lines_total=len(lines),
            lines_code=lines_code,
            lines_comments=lines_comments,
            lines_blank=lines_blank,
            analyzed_at=datetime.now().isoformat(),
            imports=imports,
            functions=functions,
            classes=classes,
            global_variables=global_vars,
            avg_function_complexity=avg_complexity,
            max_function_complexity=max_complexity,
            external_dependencies=ext_deps,
            internal_dependencies=int_deps,
            summary=summary,
        )

    def _count_comments(self, lines: list[str], language: str) -> int:
        count = 0
        in_block = False
        for line in lines:
            stripped = line.strip()
            if language == "python":
                if stripped.startswith("#"):
                    count += 1
                elif stripped.startswith('"""') or stripped.startswith("'''"):
                    count += 1
                    if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                        in_block = not in_block
            elif language in ("csharp", "javascript"):
                if stripped.startswith("//") or stripped.startswith("///"):
                    count += 1
                elif stripped.startswith("/*"):
                    in_block = True
                if in_block:
                    count += 1
                if "*/" in stripped:
                    in_block = False
            elif language in ("vbnet", "vbscript"):
                if stripped.startswith("'") or stripped.upper().startswith("REM "):
                    count += 1
            elif language == "powershell":
                if stripped.startswith("#"):
                    count += 1
                elif stripped.startswith("<#"):
                    in_block = True
                if in_block:
                    count += 1
                if "#>" in stripped:
                    in_block = False
        return count

    def _build_summary(self, fp, language, functions, classes, imports, ext_deps) -> str:
        lines = [
            f"Fichier {language.upper()} · {len(functions)} fonction(s) · {len(classes)} classe(s)",
        ]
        if ext_deps:
            lines.append(f"Dépendances externes : {', '.join(ext_deps[:5])}{'…' if len(ext_deps) > 5 else ''}")
        if functions:
            most_complex = max(functions, key=lambda f: f.cyclomatic_complexity)
            lines.append(f"Fonction la plus complexe : {most_complex.name} (complexité={most_complex.cyclomatic_complexity})")
        return " | ".join(lines)

    # --- Index global ---

    def _build_index(self, reports: list[FileReport]) -> RepoIndex:
        by_lang: dict[str, int] = {}
        all_ext: set[str] = set()
        dep_graph: dict[str, list[str]] = {}

        for r in reports:
            by_lang[r.language] = by_lang.get(r.language, 0) + 1
            all_ext.update(r.external_dependencies)
            dep_graph[r.file_path] = r.internal_dependencies

        return RepoIndex(
            root_path=str(self.root),
            analyzed_at=datetime.now().isoformat(),
            total_files=len(reports),
            files_by_language=by_lang,
            all_external_dependencies=sorted(all_ext),
            dependency_graph=dep_graph,
            file_reports=[r.file_path for r in reports],
            reports=[],
        )

    # --- Sérialiseurs ---

    def _report_to_dict(self, report: FileReport) -> dict:
        """Convertit un FileReport en dict JSON-sérialisable."""
        def convert(obj):
            if isinstance(obj, (FileReport, FunctionInfo, ClassInfo, VariableInfo, ImportInfo)):
                return {k: convert(v) for k, v in asdict(obj).items()}
            if isinstance(obj, list):
                return [convert(i) for i in obj]
            if isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            return obj
        return convert(report)

    def _save_json_report(self, report: FileReport):
        safe_name = report.file_path.replace(os.sep, "_").replace("/", "_")
        out = self.output_dir / f"{safe_name}.json"
        out.write_text(json.dumps(self._report_to_dict(report), ensure_ascii=False, indent=2), encoding="utf-8")


    def _save_library(self, reports: list[FileReport]):
        """Génère library.json au format Codeforge à partir des rapports analysés."""
        exporter = LibraryExporter(self.metadata_overrides)
        library = exporter.build_library(reports, str(self.root))
        out = self.output_dir / "library.json"
        out.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
        fn_count = len(library["functions"])
        var_count = len(library["variables"])
        print(f"[CodeCartographer] Library Codeforge → {out}")
        print(f"  {fn_count} fonction(s) · {var_count} variable(s) exportée(s)")

    def _save_index_json(self, index: RepoIndex, reports: list[FileReport]):
        # Un JSON global avec tout
        payload = {
            "index": {
                "root_path": index.root_path,
                "analyzed_at": index.analyzed_at,
                "total_files": index.total_files,
                "files_by_language": index.files_by_language,
                "all_external_dependencies": index.all_external_dependencies,
                "dependency_graph": index.dependency_graph,
            },
            "files": [self._report_to_dict(r) for r in reports],
        }
        out = self.output_dir / "repo_index.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[CodeCartographer] Index JSON → {out}")

    def _save_csv(self, reports: list[FileReport]):
        out = self.output_dir / "repo_index.csv"
        rows = []
        for r in reports:
            for f in r.functions:
                rows.append({
                    "file": r.file_path,
                    "language": r.language,
                    "lines_total": r.lines_total,
                    "lines_code": r.lines_code,
                    "function_name": f.name,
                    "start_line": f.start_line,
                    "end_line": f.end_line,
                    "parameters": "; ".join(f.parameters),
                    "return_type": f.return_type or "",
                    "cyclomatic_complexity": f.cyclomatic_complexity,
                    "calls": "; ".join(f.calls),
                    "local_variables": "; ".join(f.local_variables),
                    "docstring": (f.docstring or "").replace("\n", " "),
                    "external_deps": "; ".join(r.external_dependencies),
                    "summary": r.summary,
                })
            if not r.functions:
                rows.append({
                    "file": r.file_path,
                    "language": r.language,
                    "lines_total": r.lines_total,
                    "lines_code": r.lines_code,
                    "function_name": "(aucune)",
                    "start_line": "", "end_line": "",
                    "parameters": "", "return_type": "",
                    "cyclomatic_complexity": 0,
                    "calls": "", "local_variables": "", "docstring": "",
                    "external_deps": "; ".join(r.external_dependencies),
                    "summary": r.summary,
                })
        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        print(f"[CodeCartographer] Rapport CSV → {out}")

    def _save_text(self, reports: list[FileReport]):
        out = self.output_dir / "repo_index.txt"
        sep = "=" * 80
        lines = [
            "CODECARTOGRAPHER — Rapport d'analyse de dépôt",
            f"Généré le : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Fichiers analysés : {len(reports)}",
            sep,
        ]
        for r in reports:
            lines += [
                "",
                sep,
                f"FICHIER : {r.file_path}",
                f"  Langage      : {r.language}",
                f"  Taille       : {r.size_bytes} octets | {r.lines_total} lignes "
                f"({r.lines_code} code, {r.lines_comments} commentaires, {r.lines_blank} blancs)",
                f"  SHA-256      : {r.sha256}",
                f"  Résumé       : {r.summary}",
            ]
            if r.external_dependencies:
                lines.append(f"  Dépendances  : {', '.join(r.external_dependencies)}")
            if r.classes:
                lines.append(f"  Classes      : {', '.join(c.name for c in r.classes)}")
            lines.append(f"  Complexité   : moyenne={r.avg_function_complexity} max={r.max_function_complexity}")
            lines.append("")
            lines.append("  FONCTIONS :")
            for f in r.functions:
                lines += [
                    f"    ┌─ {f.name}({'cls: ' + f.parent_class if f.parent_class else ''})",
                    f"    │  Lignes       : {f.start_line}–{f.end_line}",
                    f"    │  Paramètres   : {', '.join(f.parameters) or '(aucun)'}",
                    f"    │  Retour       : {f.return_type or 'N/A'}",
                    f"    │  Complexité   : {f.cyclomatic_complexity}",
                    f"    │  Appels       : {', '.join(f.calls[:10]) or '(aucun)'}",
                    f"    │  Vars locales : {', '.join(f.local_variables[:10]) or '(aucune)'}",
                ]
                if f.docstring:
                    doc_wrapped = textwrap.fill(f.docstring, width=60, subsequent_indent="    │               ")
                    lines.append(f"    │  Docstring    : {doc_wrapped}")
                lines.append("    │")
                lines.append("    │  Code source :")
                for code_line in f.source_code.splitlines()[:40]:
                    lines.append(f"    │    {code_line}")
                if f.source_code.count("\n") > 40:
                    lines.append("    │    [... tronqué ...]")
                lines.append("    └─")
            if r.global_variables:
                lines.append("")
                lines.append("  VARIABLES GLOBALES :")
                for v in r.global_variables:
                    lines.append(
                        f"    • {v.name} ({v.scope})"
                        + (f" : {v.type_hint}" if v.type_hint else "")
                        + (f" — assignée dans {v.assigned_in}" if v.assigned_in else "")
                    )
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"[CodeCartographer] Rapport texte → {out}")



# ---------------------------------------------------------------------------
# Exporteur Codeforge Library
# ---------------------------------------------------------------------------

class LibraryExporter:
    """
    Convertit les résultats de CodeCartographer au format library.json de Codeforge.

    Schéma cible :
    {
        "metadata": { version, description, created, author, scope, languages, ... },
        "functions": [ { id, type, name, language, famille, description,
                         parameters, returns, source, tags, throws } ],
        "variables": [ { id, type, name, language, famille, datatype,
                         default_value, description, scope, tags } ]
    }
    """

    # Mapping langage interne CodeCartographer -> libellé Codeforge
    LANGUAGE_LABELS = {
        "python":     "Python",
        "csharp":     "CSharp",
        "vbnet":      "VBNet",
        "vbscript":   "VBScript",
        "javascript": "JavaScript",
        "powershell": "PowerShell",
    }

    # Heuristique famille : mots-clés dans nom / docstring / source -> famille Codeforge
    FAMILY_KEYWORDS: list[tuple[str, list[str]]] = [
        ("Logging",        ["log", "trace", "write-log", "write_log", "journal", "audit",
                            "alert", "mail", "send-alert", "sendalert"]),
        ("IO",             ["file", "read", "write", "path", "stream", "csv", "xml",
                            "json", "parse", "load", "save", "export", "import"]),
        ("Network",        ["network", "net", "ip", "dns", "ping", "socket", "http",
                            "rest", "api", "request", "invoke-web", "invoke-rest",
                            "webrequest", "webclient"]),
        ("Security",       ["cred", "password", "secret", "encrypt", "decrypt", "acl",
                            "permission", "hash", "sign", "cert", "tls", "ssl", "auth",
                            "token", "secur"]),
        ("ActiveDirectory",["aduser", "adgroup", "adcomputer", "ldap", "samaccount",
                            "get-ad", "new-ad", "set-ad", "remove-ad", "ou ", "gpo"]),
        ("Services",       ["service", "daemon", "process", "task", "job", "schedule",
                            "worker", "svc", "start-service", "stop-service"]),
        ("Updates",        ["update", "patch", "wsus", "hotfix", "upgrade",
                            "install-windows", "windowsupdate"]),
        ("Inventory",      ["inventory", "asset", "hardware", "software", "wmi", "cim",
                            "computer", "installed", "get-wmi", "get-cim"]),
        ("Shares",         ["share", "unc", "smb", "drive", "mount", "folder",
                            "new-smbshare", "get-smbshare"]),
        ("Remote",         ["remote", "ssh", "winrm", "invoke-command", "psremote",
                            "rdp", "telnet", "enter-pssession"]),
        ("GPO",            ["grouppolicy", "registry", "hkcu", "hklm", "reg",
                            "set-gpo", "get-gpo", "gpupdate"]),
        ("LocalAccounts",  ["localuser", "localgroup", "builtins",
                            "new-localuser", "get-localuser", "set-localuser"]),
        ("Reporting",      ["report", "summary", "html", "pdf", "table", "chart",
                            "dashboard", "metric", "stats", "statistics"]),
        ("DevOps",         ["cicd", "pipeline", "deploy", "docker", "container",
                            "helm", "git", "build", "release", "artifact"]),
        ("MLOps",          ["ml", "model", "train", "dataset", "cluster", "gpu",
                            "inference", "embedding", "vector"]),
        ("Control",        ["retry", "loop", "wait", "sleep", "timeout", "cancel",
                            "throttle", "backoff"]),
        ("Diagnostic",     ["test", "check", "diagnos", "health", "monitor", "watch",
                            "verify", "validate", "assert", "probe"]),
    ]

    def __init__(self, metadata_overrides: dict | None = None):
        self.metadata_overrides = metadata_overrides or {}
        self._fn_counter = 0
        self._var_counter = 0

    # ------------------------------------------------------------------
    # Point d'entrée principal
    # ------------------------------------------------------------------

    def build_library(self, reports: list, root_path: str) -> dict:
        """
        Construit le dict library.json complet à partir de la liste de FileReport.

        :param reports:    liste de FileReport produits par CodeAnalyzer
        :param root_path:  chemin racine du dépôt analysé
        :return:           dict au format library.json Codeforge
        """
        self._fn_counter = 0
        self._var_counter = 0

        all_functions = []
        all_variables = []
        languages_seen: set[str] = set()

        for report in reports:
            lang_label = self.LANGUAGE_LABELS.get(report.language, report.language.capitalize())
            languages_seen.add(lang_label)

            for func in report.functions:
                entry = self._convert_function(func, report, lang_label)
                all_functions.append(entry)

            for var in report.global_variables:
                entry = self._convert_variable(var, report, lang_label)
                all_variables.append(entry)

        metadata = self._build_metadata(root_path, sorted(languages_seen), len(all_functions))

        return {
            "metadata":  metadata,
            "functions": all_functions,
            "variables": all_variables,
        }

    # ------------------------------------------------------------------
    # Conversion d'une fonction
    # ------------------------------------------------------------------

    def _convert_function(self, func, report, lang_label: str) -> dict:
        self._fn_counter += 1
        fn_id = f"fn_{self._fn_counter:03d}"

        famille    = self._infer_family(func.name, func.docstring or "", func.source_code)
        description = self._build_description(func, report)
        parameters  = self._convert_parameters(func, report.language)
        returns     = self._convert_returns(func, report.language)
        tags        = self._infer_tags(func, report)
        throws      = self._extract_throws(func.source_code, report.language)

        return {
            "id":          fn_id,
            "type":        "function",
            "name":        func.name,
            "language":    lang_label,
            "famille":     famille,
            "description": description,
            "parameters":  parameters,
            "returns":     returns,
            "source":      func.source_code,
            "tags":        tags,
            "throws":      throws,
            # Champs étendus (métadonnées d'origine, utiles pour le retour au repo)
            "origin_file":           report.file_path,
            "start_line":            func.start_line,
            "end_line":              func.end_line,
            "is_method":             func.is_method,
            "parent_class":          func.parent_class,
            "cyclomatic_complexity": func.cyclomatic_complexity,
            "calls":                 func.calls,
            "decorators":            func.decorators,
        }

    # ------------------------------------------------------------------
    # Conversion d'une variable globale
    # ------------------------------------------------------------------

    def _convert_variable(self, var, report, lang_label: str) -> dict:
        self._var_counter += 1
        var_id = f"var_{self._var_counter:03d}"

        famille  = self._infer_family(var.name, "", "")
        datatype = var.type_hint or self._guess_type(var.name, var.default_value)
        tags     = self._infer_var_tags(var, report)

        return {
            "id":            var_id,
            "type":          "variable",
            "name":          var.name,
            "language":      lang_label,
            "famille":       famille,
            "datatype":      datatype or "any",
            "default_value": var.default_value or "",
            "description":   (
                f"Variable globale '{var.name}' ({var.scope})"
                + (f" — type : {datatype}" if datatype else "")
            ),
            "scope":         var.scope,
            "tags":          tags,
            # Champs étendus
            "origin_file":   report.file_path,
            "assigned_in":   var.assigned_in,
            "used_in":       var.used_in,
        }

    # ------------------------------------------------------------------
    # Heuristiques — Famille
    # ------------------------------------------------------------------

    def _infer_family(self, name: str, docstring: str, source: str) -> str:
        """Déduit la famille fonctionnelle à partir du nom, de la docstring et du code."""
        haystack = (name + " " + docstring + " " + source[:300]).lower()
        for family, keywords in self.FAMILY_KEYWORDS:
            if any(kw in haystack for kw in keywords):
                return family
        return "Misc"

    # ------------------------------------------------------------------
    # Heuristiques — Description
    # ------------------------------------------------------------------

    def _build_description(self, func, report) -> str:
        """Construit une description exploitable : docstring si dispo, sinon synthèse."""
        if func.docstring:
            desc = re.sub(r"[\r\n]+", " ", func.docstring).strip()
            desc = re.sub(r"\s+", " ", desc)
            return desc[:300]
        # Synthèse automatique
        parts = []
        if func.is_method and func.parent_class:
            parts.append(f"Méthode de {func.parent_class}.")
        n_params = len(func.parameters)
        if n_params:
            listed = ", ".join(func.parameters[:4])
            suffix = "…" if n_params > 4 else ""
            parts.append(f"Accepte {n_params} paramètre(s) : {listed}{suffix}.")
        if func.return_type:
            parts.append(f"Retourne {func.return_type}.")
        if func.calls:
            suffix = "…" if len(func.calls) > 3 else ""
            parts.append(f"Appelle : {', '.join(func.calls[:3])}{suffix}.")
        parts.append(f"Complexité cyclomatique : {func.cyclomatic_complexity}.")
        return " ".join(parts) or f"Fonction {func.name} ({report.language})."

    # ------------------------------------------------------------------
    # Heuristiques — Paramètres
    # ------------------------------------------------------------------

    def _convert_parameters(self, func, language: str) -> list[dict]:
        return [self._parse_param(p, language) for p in func.parameters]

    def _parse_param(self, raw: str, language: str) -> dict:
        """Extrait nom, type, valeur par défaut d'une chaîne de paramètre."""
        raw = raw.strip()
        name = raw
        datatype = "any"
        default_value = None
        required = True

        if language == "python":
            if "=" in raw:
                parts = raw.split("=", 1)
                raw_left = parts[0].strip()
                default_value = parts[1].strip()
                required = False
            else:
                raw_left = raw
            if ":" in raw_left:
                n, t = raw_left.split(":", 1)
                name = n.strip().lstrip("*")
                datatype = t.strip()
            else:
                name = raw_left.strip().lstrip("*")

        elif language == "csharp":
            if "=" in raw:
                parts = raw.split("=", 1)
                raw = parts[0].strip()
                default_value = parts[1].strip()
                required = False
            tokens = raw.split()
            if len(tokens) >= 2:
                datatype = tokens[-2] if len(tokens) > 2 else tokens[0]
                name = tokens[-1].strip("[]")
            else:
                name = raw

        elif language in ("vbnet", "vbscript"):
            if re.search(r"\bOptional\b", raw, re.IGNORECASE):
                required = False
            m = re.search(r"As\s+(\w+)", raw, re.IGNORECASE)
            if m:
                datatype = m.group(1)
            m2 = re.search(r"=\s*(.+)$", raw)
            if m2:
                default_value = m2.group(1).strip()
                required = False
            m3 = re.search(r"(\w+)\s+As", raw, re.IGNORECASE)
            if m3:
                name = m3.group(1)

        elif language == "powershell":
            if "=" in raw:
                parts = raw.split("=", 1)
                raw = parts[0].strip()
                default_value = parts[1].strip()
                required = False
            m = re.match(r"\[([^\]]+)\]\s*\$(\w+)", raw)
            if m:
                datatype = m.group(1)
                name = m.group(2)
            else:
                name = raw.lstrip("$").strip()

        elif language == "javascript":
            if "=" in raw:
                parts = raw.split("=", 1)
                name = parts[0].strip()
                default_value = parts[1].strip()
                required = False
            else:
                name = raw

        entry: dict = {
            "name":        name or raw,
            "datatype":    datatype,
            "required":    required,
            "description": f"Paramètre {name or raw}",
        }
        if default_value is not None:
            entry["default"] = default_value
        return entry

    # ------------------------------------------------------------------
    # Heuristiques — Retour
    # ------------------------------------------------------------------

    def _convert_returns(self, func, language: str) -> dict:
        rt = func.return_type
        if rt:
            return {"datatype": rt, "description": f"Valeur retournée ({rt})"}
        src = func.source_code.lower()
        if "return true" in src or "return false" in src:
            return {"datatype": "bool", "description": "Succès ou échec de l'opération"}
        if "$null" in src and "return" in src:
            return {"datatype": "void", "description": ""}
        if "return none" in src:
            return {"datatype": "void", "description": ""}
        if re.search(r"return\s+\[", src):
            return {"datatype": "list", "description": "Liste de résultats"}
        if re.search(r"return\s+\{", src):
            return {"datatype": "dict", "description": "Dictionnaire de résultats"}
        if "return" in src:
            return {"datatype": "any", "description": "Valeur retournée"}
        return {"datatype": "void", "description": ""}

    # ------------------------------------------------------------------
    # Heuristiques — Throws
    # ------------------------------------------------------------------

    def _extract_throws(self, source: str, language: str) -> list[str]:
        throws: set[str] = set()
        if language == "csharp":
            for m in re.finditer(r"throw\s+new\s+(\w+(?:Exception|Error))", source):
                throws.add(m.group(1))
        elif language == "powershell":
            for m in re.finditer(r'throw\s+"([^"]+)"', source, re.IGNORECASE):
                throws.add(m.group(1)[:60])
            if re.search(r"\bthrow\b", source, re.IGNORECASE) and not throws:
                throws.add("RuntimeException")
        elif language == "python":
            for m in re.finditer(r"raise\s+(\w+(?:Error|Exception|Warning)?)\s*[(\n]", source):
                throws.add(m.group(1))
        elif language == "javascript":
            for m in re.finditer(r"throw\s+new\s+(\w+(?:Error|Exception)?)", source):
                throws.add(m.group(1))
        return sorted(throws)

    # ------------------------------------------------------------------
    # Heuristiques — Tags
    # ------------------------------------------------------------------

    def _infer_tags(self, func, report) -> list[str]:
        tags: set[str] = set()
        haystack = (func.name + " " + (func.docstring or "")).lower()

        # Tags issus des dépendances du fichier
        for dep in report.external_dependencies[:5]:
            tags.add(dep.lower().split(".")[0])

        # Tags issus des appels notables
        notable_calls = {
            "requests", "urllib", "httpx", "aiohttp",
            "subprocess", "pathlib", "shutil",
            "logging", "json", "csv", "re",
            "asyncio", "threading", "multiprocessing",
        }
        for call in func.calls:
            base = call.split(".")[0].lower()
            if base in notable_calls:
                tags.add(base)

        # Tags sémantiques
        semantic = {
            "async":      "async" in haystack,
            "io":         any(k in haystack for k in ["file", "read", "write", "path", "stream"]),
            "network":    any(k in haystack for k in ["http", "url", "api", "request", "web"]),
            "security":   any(k in haystack for k in ["cred", "auth", "encrypt", "decrypt", "hash"]),
            "logging":    any(k in haystack for k in ["log", "trace", "audit"]),
            "validation": any(k in haystack for k in ["valid", "check", "verify", "test"]),
        }
        for tag, cond in semantic.items():
            if cond:
                tags.add(tag)

        tags.add(report.language)
        return sorted(tags)[:8]

    def _infer_var_tags(self, var, report) -> list[str]:
        tags: set[str] = {report.language}
        if var.scope == "global":
            tags.add("global")
        n = var.name.lower()
        if any(k in n for k in ["path", "file", "dir"]):
            tags.add("filesystem")
        if any(k in n for k in ["url", "host", "port", "server"]):
            tags.add("network")
        if any(k in n for k in ["password", "key", "secret", "token", "cred"]):
            tags.add("security")
        return sorted(tags)

    def _guess_type(self, name: str, default_value: str | None) -> str | None:
        if default_value:
            if default_value.isdigit():
                return "int"
            if re.match(r"^\d+\.\d+$", default_value):
                return "float"
            if default_value.lower() in ("true", "false", "$true", "$false"):
                return "bool"
            if default_value.startswith(("'", '"', "@'", '@"')):
                return "string"
        n = name.lower()
        if any(k in n for k in ["count", "max", "min", "num", "index", "size", "len", "port"]):
            return "int"
        if any(k in n for k in ["flag", "enable", "is_", "has_", "use_", "verbose"]):
            return "bool"
        if any(k in n for k in ["path", "name", "url", "host", "msg", "text", "str"]):
            return "string"
        return None

    # ------------------------------------------------------------------
    # Métadonnées
    # ------------------------------------------------------------------

    def _build_metadata(self, root_path: str, languages: list[str], fn_count: int) -> dict:
        repo_name = Path(root_path).name
        base = {
            "version":           "1.0",
            "description":       f"Bibliothèque extraite de '{repo_name}' par CodeCartographer",
            "created":           datetime.now().strftime("%Y-%m-%d"),
            "author":            "CodeCartographer / auto-generated",
            "scope":             f"Dépôt : {repo_name} · {fn_count} fonction(s) extraite(s)",
            "validation_status": "EXTRACTED",
            "languages":         languages,
            "source_repo":       str(root_path),
        }
        base.update(self.metadata_overrides)
        return base

# ---------------------------------------------------------------------------
# Point d'entrée CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="CodeCartographer — Analyse multi-langage d'un dépôt de code source",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Exemples :
              python analyzer.py /mon/repo
              python analyzer.py /mon/repo --output ./rapport --format csv
              python analyzer.py /mon/repo --format text --no-separate
              python analyzer.py . --verbose
              python analyzer.py /mon/repo --format library --output ./lib
              python analyzer.py /mon/repo --format library --lib-author "DSI — INGEN" --lib-validation APPROVED
        """),
    )
    parser.add_argument("root", help="Chemin racine du dépôt à analyser")
    parser.add_argument(
        "--output", "-o", default="./codecartographer_output",
        help="Dossier de sortie (défaut: ./codecartographer_output)",
    )
    parser.add_argument(
        "--format", "-f", choices=["json", "csv", "text", "library"], default="json",
        help="Format du rapport (défaut: json)",
    )
    parser.add_argument(
        "--no-separate", action="store_true",
        help="Ne pas créer un fichier JSON par source (JSON global uniquement)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Affiche chaque fichier traité",
    )
    # Options spécifiques au format --library
    parser.add_argument(
        "--lib-author",
        help="Auteur à inscrire dans les métadonnées library.json (ex: 'DSI — INGEN')",
    )
    parser.add_argument(
        "--lib-description",
        help="Description de la bibliothèque générée",
    )
    parser.add_argument(
        "--lib-scope",
        help="Scope / domaine de la bibliothèque (ex: 'Active Directory · Réseau')",
    )
    parser.add_argument(
        "--lib-validation",
        default="EXTRACTED",
        help="Statut de validation : EXTRACTED, REVIEW, APPROVED (défaut: EXTRACTED)",
    )

    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print(f"Erreur : '{args.root}' n'est pas un répertoire valide.")
        sys.exit(1)

    # Construction des overrides de métadonnées pour le format library
    metadata_overrides: dict = {}
    if args.lib_author:
        metadata_overrides["author"] = args.lib_author
    if args.lib_description:
        metadata_overrides["description"] = args.lib_description
    if args.lib_scope:
        metadata_overrides["scope"] = args.lib_scope
    if args.lib_validation:
        metadata_overrides["validation_status"] = args.lib_validation

    analyzer = CodeAnalyzer(
        root=args.root,
        output_dir=args.output,
        fmt=args.format,
        separate_files=not args.no_separate,
        verbose=args.verbose,
        metadata_overrides=metadata_overrides,
    )
    index = analyzer.run()

    print(f"\n[CodeCartographer] Analyse terminée.")
    print(f"  Fichiers analysés : {index.total_files}")
    for lang, count in sorted(index.files_by_language.items()):
        print(f"    {lang:15s} : {count}")
    if index.all_external_dependencies:
        print(f"  Dépendances externes détectées ({len(index.all_external_dependencies)}) :")
        for dep in index.all_external_dependencies[:15]:
            print(f"    • {dep}")
        if len(index.all_external_dependencies) > 15:
            print(f"    … et {len(index.all_external_dependencies) - 15} autres")
    print(f"  Rapports dans : {analyzer.output_dir.resolve()}")


if __name__ == "__main__":
    main()
