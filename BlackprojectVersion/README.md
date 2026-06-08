Voici une proposition de **README.md ultra-détaillé, professionnel et prêt à l'emploi** pour votre dépôt GitHub. Il est structuré pour être à la fois informatif pour les développeurs qui veulent l'utiliser et clair pour les mainteneurs qui veulent comprendre son architecture.

---

# 🗺️ CodeCartographer

[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Zero Dependencies](https://img.shields.io/badge/Dépendances-Aucune-success.svg)](https://pypi.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Multi-langage](https://img.shields.io/badge/Langages-Python%20%7C%20C%23%20%7C%20VB%20%7C%20JS%20%7C%20PowerShell-orange.svg)](#)

**CodeCartographer** est un analyseur de code source multi-langage avancé, conçu pour parcourir récursivement une arborescence de fichiers et générer des rapports d'analyse enrichis. Il extrait la structure du code, calcule des métriques de qualité et cartographie les dépendances, le tout **sans aucune dépendance externe** (bibliothèque standard Python uniquement).

Il est particulièrement adapté pour l'audit de legacy code, la génération de documentation automatique, ou l'alimentation d'outils de gouvernance comme **Codeforge**.

---

## 🌟 Fonctionnalités Clés

- **🔍 Analyse Profonde** : Extraction des fonctions, classes, variables globales, imports, docstrings, paramètres et types de retour.
- **📊 Métriques de Qualité** : Calcul de la complexité cyclomatique (McCabe), comptage des lignes (code, commentaires, blanc) et hachage SHA-256 des fichiers.
- **🌐 Multi-langage** : Support natif de Python (via AST), et support heuristique robuste pour C#, VB.Net, VBScript, VBA, JavaScript et PowerShell (via Regex).
- **📦 Formats de Sortie Flexibles** : Export en JSON (global ou par fichier), CSV, Texte lisible, ou format spécifique `library.json` pour Codeforge.
- **🏷️ Catégorisation Intelligente** : Détection automatique de la "famille" fonctionnelle (IO, Réseau, Sécurité, Active Directory, Logging, etc.) et génération de tags sémantiques.
- **⚡ Zéro Dépendance** : Fonctionne uniquement avec la bibliothèque standard de Python. Aucun `pip install` requis.

---

## 🛠️ Installation

Aucune installation de paquet n'est nécessaire. Il suffit de cloner le dépôt ou de télécharger le script `analyzer.py`.

```bash
# Cloner le dépôt
git clone https://github.com/votre-utilisateur/codecartographer.git
cd codecartographer

# Vérifier que Python 3.9+ est installé
python --version
```

---

## 🚀 Démarrage Rapide

Analysez un répertoire et générez un rapport JSON global :

```bash
python analyzer.py /chemin/vers/mon/projet --output ./rapports --format json
```

Générer un rapport texte lisible pour une revue de code rapide :

```bash
python analyzer.py /chemin/vers/mon/projet --format text --verbose
```

---

## 📖 Référence de la Ligne de Commande (CLI)

| Argument | Alias | Description | Valeur par défaut |
| :--- | :---: | :--- | :--- |
| `root` | | **Obligatoire.** Chemin racine du dépôt à analyser. | - |
| `--output` | `-o` | Dossier de sortie pour les rapports générés. | `./codecartographer_output` |
| `--format` | `-f` | Format du rapport : `json`, `csv`, `text`, ou `library`. | `json` |
| `--no-separate` | | Désactive la création d'un fichier JSON individuel par fichier source (utile pour les gros projets en mode JSON). | `False` |
| `--verbose` | `-v` | Affiche la progression et chaque fichier traité dans la console. | `False` |

### Options spécifiques au format `--format library` (Codeforge)
Ces options permettent d'enrichir les métadonnées du fichier `library.json` généré :

| Argument | Description |
| :--- | :--- |
| `--lib-author` | Auteur à inscrire dans les métadonnées (ex: `"DSI — INGEN"`). |
| `--lib-description` | Description globale de la bibliothèque générée. |
| `--lib-scope` | Domaine d'application (ex: `"Active Directory · Réseau"`). |
| `--lib-validation` | Statut de validation : `EXTRACTED`, `REVIEW`, ou `APPROVED`. |

---

## 📂 Formats de Sortie Expliqués

### 1. JSON (`--format json`)
Génère un fichier `repo_index.json` contenant un index global du dépôt et la liste complète des rapports de fichiers. Si `--no-separate` n'est pas utilisé, il génère également un fichier `.json` dédié pour chaque fichier source analysé (nommé de manière sécurisée).

### 2. CSV (`--format csv`)
Génère un fichier `repo_index.csv` idéal pour l'import dans Excel ou des outils de BI. Chaque ligne représente une fonction, avec ses métriques (complexité, paramètres, dépendances externes, etc.).

### 3. Texte (`--format text`)
Génère un fichier `repo_index.txt` formaté pour une lecture humaine dans un terminal ou un éditeur de texte. Il inclut un résumé par fichier, la liste des fonctions, leur complexité, et un extrait tronqué du code source.

### 4. Bibliothèque Codeforge (`--format library`)
Génère un `library.json` structuré selon le schéma Codeforge. Il transforme les données brutes en :
- **Fonctions** : Avec ID unique, famille déduite (ex: "Security", "IO"), paramètres typés, valeur de retour, tags et exceptions levées (`throws`).
- **Variables** : Variables globales avec type deviné, portée et description.
- **Métadonnées** : Version, auteur, scope et statut de validation.

---

## 🧠 Architecture et Heuristiques

### Moteurs d'Analyse
1. **PythonParser** : Utilise le module natif `ast` de Python pour une analyse syntaxique parfaite et fiable à 100%.
2. **RegexParser** : Utilise des expressions régulières optimisées pour C#, VB, JS et PowerShell. Bien que moins parfait que l'AST pour les signatures génériques complexes, il couvre 95% des cas d'usage standards de manière très rapide.

### Déduction des "Familles" Fonctionnelles
L'outil scanne le nom de la fonction, sa docstring et les 300 premiers caractères de son code source pour lui attribuer une famille parmi :
> `Logging`, `IO`, `Network`, `Security`, `ActiveDirectory`, `Services`, `Updates`, `Inventory`, `Shares`, `Remote`, `GPO`, `LocalAccounts`, `Reporting`, `DevOps`, `MLOps`, `Control`, `Diagnostic`, ou `Misc`.

### Déduction des Types et Paramètres
Le script tente de parser les signatures de paramètres pour extraire :
- Le nom de la variable.
- Le type de données (via les type hints Python, les déclarations C#/PS, ou par heuristique sur le nom/valeur par défaut).
- Si le paramètre est obligatoire ou optionnel (détection de `=`, `Optional`, etc.).

---

## 📋 Exemple de Sortie (Extrait JSON)

```json
{
  "file_path": "src/auth.py",
  "language": "python",
  "lines_total": 120,
  "lines_code": 85,
  "sha256": "a1b2c3d4...",
  "functions": [
    {
      "name": "verify_token",
      "start_line": 45,
      "end_line": 60,
      "parameters": ["token: str", "secret_key: str"],
      "return_type": "bool",
      "cyclomatic_complexity": 3,
      "calls": ["hmac.compare_digest", "logging.warning"],
      "docstring": "Vérifie la validité d'un token JWT."
    }
  ],
  "external_dependencies": ["hmac", "logging"],
  "summary": "Fichier PYTHON · 1 fonction(s) · 0 classe(s) | Dépendances externes : hmac, logging | Fonction la plus complexe : verify_token (complexité=3)"
}
```

---

## ⚠️ Limitations Connues

- **Analyse Regex** : Pour C# et JavaScript, les signatures de fonctions très complexes (génériques imbriqués, destructuring avancé en JS) peuvent être partiellement mal interprétées. Le corps de la fonction et les métriques de complexité restent toutefois corrects.
- **VBA/VBScript** : L'analyse se limite aux motifs `Sub`/`Function` et `Dim`. Les variables de classe ou les propriétés complexes ne sont pas entièrement modélisées.
- **Encodage** : Le script suppose un encodage `utf-8`. Les fichiers avec des encodages exotiques (ex: Windows-1252 sans BOM) peuvent lever des erreurs de lecture (gérées gracieusement avec un message d'avertissement).

---

## 🤝 Contribuer

Les contributions sont les bienvenues ! Voici comment procéder :
1. Forkez le dépôt.
2. Créez votre branche de fonctionnalité (`git checkout -b feature/AmazingFeature`).
3. Committez vos changements (`git commit -m 'Add some AmazingFeature'`).
4. Poussez vers la branche (`git push origin feature/AmazingFeature`).
5. Ouvrez une Pull Request.

*N'hésitez pas à ajouter de nouveaux motifs Regex pour améliorer la prise en charge d'un langage spécifique !*

---

## 📜 Licence

Distribué sous la licence MIT. Voir le fichier `LICENSE` pour plus d'informations.

---

## 📬 Contact

Projet maintenu par [Votre Nom / Votre Organisation].  
Pour toute question ou suggestion, ouvrez une [Issue](https://github.com/votre-utilisateur/codecartographer/issues) sur GitHub.

--- 
