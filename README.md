# CodeCartographer 🗺️

Analyseur statique de code source multi-langage. Parcourt récursivement un dépôt et génère un rapport structuré documentant chaque fichier source : fonctions, variables, imports, métriques de complexité et dépendances.

## Langages supportés

| Extension | Langage | Méthode d'analyse |
|-----------|---------|-------------------|
| `.py` | Python | AST natif (`ast.parse`) — précision maximale |
| `.cs` | C# | Regex + heuristiques |
| `.vb` | VB.Net | Regex + heuristiques |
| `.vbs` `.vba` | VBScript / VBA | Regex + heuristiques |
| `.js` `.mjs` `.cjs` | JavaScript | Regex (ESM, CJS, JSDoc) |
| `.ps1` `.psm1` `.psd1` | PowerShell | Regex + tokens |

## Installation

Aucune dépendance externe requise — uniquement la bibliothèque standard Python 3.10+.

```bash
python analyzer.py --help
```

## Utilisation

```bash
# Rapport JSON (défaut) — un fichier par source + index global
python analyzer.py /chemin/vers/repo

# Spécifier le dossier de sortie
python analyzer.py /chemin/vers/repo --output ./rapport

# Format CSV (une ligne par fonction)
python analyzer.py /chemin/vers/repo --format csv

# Format texte lisible (rapport complet indenté)
python analyzer.py /chemin/vers/repo --format text

# JSON global uniquement (sans fichiers individuels)
python analyzer.py /chemin/vers/repo --no-separate

# Mode verbeux
python analyzer.py /chemin/vers/repo --verbose
```

## Sorties produites

### Format JSON (recommandé)

Deux fichiers sont générés :

**`repo_index.json`** — Index global du dépôt :
```json
{
  "index": {
    "root_path": "/mon/repo",
    "analyzed_at": "2026-05-21T14:30:00",
    "total_files": 42,
    "files_by_language": { "python": 18, "csharp": 12, "javascript": 8, "powershell": 4 },
    "all_external_dependencies": ["requests", "numpy", "Newtonsoft.Json", "axios"],
    "dependency_graph": {
      "services/api.py": ["utils.helpers", "models.base"],
      "main.py": ["services.api"]
    }
  },
  "files": [ /* tableau de FileReport */ ]
}
```

**`<nom_fichier>.json`** — Rapport individuel pour chaque source :
```json
{
  "file_path": "services/api.py",
  "file_name": "api.py",
  "language": "python",
  "size_bytes": 3421,
  "sha256": "a3f2...",
  "lines_total": 87,
  "lines_code": 64,
  "lines_comments": 12,
  "lines_blank": 11,
  "analyzed_at": "2026-05-21T14:30:00",
  "imports": [
    { "module": "requests", "alias": null, "items": [], "is_external": true, "line": 3 }
  ],
  "functions": [
    {
      "name": "fetch_data",
      "start_line": 15,
      "end_line": 28,
      "parameters": ["url", "retries"],
      "return_type": "list",
      "docstring": "Récupère les données depuis l'API avec retry automatique.",
      "source_code": "def fetch_data(url: str, retries: int = 3) -> list:\n    ...",
      "local_variables": ["response", "attempt"],
      "calls": ["requests.get", "response.raise_for_status"],
      "decorators": [],
      "is_method": false,
      "parent_class": null,
      "cyclomatic_complexity": 4
    }
  ],
  "classes": [
    {
      "name": "ApiClient",
      "start_line": 31,
      "end_line": 65,
      "base_classes": ["EventEmitter"],
      "methods": ["__init__", "get", "post"],
      "class_variables": ["DEFAULT_TIMEOUT"],
      "docstring": "Client HTTP avec gestion d'événements."
    }
  ],
  "global_variables": [
    { "name": "BASE_URL", "scope": "global", "assigned_in": ["<module>"], "used_in": ["fetch_data"], "type_hint": null }
  ],
  "avg_function_complexity": 2.8,
  "max_function_complexity": 6,
  "external_dependencies": ["requests", "numpy"],
  "internal_dependencies": ["utils.helpers"],
  "summary": "Fichier PYTHON · 5 fonction(s) · 1 classe(s) | Dépendances : requests, numpy | Fonction la plus complexe : fetch_data (complexité=4)"
}
```

### Format CSV

Une ligne par fonction — idéal pour Excel, pandas, filtres rapides :

```
file,language,lines_total,function_name,parameters,cyclomatic_complexity,calls,external_deps,...
main.py,python,62,fetch_data,url; retries,4,requests.get; raise_for_status,requests; numpy,...
```

### Format texte

Rapport lisible en terminal ou éditeur, avec indentation arborescente et code source des fonctions.

## Ce qui est capturé

| Élément | Python | C# | VB | JS | PS |
|---------|--------|----|----|----|----|
| Fonctions / méthodes | ✅ AST | ✅ | ✅ | ✅ | ✅ |
| Signature complète | ✅ | ✅ | ✅ | ✅ | ✅ |
| Code source de chaque fonction | ✅ | ✅ | ✅ | ✅ | ✅ |
| Type de retour | ✅ | — | — | — | — |
| Docstrings / commentaires doc | ✅ | ✅ `///` | ✅ `'''` | ✅ JSDoc | ✅ `<# #>` |
| Variables locales | ✅ | ✅ | ✅ | ✅ | ✅ |
| Variables globales | ✅ | ✅ | ✅ | ✅ | ✅ |
| Appels de fonctions internes | ✅ | ✅ | ✅ | ✅ | ✅ |
| Classes + héritage | ✅ | ✅ | ✅ | ✅ | ✅ |
| Imports / dépendances | ✅ | ✅ | ✅ | ✅ | ✅ |
| Distinction interne/externe | ✅ | — | — | ✅ | — |
| Complexité cyclomatique | ✅ | ✅ | ✅ | ✅ | ✅ |
| Métriques lignes | ✅ | ✅ | ✅ | ✅ | ✅ |
| SHA-256 du fichier | ✅ | ✅ | ✅ | ✅ | ✅ |
| Graphe de dépendances inter-fichiers | ✅ | — | — | ✅ | — |

## Dossiers ignorés automatiquement

`.git`, `__pycache__`, `node_modules`, `.venv`, `venv`, `dist`, `build`, `bin`, `obj`, `.idea`, `.vscode`, `packages`, `.nuget`

## Idées d'extensions

- **Intégration LLM** : passer le `summary` de chaque fichier à un LLM pour générer une description en langage naturel du rôle du module dans l'architecture globale
- **Graphe de dépendances** : exporter `dependency_graph` vers Graphviz ou D3.js pour une visualisation interactive
- **Détection de code mort** : fonctions définies mais jamais référencées dans `calls` d'autres fichiers
- **Suivi temporel** : comparer deux analyses (avant/après refactoring) via les SHA-256
- **Filtre par seuil de complexité** : `--min-complexity 5` pour ne rapporter que les fonctions complexes
- **Support TypeScript** : extension mineure du parseur JavaScript
- **Export HTML** : rapport navigable avec recherche full-text

## Structure du projet

```
analyzer.py          ← Script principal (un seul fichier, aucune dépendance)
README.md            ← Cette documentation
```
