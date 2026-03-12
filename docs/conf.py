"""Sphinx configuration for research-index documentation."""

project = "research-index"
author = "Michael Roynard"
release = "0.1.0"

extensions = [
    "myst_parser",
    "sphinxcontrib.mermaid",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "tasklist",
]

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "superpowers",
    "plans",
    "insights",
]

html_theme = "sphinx_rtd_theme"
html_static_path = []

# MyST settings
myst_heading_anchors = 3

# Mermaid settings
mermaid_output_format = "raw"
